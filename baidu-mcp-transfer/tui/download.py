"""Detached, resumable Baidu downloader. URLs and credentials stay in memory."""
import fcntl
import errno
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from core import SafeError, SECRET, atomic, read, identity, remote_path

STOP = False

def stop(signum, frame):
    global STOP
    STOP = True


def check():
    if STOP:
        raise InterruptedError()


def token():
    value = os.environ.pop('BAIDU_NETDISK_ACCESS_TOKEN', '') or os.environ.pop('BAIDU_ACCESS_TOKEN', '')
    os.environ.pop('BAIDU_NETDISK_ACCESS_TOKEN', None)
    os.environ.pop('BAIDU_ACCESS_TOKEN', None)
    if value:
        return value
    if SECRET is None:
        raise SafeError('缺少百度授权：请在终端隐藏输入令牌。')
    try:
        if SECRET.is_symlink() or SECRET.stat().st_mode & 0o077 or SECRET.parent.stat().st_mode & 0o077:
            raise SafeError('凭据目录需为700、文件需为600，且不能是软链接')
        for line in SECRET.read_text().splitlines():
            m = re.match(r'\s*(?:export\s+)?(BAIDU_ACCESS_TOKEN|BAIDU_NETDISK_ACCESS_TOKEN)\s*=\s*(.*)', line)
            if m:
                value = m[2].strip().strip("\"\x27")
                if value:
                    return value
    except OSError:
        raise SafeError('指定的凭据文件不可读。') from None
    raise SafeError('指定的凭据文件没有百度授权。')


class Api:
    def __init__(self):
        self.token = token()

    def request(self, endpoint, method, **values):
        check()
        values.update(method=method, access_token=self.token)
        url = 'https://pan.baidu.com/rest/2.0/xpan/' + endpoint + '?' + urllib.parse.urlencode(values)
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                value = json.load(r)
        except Exception:
            raise SafeError('百度接口请求失败，请检查网络和授权') from None
        code = value.get('errno', value.get('error_code'))
        if type(code) is not int or code != 0:
            raise SafeError('百度接口拒绝请求，错误码 %s' % (code if type(code) is int else '?'))
        return value

    def listing(self, path):
        output = []
        for start in range(0, 1000000, 1000):
            rows = self.request('file', 'list', dir=path, start=start, limit=1000).get('list')
            if not isinstance(rows, list):
                raise SafeError('网盘目录列表不完整')
            output.extend(rows)
            if len(rows) < 1000:
                return output
        raise SafeError('目录过大，请分目录下载')

    def dlink(self, fsid):
        result = self.request('multimedia', 'filemetas', fsids=json.dumps([fsid]), dlink=1)
        rows = result.get('list', [])
        if len(rows) != 1 or rows[0].get('fs_id') != fsid or not rows[0].get('dlink'):
            raise SafeError('未获得文件下载链接，请检查下载权限')
        url = trusted_url(rows[0]['dlink'])
        u = urllib.parse.urlsplit(url)
        values = [(k,v) for k,v in urllib.parse.parse_qsl(u.query, keep_blank_values=True) if k != 'access_token']
        values.append(('access_token', self.token))
        return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, urllib.parse.urlencode(values), ''))


def trusted_url(url):
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or '').lower()
    allowed = ('baidu.com', 'baidupcs.com', 'baidubce.com', 'bdstatic.com')
    if (u.scheme not in ('http','https') or not any(host == d or host.endswith('.'+d) for d in allowed)
            or u.username or u.password or u.port not in (None,80,443)):
        raise SafeError('下载链接不属于允许的百度服务域名')
    return urllib.parse.urlunsplit(('https', host, u.path, u.query, ''))


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, trusted_url(newurl))


