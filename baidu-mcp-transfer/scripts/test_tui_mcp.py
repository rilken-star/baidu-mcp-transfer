#!/usr/bin/env python3
"""Offline TUI MCP integration check; fake credential, no network or real model."""
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from common import atomic_json, read_json
from test_transfer import configuration
import worker

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);config=configuration(root)
    config_path=root/'config.json';atomic_json(config_path,config);config['_config_path']=str(config_path)
    source=Path(config['source'])/'sparse-fixture.bin'
    with source.open('wb') as f:f.truncate(2048*4*1024*1024+1)
    with patch.dict(os.environ,{'BAIDU_TUI_PARALLEL':'1','BAIDU_TUI_CHUNK_MIB':'4','BAIDU_TUI_WORKERS':'2'}):
        mcp=worker.Mcp(config,'offline-fake-token')
        try:
            response=mcp.call('tools/call',{'name':'upload_file','arguments':
                {'local_file_path':str(source),'remote_path':'/backup/tree'}},30)
        finally:mcp.close()
    parts=read_json(Path(config['remote_state'])/'parts.json')
    assert parts.get('stage')=='failed' and parts.get('diagnostic',{}).get('kind')=='too_many_parts'
    assert 'offline-fake-token' not in json.dumps(response)
    print('TUI MCP handshake, chunk-limit guard and token-free response: PASS')
