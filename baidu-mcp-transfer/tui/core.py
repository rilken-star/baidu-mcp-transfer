"""Credential-free task registry and progress reader for Baidu Netdisk TUI."""
import collections
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import signal
import subprocess
import sys
import time
import uuid

PACK = Path(os.environ.get('BAIDU_TUI_PACK', str(Path(__file__).resolve().parent.parent))).expanduser().resolve()
BASE = Path(__file__).resolve().parent
STATE_HOME = Path(os.environ.get('BAIDU_TUI_STATE', str(Path.home() / '.local/state/baidu-tui'))).expanduser()
JOBS = STATE_HOME / 'jobs'
SECRET = Path(os.environ['BAIDU_TUI_SECRETS_FILE']).expanduser() if os.environ.get('BAIDU_TUI_SECRETS_FILE') else None
CHUNK = 4 * 1024 * 1024
CODE_ROOT = Path(os.environ.get('BAIDU_TUI_DISCOVERY_CODE_ROOT', str(Path.home() / 'project/code'))).expanduser()
STATE_ROOT = Path(os.environ.get('BAIDU_TUI_DISCOVERY_STATE_ROOT', str(Path.home() / 'project/codex'))).expanduser()
PROC_ROOT = Path('/proc')

class SafeError(Exception):
    """Only fixed, credential-free messages belong here."""


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {} if default is None else default


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def identity(pid):
    try:
        text = Path('/proc/%d/stat' % pid).read_text()
        tail = text[text.rfind(')') + 2:].split()
        return None if tail[0] == 'Z' else tail[19]
    except (OSError, IndexError, TypeError, ValueError):
        return None


def alive(owner):
    return (type(owner.get('pid')) is int and owner.get('start_ticks') is not None
            and identity(owner['pid']) == str(owner['start_ticks']))


def clean(text):
    return ''.join(c if c.isprintable() else ' ' for c in str(text))


def remote_path(text):
    p = PurePosixPath(text)
    if (not p.is_absolute() or '..' in p.parts or '\x00' in text or str(p) != text
            or any(ord(c) < 32 for c in text)):
        raise SafeError('网盘路径须为规范的绝对路径，例如 /backup')
    return str(p)


def discover_configs():
    # Read only named transfer configuration files under Baidu-specific folders.
    candidates = []
    try:
        for folder in sorted(CODE_ROOT.glob('baidu*')):
            if folder.is_dir():
                candidates.extend(folder.glob('config*.json'))
                candidates.extend((folder / 'baidu-mcp-transfer').glob('config*.json'))
        for folder in sorted(STATE_ROOT.glob('baidu*')):
            if folder.is_dir():
                candidates.extend(folder.glob('config*.json'))
    except OSError:
        pass
    for path in candidates:
        cfg = read(path)
        state = cfg.get('remote_state')
        kind = cfg.get('kind', cfg.get('direction'))
        if kind not in ('upload', 'download'):
            kind = 'upload' if cfg.get('remote_code') else None
        if (kind is None or not isinstance(state, str) or not Path(state).is_absolute()
                or not isinstance(cfg.get('source'), str) or not isinstance(cfg.get('target'), str)
                or not (Path(state) / 'progress.json').is_file()):
            continue
        yield path, cfg, kind


def discover_mcp_processes(known_children):
    # An uninstrumented MCP process can be recognized, but cannot reveal transfer progress.
    try:
        processes = PROC_ROOT.iterdir()
        for folder in processes:
            if not folder.name.isdigit():
                continue
            try:
                pid = int(folder.name)
                if pid in known_children:
                    continue
                argv = (folder / 'cmdline').read_bytes().split(b'\0')
                paths = [x.lower() for x in argv if x.startswith(b'/') and b'=' not in x]
                if not any(b'baidu' in x and (x.rsplit(b'/', 1)[-1] in (b'mcp_server.py', b'fileupload_tool.py', b'baidu-mcp', b'baidu-mcp-server') or (b'mcp' in x.rsplit(b'/', 1)[-1] and x.endswith((b'.py', b'.js')))) for x in paths):
                    continue
                ticks = identity(pid)
                if ticks is None:
                    continue
                yield {'id': 'mcp-%d-%s' % (pid, ticks), 'kind': 'mcp',
                       'title': '百度 MCP（未接入进度）', 'pid': pid, 'start_ticks': ticks,
                       'created_at': 0, 'managed': False}
            except (OSError, ValueError):
                continue
    except OSError:
        pass


