#!/usr/bin/env python3
"""Source-server controller. Bodies never enter SSH or the local Codex host."""
import argparse
import fcntl
import fnmatch
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import select
import signal
import stat
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from common import atomic_json, inside, read_json, validate

logging.disable(logging.CRITICAL)
STOP = False


class Attention(Exception):
    pass


class Stopped(Attention):
    pass


def signal_stop(signum, frame):
    global STOP
    STOP = True


def check_stop():
    if STOP:
        raise Stopped('Stopped by user')


def excluded(rel, size, config):
    return (size < config['min_bytes']
            or any(inside(rel, root) for root in config['exclude_roots'])
            or any(fnmatch.fnmatchcase(rel.lower(), pattern.lower())
                   for pattern in config['exclude_globs']))


def scan(config):
    root = Path(config['source'])
    if not root.is_dir() or root.is_symlink():
        raise Attention('Source root must be a real directory')
    rows, issues, excluded_count = [], [], 0
    for parent, dirs, files in os.walk(root, followlinks=False):
        check_stop()
        kept = []
        for name in sorted(dirs):
            path = Path(parent) / name
            rel = path.relative_to(root).as_posix()
            if any(inside(rel, r) for r in config['exclude_roots']):
                continue
            if path.is_symlink():
                issues.append({'rel': rel, 'reason': 'directory_symlink_not_followed'})
            else:
                kept.append(name)
        dirs[:] = kept
        for name in sorted(files):
            path = Path(parent) / name
            rel = path.relative_to(root).as_posix()
            if any(inside(rel, r) for r in config['exclude_roots']):
                continue
            try:
                if path.is_symlink() and not config['follow_file_links']:
                    issues.append({'rel': rel, 'reason': 'file_symlink_not_authorized'})
                    continue
                metadata = path.stat()
                if not stat.S_ISREG(metadata.st_mode):
                    issues.append({'rel': rel, 'reason': 'not_regular_file'})
                    continue
                if excluded(rel, metadata.st_size, config):
                    excluded_count += 1
                    continue
                rows.append({'rel': rel, 'size': metadata.st_size, 'mtime_ns': metadata.st_mtime_ns})
            except OSError:
                issues.append({'rel': rel, 'reason': 'source_stat_failed'})
    rows.sort(key=lambda x: (-x['size'], x['rel']))
    return {'checked_at': time.time(), 'files': rows, 'issues': issues,
            'excluded_files_by_size_or_glob': excluded_count,
            'effective_files': len(rows), 'effective_bytes': sum(x['size'] for x in rows)}


def strict_match(row, path, size):
    return (row.get('path') == path and row.get('server_filename') == PurePosixPath(path).name
            and type(row.get('isdir')) is int and row['isdir'] == 0
            and type(row.get('size')) is int and row['size'] == size)


