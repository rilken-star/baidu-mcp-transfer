#!/usr/bin/env python3
"""Local control only: config, program deployment, metadata, encrypted token pipe."""
import argparse
import getpass
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
import uuid

from common import absolute, atomic_json, read_json, validate

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent


def ssh(config):
    host, user = config['host'], config['user']
    if (not re.fullmatch(r'[A-Za-z0-9_.:-]+', host) or host.startswith('-')
            or not re.fullmatch(r'[A-Za-z0-9_.-]+', user) or user.startswith('-')
            or type(config['port']) is not int or not 1 <= config['port'] <= 65535):
        raise ValueError('Invalid SSH destination')
    result = ['ssh', '-T', '-p', str(config['port']), '-o', 'BatchMode=yes',
              '-o', 'StrictHostKeyChecking=yes', '-o', 'ServerAliveInterval=30',
              '-o', 'ServerAliveCountMax=3']
    if config.get('control_path'):
        result += ['-o', 'ControlPath=' + config['control_path']]
    return result + [user + '@' + host]


def remote_command(config, mode):
    code = Path(config['remote_code'])
    python = code / '.venv/bin/python'
    # plan/status/stop need no third-party dependencies, and no credentials.
    executable = 'python3' if mode in ('plan', 'status', 'stop') else str(python)
    return shlex.join([executable, str(code / 'scripts/worker.py'), mode,
                       '--config', str(code / 'config.json')])


def prompt_token():
    if sys.platform == 'darwin':
        script = ('text returned of (display dialog "输入百度 AccessToken：仅本次进程内存和加密 SSH 管道使用，不保存。" '
                  'default answer "" with hidden answer buttons {"取消", "确定"} default button "确定" '
                  'with title "百度 MCP 直传授权")')
        response = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
        if response.returncode:
            raise RuntimeError('Authorization cancelled')
        token = response.stdout.strip()
    else:
        if not sys.stdin.isatty():
            raise RuntimeError('Run start in an interactive terminal for hidden input')
        token = getpass.getpass('Baidu AccessToken (hidden, memory only): ').strip()
    if not token or len(token) > 8192 or '\n' in token or '\r' in token:
        raise ValueError('Invalid credential input')
    return token


def deploy(config):
    buffer = io.BytesIO()
    # Program allowlist: never reads the configured SOURCE or any previous job state.
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for relative in ('scripts/common.py', 'scripts/worker.py', 'scripts/mcp_server.py', 'requirements.txt'):
            archive.add(SKILL / relative, arcname=relative, recursive=False)
        for path in sorted((SKILL / 'vendor').rglob('*')):
            if path.is_file() and not path.is_symlink() and '__pycache__' not in path.parts:
                archive.add(path, arcname=str(path.relative_to(SKILL)), recursive=False)
        data = json.dumps(config, ensure_ascii=False).encode()
        info = tarfile.TarInfo('config.json')
        info.size, info.mode = len(data), 0o600
        archive.addfile(info, io.BytesIO(data))
    code = config['remote_code']
    # Exclusive directory creation prevents replacing another deployment or user files.
    program = ('import sys,tarfile,pathlib; '
               'assert sys.version_info >= (3,12), "Source server requires Python 3.12+"; '
               'p=pathlib.Path(sys.argv[1]); p.mkdir(parents=True,exist_ok=False); '
               'p.chmod(0o700); '
               'tarfile.open(fileobj=sys.stdin.buffer,mode="r|gz").extractall(p,filter="data")')
    command = shlex.join(['python3', '-c', program, code])
    result = subprocess.run(ssh(config) + [command], input=buffer.getvalue(), stderr=subprocess.DEVNULL)
    if result.returncode:
        raise RuntimeError('Deployment failed; verify SSH, Python 3.12, and a fresh remote path')
    print('Programs deployed. No source file bodies transferred.')


def setup(config):
    code = Path(config['remote_code'])
    commands = [shlex.join(['python3', '-m', 'venv', str(code / '.venv')]),
                shlex.join([str(code / '.venv/bin/python'), '-m', 'pip', 'install',
                            '--disable-pip-version-check', '-r', str(code / 'requirements.txt')])]
    # No credentials exist here, but avoid exposing inherited package-index configuration in output.
    result = subprocess.run(ssh(config) + [' && '.join(commands)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode:
        raise RuntimeError('Dependency setup failed; check source-server Python and PyPI access')
    print('Source-server dependencies installed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='Create credential-free job config; no connection or upload')
    for key in ('workspace', 'host', 'user', 'source', 'target', 'remote-base'):
        init.add_argument('--' + key, required=True)
    init.add_argument('--port', type=int, default=22)
    init.add_argument('--control-path')
    init.add_argument('--job-name', default='baidu-' + uuid.uuid4().hex[:12])
    init.add_argument('--exclude-root', action='append', default=[])
    init.add_argument('--exclude-glob', action='append', default=[])
    init.add_argument('--min-bytes', type=int, default=0)
    init.add_argument('--overwrite', action='store_true')
    init.add_argument('--follow-file-links', action='store_true')
    for command in ('deploy', 'setup', 'plan', 'start', 'verify', 'status', 'stop'):
        p = sub.add_parser(command)
        p.add_argument('--config', required=True)
    args = parser.parse_args()
    if args.command == 'init':
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,63}', args.job_name):
            raise ValueError('Invalid job name')
        base = args.remote_base.rstrip('/')
        absolute(base)
        config = validate({'version': 1, 'host': args.host, 'user': args.user, 'port': args.port,
                           'control_path': args.control_path, 'source': args.source, 'target': args.target,
                           'remote_code': base + '/code/' + args.job_name,
                           'remote_state': base + '/codex/' + args.job_name,
                           'min_bytes': args.min_bytes, 'exclude_roots': [s.rstrip('/') for s in args.exclude_root],
                           'exclude_globs': args.exclude_glob, 'overwrite': args.overwrite,
                           'follow_file_links': args.follow_file_links})
        ssh(config)
        path = Path(args.workspace).resolve() / 'project/codex/baidu-mcp-transfer' / args.job_name / 'config.json'
        if path.exists():
            raise ValueError('Job already exists; reuse its config for resume')
        atomic_json(path, config)
        print(path)
        return 0
    config = validate(read_json(args.config))
    if args.command == 'deploy':
        deploy(config)
        return 0
    if args.command == 'setup':
        setup(config)
        return 0
    mode = 'run' if args.command == 'start' else args.command
    # Check noninteractive authentication BEFORE asking for a Baidu token.
    probe = subprocess.run(ssh(config) + ['true'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if probe.returncode:
        raise RuntimeError('SSH unavailable. Establish a trusted key/ControlMaster connection; no token requested.')
    token = prompt_token() if mode in ('run', 'verify') else None
    process = subprocess.Popen(ssh(config) + [remote_command(config, mode)],
                               stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        if token:
            process.stdin.write((json.dumps({'access_token': token}) + '\n').encode())
            process.stdin.flush()
        process.stdin.close()
        token = None
        return process.wait()
    except KeyboardInterrupt:
        # A separate stop CONTROL request terminates remote controller + known SDK child.
        subprocess.run(ssh(config) + [remote_command(config, 'stop')], stderr=subprocess.DEVNULL)
        process.terminate()
        process.wait()
        return 130


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        # Only our own fixed messages may be shown; never HTTP/SSH/SDK exception data.
        if type(error) in (ValueError, RuntimeError):
            print(str(error), file=sys.stderr)
        else:
            print('Control command failed; credential and exception text suppressed', file=sys.stderr)
        raise SystemExit(2)
