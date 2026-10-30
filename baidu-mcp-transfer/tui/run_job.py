#!/usr/bin/env python3
"""Local TUI upload controller: tokens remain in processes, not files or argv."""
import io
import json
import os
from pathlib import Path
import resource
import sys

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
package = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(package / 'scripts'))
from common import atomic_json, read_json, validate
import worker
from download import token
from core import SafeError

def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ('run', 'verify'):
        raise SystemExit('Usage: run_job.py {run|verify} CONFIG')
    path = Path(sys.argv[2]).resolve()
    config = validate(read_json(path))
    config['_config_path'] = str(path)
    try:
        access = token()
    except SafeError:
        atomic_json(Path(config['remote_state']) / 'progress.json',
                    {'phase':'needs_attention', 'message':'未提供可用的百度授权；请在终端隐藏输入。'})
        return 2
    os.environ['BAIDU_TUI_PARALLEL'] = '1'
    sys.stdin = io.TextIOWrapper(io.BytesIO((json.dumps({'access_token': access}) + '\n').encode()))
    access = None
    return worker.run(config, sys.argv[1] == 'verify')

if __name__ == '__main__':
    raise SystemExit(main())