class Baidu:
    """Official OpenAPI metadata only. Never prints HTTP exception strings."""
    def __init__(self, token):
        self.token = token

    def request(self, method, values, post=False):
        query = urllib.parse.urlencode({'method': method, 'access_token': self.token})
        url = 'https://pan.baidu.com/rest/2.0/xpan/file?' + query
        if post:
            request = urllib.request.Request(url, data=urllib.parse.urlencode(values).encode())
        else:
            request = urllib.request.Request(url + '&' + urllib.parse.urlencode(values))
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                value = json.load(response)
        except Exception:
            raise Attention('Baidu metadata request failed; no credential logged') from None
        if not isinstance(value, dict) or type(value.get('errno')) is not int or value['errno'] != 0:
            raise Attention('Baidu rejected metadata request; check authorization and destination')
        return value

    def listing(self, directory):
        rows = []
        for start in range(0, 1000000, 1000):
            check_stop()
            value = self.request('list', {'dir': directory, 'start': start, 'limit': 1000})
            page = value.get('list')
            if not isinstance(page, list) or any(not isinstance(x, dict) for x in page):
                raise Attention('Incomplete destination enumeration')
            rows.extend(page)
            if len(page) < 1000:
                return rows
        raise Attention('Destination enumeration limit exceeded')

    def ensure_parent(self, directory, config, attempts):
        target = PurePosixPath(config['target'])
        parent = target.parent
        # Destination's parent must already exist. Never create above the authorized root.
        for name in PurePosixPath(directory).relative_to(parent).parts:
            path = str(parent / name)
            found = [r for r in self.listing(str(parent)) if r.get('path') == path]
            if found:
                if len(found) != 1 or found[0].get('server_filename') != name or type(found[0].get('isdir')) is not int or found[0]['isdir'] != 1:
                    raise Attention('Destination directory conflict')
                attempts.pop(path, None)
            else:
                if path in attempts:
                    raise Attention('Previous directory creation outcome unknown; not resubmitting')
                attempts[path] = {'stage': 'attempting', 'checked_at': time.time()}
                atomic_json(Path(config['remote_state']) / 'directories.json', attempts)
                check_stop()
                self.request('create', {'path': path, 'isdir': 1, 'size': 0, 'rtype': 0}, post=True)
                found = [r for r in self.listing(str(parent)) if r.get('path') == path]
                if len(found) != 1 or found[0].get('server_filename') != name or type(found[0].get('isdir')) is not int or found[0]['isdir'] != 1:
                    raise Attention('Directory creation outcome unknown')
                attempts.pop(path, None)
            atomic_json(Path(config['remote_state']) / 'directories.json', attempts)
            parent = parent / name

    def proof(self, path, size):
        rows = self.listing(str(PurePosixPath(path).parent))
        matches = [r for r in rows if r.get('path') == path]
        ok = len(matches) == 1 and strict_match(matches[0], path, size)
        return {'checked_at': time.time(), 'api_errno': 0, 'full_enumeration': True,
                'path': path, 'expected_bytes': size, 'verified': ok,
                'matches': [{k: r.get(k) for k in ('path', 'server_filename', 'isdir', 'size', 'fs_id')}
                            for r in matches]}


class Mcp:
    """Small JSON-RPC stdio client. No tool arguments/responses are logged."""
    def __init__(self, config, token, lock_fd=None):
        env = os.environ.copy()
        env['BAIDU_NETDISK_ACCESS_TOKEN'] = token
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name('mcp_server.py')), config['_config_path']],
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            pass_fds=() if lock_fd is None else (lock_fd,))
        env.pop('BAIDU_NETDISK_ACCESS_TOKEN', None)
        self.sequence = 0
        self.buffer = b''
        try:
            self.call('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                      'clientInfo': {'name': 'baidu-mcp-transfer', 'version': '1.0.0'}}, 60)
            self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        except BaseException:
            self.close()
            raise

    def send(self, value):
        self.process.stdin.write((json.dumps(value) + '\n').encode())
        self.process.stdin.flush()

    def call(self, method, params, timeout=86400):
        self.sequence += 1
        identity = self.sequence
        self.send({'jsonrpc': '2.0', 'id': identity, 'method': method, 'params': params})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            check_stop()
            while b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeError):
                    raise Attention('Invalid MCP protocol response') from None
                if value.get('id') == identity:
                    if 'error' in value:
                        raise Attention('MCP request failed; verify destination before any retry')
                    return value.get('result', {})
            if select.select([self.process.stdout], [], [], 0.25)[0]:
                data = os.read(self.process.stdout.fileno(), 65536)
                if not data:
                    raise Attention('MCP connection closed; upload outcome may be unknown')
                self.buffer += data
                if len(self.buffer) > 16777216:
                    raise Attention('MCP response size exceeded')
        raise Attention('MCP timeout; upload outcome may be unknown')

    def upload(self, path, parent):
        self.call('tools/call', {'name': 'upload_file',
                  'arguments': {'local_file_path': path, 'remote_path': parent}})
        # Tool success is deliberately ignored. Only independent target proof counts.

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()


def signature(item):
    return {'size': item['size'], 'mtime_ns': item['mtime_ns']}


def source_matches(config, item):
    path = Path(config['source']) / item['rel']
    try:
        metadata = path.stat()
        return (not (path.is_symlink() and not config['follow_file_links'])
                and stat.S_ISREG(metadata.st_mode) and metadata.st_size == item['size']
                and metadata.st_mtime_ns == item['mtime_ns'])
    except OSError:
        return False