def enumerate_files(api, source):
    source = remote_path(source)
    if source == '/':
        row = {'path':'/', 'isdir':1}
    else:
        rows = [r for r in api.listing(str(PurePosixPath(source).parent)) if r.get('path') == source]
        if len(rows) != 1:
            raise SafeError('找不到指定网盘文件或目录')
        row = rows[0]
    root = PurePosixPath(source) if row.get('isdir') == 1 else PurePosixPath(source).parent
    pending = [row]
    files = []
    visited = set()
    while pending:
        check()
        item = pending.pop()
        path = remote_path(item['path'])
        if path in visited:
            raise SafeError('目录列表存在重复路径')
        visited.add(path)
        p = PurePosixPath(path)
        if p != root and root not in p.parents:
            raise SafeError('目录返回了范围之外的文件')
        if item.get('isdir') == 1:
            pending.extend(api.listing(path))
        elif item.get('isdir') == 0:
            rel = p.relative_to(root).as_posix()
            if rel == '.' or '..' in PurePosixPath(rel).parts:
                raise SafeError('下载文件名无效')
            files.append({'rel':rel, 'path':path, 'size':int(item['size']),
                          'fs_id':int(item['fs_id']), 'mtime':item.get('server_mtime')})
        else:
            raise SafeError('网盘返回了未知文件类型')
    return sorted(files, key=lambda r:r['rel'])


def safe_child(root, rel):
    p = root / rel
    if not p.resolve().is_relative_to(root.resolve()) or p.is_symlink():
        raise SafeError('下载路径包含指向目标目录外部的链接')
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def transfer(api, row, final, partial, progress, save):
    offset = partial.stat().st_size if partial.exists() else 0
    if partial.is_symlink() or offset > row['size']:
        raise SafeError('临时文件不安全或大小不匹配')
    opener = urllib.request.build_opener(Redirect())
    for attempt in range(3):
        offset = partial.stat().st_size if partial.exists() else 0
        check()
        if offset == row['size']:
            break
        headers = {'User-Agent':'pan.baidu.com', 'Accept-Encoding':'identity'}
        if offset:
            headers['Range'] = 'bytes=%d-' % offset
        try:
            request = urllib.request.Request(api.dlink(row['fs_id']), headers=headers)
            with opener.open(request, timeout=30) as response:
                if offset:
                    content_range = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range',''))
                    if response.status != 206 or not content_range or int(content_range[1]) != offset or int(content_range[3]) != row['size'] or int(content_range[2]) != row['size']-1:
                        raise SafeError('服务器未正确响应断点请求，已保留临时文件')
                if response.headers.get('Content-Length') is not None and int(response.headers['Content-Length']) != row['size'] - offset:
                    raise SafeError('下载响应大小不符，未覆盖已有文件')
                if 'json' in response.headers.get('Content-Type','').lower():
                    raise SafeError('下载服务返回了错误响应，请检查授权')
                fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd,'ab') as out:
                    while True:
                        check()
                        block = response.read(1024*1024)
                        if not block:
                            break
                        if offset + len(block) > row['size']:
                            raise SafeError('下载响应超出预期大小')
                        out.write(block)
                        offset += len(block)
                        progress.update(current_bytes=offset, checked_at=time.time(), stage='downloading')
                        save()
                    out.flush()
                    os.fsync(out.fileno())
            if offset == row['size']:
                break
        except (SafeError, InterruptedError):
            raise
        except Exception as error:
            if isinstance(error, OSError) and error.errno in (errno.ENOSPC,errno.EACCES,errno.EROFS):
                raise SafeError('本地磁盘空间不足或没有写入权限，临时文件已保留') from None
            if attempt == 2:
                raise SafeError('下载连接失败，临时数据已保留；可按 c 继续') from None
        check()
        time.sleep(1)
    if offset != row['size']:
        raise SafeError('下载尚未完整，临时文件已保留')
    if not partial.exists():
        partial.touch(mode=0o600, exist_ok=False)
    # Atomic publication without overwriting a pre-existing destination.
    try:
        os.link(partial, final, follow_symlinks=False)
    except FileExistsError:
        raise SafeError('目标文件已存在，未覆盖') from None
    partial.unlink()


