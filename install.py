#!/usr/bin/env python3
"""Offline skill installer. Does not configure MCP, connect, authorize or upload."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dest', type=Path, help='Skills directory, defaults to CODEX_HOME/skills')
    args = parser.parse_args()
    source = Path(__file__).resolve().parent / 'baidu-mcp-transfer'
    expected = json.loads((source.parent / 'MANIFEST.json').read_text(encoding='utf-8'))['sha256']
    paths = list(source.rglob('*'))
    if any(p.is_symlink() for p in paths):
        raise SystemExit('Refusing package containing symlinks')
    actual = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in paths if p.is_file()}
    if actual != expected:
        raise SystemExit('Package checksum mismatch; installation aborted')
    destination = args.dest or Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'skills'
    target = destination.expanduser().resolve() / source.name
    if target.exists():
        raise SystemExit('Skill already exists; refusing to overwrite: ' + str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    print('Installed: ' + str(target))
    print('Available on the next Codex turn. Installation did not start a transfer.')


if __name__ == '__main__':
    main()