def jobs():
    JOBS.mkdir(mode=0o700, parents=True, exist_ok=True)
    rows = []
    states = set()
    children = set()
    for path in JOBS.glob('*/task.json'):
        row = read(path)
        if row.get('kind') in ('upload', 'download') and isinstance(row.get('config'), str):
            row['_path'] = str(path)
            cfg = read(row['config'])
            if isinstance(cfg.get('remote_state'), str):
                states.add(cfg['remote_state'])
                child = read(Path(cfg['remote_state']) / 'child.json')
                if type(child.get('pid')) is int and alive(child):
                    children.add(child['pid'])
            rows.append(row)
    for path, cfg, kind in discover_configs():
        state = cfg['remote_state']
        child = read(Path(state) / 'child.json')
        if type(child.get('pid')) is int and alive(child):
            children.add(child['pid'])
        if state in states:
            continue
        states.add(state)
        ident = 'auto-' + hashlib.sha256(state.encode()).hexdigest()[:12]
        try:
            created = path.stat().st_mtime
        except OSError:
            created = 0
        rows.append({'id': ident, 'kind': kind, 'config': str(path), 'managed': False,
                     'title': '自动发现' + ('上传 ' if kind == 'upload' else '下载 ')
                              + PurePosixPath(cfg['target']).name,
                     'created_at': created})
    rows.extend(discover_mcp_processes(children))
    return sorted(rows, key=lambda x: (x.get('created_at', 0), x['id']))


def register_existing(config, title='DeepSeek / Maya 备份'):
    folder = JOBS / 'existing-deepseek'
    if not (folder / 'task.json').exists():
        cfg = read(config)
        if not cfg.get('remote_state'):
            raise SafeError('现有上传任务配置不可读')
        atomic(folder / 'task.json', {'id': folder.name, 'kind': 'upload', 'title': title,
               'created_at': time.time(), 'config': str(config), 'managed': False})