def transfer_one(config, item, ledger, api, mcp, directories, verify_only=False):
    rel = item['rel']
    previous = ledger.get(rel, {})
    destination = str(PurePosixPath(config['target']) / rel)
    state_path = Path(config['remote_state']) / 'ledger.json'
    if previous and previous.get('source') != signature(item):
        return 'source_changed'
    if not source_matches(config, item):
        return 'source_changed'
    if not verify_only:
        api.ensure_parent(str(PurePosixPath(destination).parent), config, directories)
    proof = api.proof(destination, item['size'])
    if proof['verified']:
        ledger[rel] = {'stage': 'verified', 'source': signature(item), 'proof': proof}
        atomic_json(state_path, ledger)
        return 'verified'
    if previous.get('stage') in ('attempting', 'unknown'):
        return 'unknown'
    if verify_only:
        return 'not_verified'
    if proof['matches'] and not config['overwrite']:
        return 'conflict'
    if any(type(r.get('isdir')) is not int or r['isdir'] != 0 for r in proof['matches']):
        return 'conflict'
    check_stop()
    if not source_matches(config, item):
        return 'source_changed'
    ledger[rel] = {'stage': 'attempting', 'source': signature(item), 'started_at': time.time()}
    atomic_json(state_path, ledger)  # durable BEFORE mutation; interrupted attempt cannot auto-repeat
    try:
        mcp.upload(str(Path(config['source']) / rel), str(PurePosixPath(destination).parent))
        proof = api.proof(destination, item['size'])
        valid = proof['verified'] and source_matches(config, item)
        ledger[rel].update(stage='verified' if valid else 'unknown', proof=proof)
    except BaseException:
        ledger[rel]['stage'] = 'unknown'
        atomic_json(state_path, ledger)
        raise
    atomic_json(state_path, ledger)
    return ledger[rel]['stage']


def process_identity(pid):
    try:
        raw = Path('/proc/%d/stat' % pid).read_text()
        tail = raw[raw.rfind(')') + 2:].split()
        return None if tail[0] == 'Z' else tail[19]
    except (OSError, IndexError):
        return None


def alive(config):
    owner = read_json(Path(config['remote_state']) / 'owner.json', {})
    pid = owner.get('pid')
    if type(pid) is not int or owner.get('start_ticks') is None:
        return False, owner
    return process_identity(pid) == owner['start_ticks'], owner


def child_alive(config):
    child = read_json(Path(config['remote_state']) / 'child.json', {})
    pid = child.get('pid')
    return (type(pid) is int and child.get('start_ticks') is not None
            and process_identity(pid) == child['start_ticks'])


def stop(config):
    active, owner = alive(config)
    if active:
        os.kill(owner['pid'], signal.SIGTERM)
        for unused in range(60):
            if not alive(config)[0]:
                break
            time.sleep(0.25)
    active = alive(config)[0]
    # Also cover an orphaned SDK child after a previously crashed controller.
    child = read_json(Path(config['remote_state']) / 'child.json', {})
    pid = child.get('pid')
    if type(pid) is int and child.get('start_ticks') is not None and process_identity(pid) == child['start_ticks']:
        os.kill(pid, signal.SIGKILL)
    if active:
        os.kill(owner['pid'], signal.SIGKILL)
        time.sleep(0.25)
    for unused in range(20):
        if not child_alive(config):
            break
        time.sleep(0.25)
    stopped = not alive(config)[0] and not child_alive(config)
    if stopped:
        progress_path = Path(config['remote_state']) / 'progress.json'
        progress = read_json(progress_path, {})
        progress.update(phase='stopped', checked_at=time.time(), stop_confirmed=True)
        atomic_json(progress_path, progress)
    return {'stopped': stopped, 'records_preserved': True}