def run(config_path):
    resource.setrlimit(resource.RLIMIT_CORE, (0,0))
    cfg = read(config_path)
    state = Path(cfg['remote_state']); state.mkdir(mode=0o700,parents=True,exist_ok=True)
    lock = open(state/'download.lock','a')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        return 2
    for sig in (signal.SIGTERM,signal.SIGINT):
        signal.signal(sig,stop)
    atomic(state/'owner.json',{'pid':os.getpid(),'start_ticks':identity(os.getpid())})
    progress = {'phase':'scanning','stage':'listing','verified_files':0,'verified_bytes':0,
                'effective_files':0,'effective_bytes':0,'current_bytes':0,'checked_at':time.time()}
    last = [0.0]
    def save(force=False):
        if force or time.monotonic()-last[0] >= .25:
            atomic(state/'progress.json',progress);last[0]=time.monotonic()
    try:
        save(True)
        api=Api()
        rows=enumerate_files(api,cfg['source'])
        prior=read(state/'manifest.json').get('files',[])
        if prior and prior != rows:
            raise SafeError('网盘源文件已变化，未复用旧临时文件，请创建新任务')
        atomic(state/'manifest.json',{'files':rows})
        progress.update(effective_files=len(rows),effective_bytes=sum(r['size'] for r in rows))
        target=Path(cfg['target']).resolve();target.mkdir(parents=True,exist_ok=True)
        partials_root=target/'.baidu-partials'
        if partials_root.is_symlink():
            raise SafeError('临时下载目录不能是软链接')
        tmp=partials_root/Path(config_path).parent.name
        if tmp.is_symlink() or not tmp.resolve().is_relative_to(target):
            raise SafeError('临时下载目录不在目标范围内')
        tmp.mkdir(mode=0o700,parents=True,exist_ok=True)
        ledger=read(state/'ledger.json')
        for row in rows:
            check()
            final=safe_child(target,row['rel']);partial=safe_child(tmp,row['rel']+'.part')
            known=ledger.get(row['rel'],{})
            if known and known.get('source') != row:
                raise SafeError('源签名发生变化，未复用临时文件')
            if final.exists():
                if known.get('stage')=='verified' and not final.is_symlink() and final.stat().st_size==row['size']:
                    progress['verified_files']+=1;progress['verified_bytes']+=row['size'];continue
                # Recover a crash between atomic publication and durable completion.
                if known.get('stage')=='downloading' and partial.exists() and final.stat().st_ino==partial.stat().st_ino and final.stat().st_dev==partial.stat().st_dev and final.stat().st_size==row['size']:
                    partial.unlink()
                else:
                    raise SafeError('目标文件已存在且不属于已核验记录，未覆盖')
            elif partial.exists() and not known:
                raise SafeError('临时文件来源未确认，未自动覆盖或续传')
            progress.update(phase='processing',stage='downloading',current=row['rel'],current_size=row['size'],current_bytes=partial.stat().st_size if partial.exists() else 0,checked_at=time.time())
            save(True)
            if not final.exists():
                ledger[row['rel']]={'source':row,'stage':'downloading'};atomic(state/'ledger.json',ledger)
                transfer(api,row,final,partial,progress,save)
            ledger[row['rel']]={'source':row,'stage':'verified'};atomic(state/'ledger.json',ledger)
            progress['verified_files']+=1;progress['verified_bytes']+=row['size']
            progress.update(current_bytes=0,current_size=0,checked_at=time.time());save(True)
        progress.update(phase='complete',stage='complete',message='已按元信息和大小核验完成')
    except InterruptedError:
        progress.update(phase='stopped',message='下载已停止，临时数据保留，可按 c 继续')
    except SafeError as error:
        progress.update(phase='needs_attention',message=str(error))
    except Exception:
        progress.update(phase='needs_attention',message='下载失败；异常详情已隐藏，可检查权限和网络后继续')
    finally:
        progress['checked_at']=time.time();save(True)
        lock.close()
    return 0 if progress['phase']=='complete' else 2

if __name__=='__main__':
    try:
        raise SystemExit(run(sys.argv[1]))
    except Exception:
        raise SystemExit(2)