def snapshot(task):
    if task['kind'] == 'mcp':
        running = alive(task)
        return {**task, 'phase': 'mcp_online' if running else 'stopped', 'running': running,
                'total': 0, 'verified': 0, 'accounted': 0, 'files': 0, 'total_files': 0,
                'current': '', 'current_size': 0, 'current_bytes': 0, 'stage': 'unknown_telemetry',
                'source': '', 'target': '', 'pending': 0,
                'message': 'MCP 在线，未提供进度；无法判断是否正在传输。',
                'checked_at': 0, 'state': '', 'api_error_code': None}
    cfg = read(task['config'])
    state = Path(cfg.get('remote_state', Path(task['config']).parent / 'state'))
    progress = read(state / 'progress.json')
    owner = read(state / 'owner.json')
    launcher = read(Path(task['config']).parent / 'launcher.json')
    running = alive(owner) or (not owner and alive(launcher))
    phase = progress.get('phase', 'starting' if running else 'queued')
    if phase not in ('complete', 'stopped', 'needs_attention', 'failed') and not running:
        phase = 'interrupted' if progress else ('needs_attention' if launcher else 'queued')
    total = int(progress.get('effective_bytes', 0))
    verified = int(progress.get('verified_bytes', 0))
    verified_files = int(progress.get('verified_files', 0))
    size = int(progress.get('current_size', 0))
    offset = int(progress.get('current_bytes', 0))
    stage = progress.get('stage', 'starting_download' if task['kind'] == 'download' and phase == 'starting' else phase)
    current = progress.get('current', '')
    file_verified = False
    parts = {}
    if task['kind'] == 'upload':
        manifest = read(state / 'manifest.json')
        total = int(manifest.get('effective_bytes', total))
        item = next((x for x in manifest.get('files', []) if x['rel'] == current), {})
        size = int(item.get('size', 0))
        ledger = read(state / 'ledger.json')
        if 'verified_bytes' not in progress:
            completed = [row for row in manifest.get('files', []) if ledger.get(row['rel'], {}).get('stage') == 'verified' and ledger.get(row['rel'], {}).get('proof', {}).get('verified') and ledger.get(row['rel'], {}).get('source') == {'size': row['size'], 'mtime_ns': row.get('mtime_ns')}]
            verified = sum(row['size'] for row in completed)
            verified_files = len(completed)
        file_verified = ledger.get(current, {}).get('stage') == 'verified'
        parts = read(state / 'parts.json')
        child = read(state / 'child.json')
        offset = 0
        stage = 'metadata'
        if (parts.get('owner_pid') == child.get('pid') and parts.get('owner_pid') is not None
                and parts.get('checked_at', 0) >= progress.get('checked_at', 0)):
            stage = parts.get('stage', 'metadata')
            seq = parts.get('part')
            completed = parts.get('completed_bytes')
            if type(completed) is int and 0 <= completed <= size:
                offset = completed
            elif type(seq) is int and seq >= 0:
                offset = min(size, (seq + (stage == 'accepted_part')) * CHUNK)
            if stage == 'finished' and parts.get('sdk_success'):
                offset = size
        if file_verified:
            offset = size
    if phase == 'complete':
        stage = 'complete'
    accounted = verified + (0 if file_verified else offset)
    accounted = min(total, accounted) if total else 0
    diagnostic = parts.get('diagnostic') if isinstance(parts, dict) else None
    diagnostic_message = ('分片数超过 2048，需调整分片大小并核验结果后处理。'
                          if isinstance(diagnostic, dict) and diagnostic.get('kind') == 'too_many_parts' else '')
    return {**task, 'phase': phase, 'running': running, 'total': total,
            'verified': verified, 'accounted': accounted,
            'files': verified_files,
            'total_files': progress.get('effective_files', 0) or (manifest.get('effective_files', 0) if task['kind'] == 'upload' else 0),
            'current': current, 'current_size': size, 'current_bytes': offset,
            'stage': stage, 'source': task.get('display_source', cfg.get('source', '')), 'target': cfg.get('target', ''),
            'pending': len(progress.get('pending', {})),
            'message': diagnostic_message or progress.get('message', '') or ('后台启动失败，请检查凭据配置、路径和依赖。' if not progress and launcher and not running else ''), 'checked_at': progress.get('checked_at', 0),
            'state': str(state), 'api_error_code': parts.get('api_error_code')}


class Rates:
    def __init__(self):
        self.values = {}

    def reset(self, key):
        self.values.pop(key, None)

    def update(self, key, amount, now=None):
        now = time.monotonic() if now is None else now
        q = self.values.setdefault(key, collections.deque())
        if q and (amount < q[-1][1] or now <= q[-1][0] or now - q[-1][0] > 30):
            q.clear()
        q.append((now, amount))
        while len(q) > 2 and q[0][0] < now - 20:
            q.popleft()
        if len(q) < 2 or now - q[0][0] < 1:
            return 0
        return max(0, (amount - q[0][1]) / (now - q[0][0]))


def _job(kind):
    folder = JOBS / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    return folder, {'id': folder.name, 'kind': kind, 'created_at': time.time(), 'managed': True}


def python_executable():
    venv = PACK / '.venv/bin/python'
    return str(venv) if venv.is_file() else sys.executable


def _spawn(task, folder, mode='run'):
    if task['kind'] == 'upload':
        command = [python_executable(), str(PACK / 'tui/run_job.py'), mode, task['config']]
    else:
        command = [sys.executable, str(BASE / 'download.py'), task['config']]
    # Workers write their own sanitized state; never retain raw SDK / HTTP errors.
    with open(os.devnull, 'r+b') as null:
        p = subprocess.Popen(command, stdin=null, stdout=null, stderr=null,
                             start_new_session=True, close_fds=True)
    atomic(folder / 'launcher.json', {'pid': p.pid, 'start_ticks': identity(p.pid), 'started_at': time.time()})