def run(config, verify_only=False):
    state = Path(config['remote_state'])
    state.mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    pair = hashlib.sha256((config['source'] + '\0' + config['target']).encode()).hexdigest()
    lock_dir = Path.home() / '.local/state/baidu-mcp-transfer'
    lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (lock_dir / (pair + '.lock')).open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Attention('A controller already owns this source/destination pair') from None
        signal.signal(signal.SIGTERM, signal_stop)
        signal.signal(signal.SIGINT, signal_stop)
        signal.signal(signal.SIGHUP, signal_stop)
        atomic_json(state / 'owner.json', {'pid': os.getpid(), 'start_ticks': process_identity(os.getpid())})
        progress = {'phase': 'scanning', 'checked_at': time.time(), 'pending': {}}
        mcp = None
        try:
            header = sys.stdin.buffer.readline(16384)
            if not header.endswith(b'\n'):
                raise Attention('Credential header missing')
            value = json.loads(header)
            token = value.get('access_token')
            del value, header
            if not isinstance(token, str) or not token or len(token) > 8192:
                raise Attention('Credential header invalid')
            manifest = scan(config)
            atomic_json(state / 'manifest.json', manifest)
            ledger = read_json(state / 'ledger.json', {})
            directories = read_json(state / 'directories.json', {})
            api = Baidu(token)
            if not verify_only:
                # SDK child retains the same flock if the controller crashes.
                # No second controller can start while that child still uploads.
                mcp = Mcp(config, token, handle.fileno())
                atomic_json(state / 'child.json', {'pid': mcp.process.pid,
                            'start_ticks': process_identity(mcp.process.pid)})
            for item in manifest['files']:
                check_stop()
                progress.update(phase='verifying' if verify_only else 'processing',
                                current=item['rel'], checked_at=time.time())
                atomic_json(state / 'progress.json', progress)
                outcome = transfer_one(config, item, ledger, api, mcp, directories, verify_only)
                if outcome != 'verified':
                    progress['pending'][item['rel']] = outcome
                    # Keep historical proof but do not count a now-invalid target as complete.
                    if item['rel'] in ledger:
                        ledger[item['rel']]['stage'] = outcome
                        atomic_json(state / 'ledger.json', ledger)
                verified = [i for i in manifest['files'] if ledger.get(i['rel'], {}).get('stage') == 'verified'
                            and ledger[i['rel']].get('source') == signature(i)]
                progress.update(verified_files=len(verified), verified_bytes=sum(i['size'] for i in verified),
                                effective_files=manifest['effective_files'], effective_bytes=manifest['effective_bytes'])
                atomic_json(state / 'progress.json', progress)
            progress.update(phase='needs_attention' if progress['pending'] or manifest['issues'] else 'complete',
                            source_issues=manifest['issues'])
        except Stopped:
            progress['phase'] = 'stopped'
        except Exception:
            progress.update(phase='needs_attention', message='Stopped on error; inspect metadata and verify before retry')
        finally:
            if mcp:
                mcp.close()
            progress['checked_at'] = time.time()
            atomic_json(state / 'progress.json', progress)
            # Tokens existed only in this process and its SDK child; process exit releases them.
        print(json.dumps(progress, ensure_ascii=False), flush=True)
        return 0 if progress['phase'] in ('complete', 'stopped') else 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['plan', 'run', 'verify', 'status', 'stop'])
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = validate(read_json(args.config))
    config['_config_path'] = str(Path(args.config).resolve())
    if args.mode == 'status':
        output = {'running': alive(config)[0], 'sdk_running': child_alive(config),
                  'progress': read_json(Path(config['remote_state']) / 'progress.json', {}),
                  'parts': read_json(Path(config['remote_state']) / 'parts.json', {})}
    elif args.mode == 'stop':
        output = stop(config)
    elif args.mode == 'plan':
        output = scan(config)
        atomic_json(Path(config['remote_state']) / 'manifest.json', output)
        output = {k: v for k, v in output.items() if k != 'files'}
    else:
        return run(config, args.mode == 'verify')
    print(json.dumps(output, ensure_ascii=False))
    return 2 if args.mode == 'stop' and not output['stopped'] else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('Transfer command failed; credential and exception text suppressed', file=sys.stderr)
        raise SystemExit(2)
