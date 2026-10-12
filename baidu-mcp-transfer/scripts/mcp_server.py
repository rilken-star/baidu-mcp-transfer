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
        result = original_upload(local_file_path, remote_path)
        # Never forward SDK exception text, URLs, credentials or unfiltered metadata.
        ok = isinstance(result, dict) and result.get('status') == 'success'
        event('finished', sdk_success=ok)
        return {'status': 'success' if ok else 'error'}
    except Exception:
        event('failed')
        return {'status': 'error', 'message': 'Upload result requires destination verification'}
    finally:
        lock.release()


async def upload(local_file_path: str, remote_path: str) -> dict:
    return await asyncio.to_thread(guarded, local_file_path, remote_path)


tool = official.mcp._tool_manager.get_tool('upload_file')
tool.fn = upload
tool.is_async = True
official.mcp.run(transport='stdio')