def _avoid_active_collision(target, name=None):
    for task in jobs():
        if task['kind'] != 'upload':
            continue
        snap = snapshot(task)
        if not snap['running']:
            continue
        cfg = read(task['config'])
        manifest = read(Path(cfg['remote_state']) / 'manifest.json')
        wanted = target.rstrip('/') + '/' + name if name else target
        if manifest.get('files'):
            for item in manifest['files']:
                existing = cfg['target'].rstrip('/') + '/' + item['rel']
                if existing == wanted or (name is None and existing.startswith(wanted.rstrip('/') + '/')):
                    raise SafeError('目标与正在运行的上传任务重叠，请换一个网盘目录')
        elif target.startswith(cfg['target'].rstrip('/') + '/') or cfg['target'].startswith(target.rstrip('/') + '/') or target == cfg['target']:
            raise SafeError('目标可能与启动中的任务重叠，请换一个网盘目录')


def start_upload(source, target):
    original = Path(source).expanduser().absolute()
    if not original.exists() or not (original.is_file() or original.is_dir()):
        raise SafeError('本地源文件或目录不存在')
    target = remote_path(target)
    if target == '/':
        raise SafeError('请选择一个具体网盘目录')
    if original.name.startswith('.env') or original.name.startswith('secrets.env') or original.suffix in ('.key', '.pem'):
        raise SafeError('凭据文件不能作为上传源')
    _avoid_active_collision(target, original.name if original.is_file() else None)
    folder, task = _job('upload')
    if original.is_file():
        source_root = folder / 'source'
        source_root.mkdir(mode=0o700)
        (source_root / original.name).symlink_to(original)
    else:
        source_root = original.resolve()
        if folder.is_relative_to(source_root):
            raise SafeError('源目录包含任务状态目录，请选择更具体的目录')
    cfg = {'version': 1, 'host': 'localhost', 'user': os.environ.get('USER', ''),
           'port': 22, 'control_path': '/tmp/baidu-tui-control',
           'remote_code': str(PACK)}
    cfg.update(source=str(source_root), target=target, remote_state=str(folder / 'state'),
               exclude_roots=[], exclude_globs=['*.env', '.env*', '*/.env*', '*secrets.env*', '*.pem', '*.key'],
               min_bytes=0, overwrite=False, follow_file_links=True)
    atomic(folder / 'config.json', cfg)
    task.update(config=str(folder / 'config.json'), title='上传 ' + original.name, display_source=str(original))
    atomic(folder / 'task.json', task)
    _spawn(task, folder)
    return task


def start_download(source, target):
    source = remote_path(source)
    target_path = Path(target).expanduser().absolute()
    if target_path.exists() and not target_path.is_dir():
        raise SafeError('下载目标必须是一个本地目录')
    for task in jobs():
        if task['kind'] == 'download' and snapshot(task)['running']:
            cfg = read(task['config'])
            if cfg.get('target') == str(target_path):
                raise SafeError('该本地目录已有下载任务，请选择另一目录')
    folder, task = _job('download')
    cfg = {'source': source, 'target': str(target_path), 'remote_state': str(folder / 'state')}
    atomic(folder / 'config.json', cfg)
    task.update(config=str(folder / 'config.json'), title='下载 ' + (PurePosixPath(source).name or '/'))
    atomic(folder / 'task.json', task)
    _spawn(task, folder)
    return task


def resume_download(task):
    if task['kind'] != 'download':
        raise SafeError('上传需先核验当前文件，不自动重传结果未知的文件')
    if snapshot(task)['running']:
        raise SafeError('任务正在运行')
    _spawn(task, Path(task['config']).parent)


def stop_task(task):
    if task['kind'] == 'mcp':
        raise SafeError('此 MCP 未接入传输状态，不能从这里停止。')
    snap = snapshot(task)
    if not snap['running']:
        raise SafeError('任务当前没有运行')
    if task['kind'] == 'upload':
        cfg = read(task['config'])
        command = [python_executable(), str(PACK / 'scripts/worker.py'), 'stop', '--config', task['config']]
        r = subprocess.run(command, capture_output=True, timeout=45)
        if r.returncode:
            raise SafeError('停止未确认，请刷新检查任务状态')
    else:
        owner = read(Path(snap['state']) / 'owner.json')
        if alive(owner):
            os.kill(owner['pid'], signal.SIGTERM)
        else:
            raise SafeError('后台进程尚未就绪，请稍后刷新')
