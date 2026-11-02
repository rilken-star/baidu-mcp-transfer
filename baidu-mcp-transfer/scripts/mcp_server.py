"""Guarded official stdio MCP, running ONLY on the source server."""
import asyncio
import fnmatch
import importlib.util
import logging
import os
from pathlib import Path
import sys
import threading
import time

from common import atomic_json, inside, read_json, validate

logging.disable(logging.CRITICAL)
config = validate(read_json(sys.argv[1]))
vendor = Path(__file__).resolve().parents[1] / 'vendor'
sys.path.insert(0, str(vendor))
spec = importlib.util.spec_from_file_location('official_baidu_mcp', vendor / 'fileupload_tool.py')
official = importlib.util.module_from_spec(spec)
spec.loader.exec_module(official)
from baidu_auth import install_sdk_refresh
install_sdk_refresh(official)
from safe_retry import install_sdk_retry
install_sdk_retry(official, event=lambda *a,**kw:event(*a,**kw))
# The official module keeps its token in process memory. Remove inherited env.
os.environ.pop('BAIDU_NETDISK_ACCESS_TOKEN', None)
original_upload = official.upload_file
original_part = official.fileupload_api.FileuploadApi.pcssuperfile2
lock = threading.Lock()
progress_path = Path(config['remote_state']) / 'parts.json'


def event(stage, **values):
    atomic_json(progress_path, {'checked_at': time.time(), 'owner_pid': os.getpid(),
                               'stage': stage, **values})


def part(self, *args, **kwargs):
    sequence = int(kwargs.get('partseq', 0))
    event('sending_part', part=sequence)
    response = original_part(self, *args, **kwargs)
    event('accepted_part', part=sequence, acknowledged=bool(response.get('md5')))
    return response


# The original serial uploader remains the default for the packaged skill.
# The TUI controller opts into bounded parallel uploads and durable checkpoints.
parallel_enabled = os.environ.pop('BAIDU_TUI_PARALLEL', '') == '1'
if parallel_enabled:
    try:
        chunk_mib = int(os.environ.pop('BAIDU_TUI_CHUNK_MIB', '4'))
        workers = int(os.environ.pop('BAIDU_TUI_WORKERS', '4'))
        if chunk_mib not in (4, 8, 16, 32, 64) or not 1 <= workers <= 8:
            raise ValueError('Invalid transfer settings')
    except ValueError:
        raise SystemExit('Invalid TUI transfer settings')
    official.CHUNK_SIZE = chunk_mib * 1024 * 1024
    from parallel_upload import build
    official.upload_large_file = build(official, event, workers=workers,
        checkpoint_dir=str(Path(config['remote_state']) / 'upload-checkpoints'))
else:
    official.fileupload_api.FileuploadApi.pcssuperfile2 = part


def guarded(local_file_path, remote_path):
    # lexical scope permits explicitly authorized file symlinks, not directory traversal.
    if (not isinstance(local_file_path, str) or not isinstance(remote_path, str)
            or not inside(local_file_path, config['source'])
            or not inside(remote_path, config['target'])
            or '..' in Path(local_file_path).parts or '..' in Path(remote_path).parts
            or str(Path(local_file_path)) != local_file_path
            or str(Path(remote_path)) != remote_path):
        return {'status': 'error', 'message': 'Outside configured transfer scope'}
    if not lock.acquire(blocking=False):
        return {'status': 'error', 'message': 'Another upload is active'}
    try:
        path = Path(local_file_path)
        rel = path.relative_to(config['source']).as_posix()
        size = path.stat().st_size
        expected_parent = str(Path(config['target']) / Path(rel).parent)
        if (remote_path != expected_parent or size < config['min_bytes']
                or any(inside(rel, r) for r in config['exclude_roots'])
                or any(fnmatch.fnmatchcase(rel.lower(), g.lower()) for g in config['exclude_globs'])
                or (path.is_symlink() and not config['follow_file_links'])):
            return {'status': 'error', 'message': 'File excluded by configured scope'}
        event('starting', source_bytes=size)
        if parallel_enabled and (size + official.CHUNK_SIZE - 1) // official.CHUNK_SIZE > 2048:
            event('failed', diagnostic={'kind': 'too_many_parts', 'chunk_mib': official.CHUNK_SIZE // (1024*1024)})
            return {'status': 'error', 'message': 'File requires more than 2048 parts'}
        result = original_upload(local_file_path, remote_path)
        # Never forward SDK exception text, URLs, credentials or unfiltered metadata.
        ok = isinstance(result, dict) and result.get('status') == 'success'
        failure = result.get('failure') if isinstance(result, dict) else None
        event('finished', sdk_success=ok, diagnostic=failure if isinstance(failure, dict) else None)
        return {'status': 'success' if ok else 'error'}
    except Exception as error:
        from safe_retry import diagnostic
        event('failed', diagnostic=getattr(error,'diagnostic',diagnostic(error,'upload',1)))
        return {'status': 'error', 'message': 'Upload result requires destination verification'}
    finally:
        lock.release()


async def upload(local_file_path: str, remote_path: str) -> dict:
    return await asyncio.to_thread(guarded, local_file_path, remote_path)


tool = official.mcp._tool_manager.get_tool('upload_file')
tool.fn = upload
tool.is_async = True
official.mcp.run(transport='stdio')
