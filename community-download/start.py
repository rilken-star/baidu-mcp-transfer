#!/usr/bin/env python3
"""Prompt locally and start one remote download process without storing a token."""
import argparse
import getpass
import json
import re
import shlex
import subprocess
import sys

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--host', required=True)
p.add_argument('--user', required=True)
p.add_argument('--port', type=int, default=22)
p.add_argument('--remote-dir', required=True)
p.add_argument('--control-path')
a = p.parse_args()
if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]*', a.host)
        or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', a.user)
        or not 1 <= a.port <= 65535
        or not a.remote_dir.startswith('/') or a.remote_dir == '/'):
    raise SystemExit('INVALID_DESTINATION')
ssh = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
       '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3',
       '-p', str(a.port)]
if a.control_path:
    ssh += ['-S', a.control_path]
ssh += [a.user + '@' + a.host]
probe = subprocess.run(ssh + [shlex.join(['test', '-f', a.remote_dir + '/controller.mjs'])],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if probe.returncode:
    raise SystemExit('SERVER_NOT_READY')
if sys.platform == 'darwin':
    dialog = ('text returned of (display dialog "输入百度 Access Token，仅本次传输使用，不保存。" '
              'default answer "" with hidden answer buttons {"取消", "开始传输"} '
              'default button "开始传输" with title "百度 MCP 下载授权")')
    response = subprocess.run(['osascript', '-e', dialog], capture_output=True, text=True)
    if response.returncode:
        raise SystemExit('AUTH_DIALOG_CANCELLED_OR_UNAVAILABLE')
    token = response.stdout.strip()
    response = None
else:
    if not sys.stdin.isatty():
        raise SystemExit('INTERACTIVE_HIDDEN_INPUT_REQUIRED')
    token = getpass.getpass('Baidu Access Token (hidden): ').strip()
if not token or len(token) > 8192 or any(c.isspace() for c in token):
    raise SystemExit('INVALID_TOKEN')
remote = '''import json,sys,subprocess,pathlib
base=pathlib.Path(sys.argv[1])
progress=base/'progress.json'
if progress.exists():
 state=json.loads(progress.read_text())
 if state.get('phase') not in ['complete','needs_attention']:
  raise SystemExit('CHECK_EXISTING_JOB_BEFORE_RESTART')
payload=sys.stdin.buffer.read()
if not json.loads(payload).get('access_token'):raise SystemExit('TOKEN_MISSING')
p=subprocess.Popen(['node',str(base/'controller.mjs')],stdin=subprocess.PIPE,
 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,cwd=base,start_new_session=True)
p.stdin.write(payload);p.stdin.close()
print('TRANSFER_STARTED_PID',p.pid)
'''
result = subprocess.run(ssh + [shlex.join(['python3', '-c', remote, a.remote_dir])],
                        input=json.dumps({'access_token': token}).encode(),
                        stderr=subprocess.DEVNULL)
token = None
if result.returncode:
    raise SystemExit('START_FAILED_CHECK_REMOTE_JOB')

