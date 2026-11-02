#!/usr/bin/env python3
"""Offline installer for the Baidu transfer skill and optional terminal launcher."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dest', type=Path, help='Skills directory; defaults to CODEX_HOME/skills')
    parser.add_argument('--bin-dir', type=Path, default=Path.home() / '.local/bin',
                        help='CLI launcher directory; defaults to ~/.local/bin')
    parser.add_argument('--no-cli', action='store_true', help='Install skill only, without CLI launcher')
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
    launcher = args.bin_dir.expanduser().resolve() / 'baidu-tui'
    if target.exists() or target.is_symlink():
        raise SystemExit('Skill already exists; refusing to overwrite: ' + str(target))
    if not args.no_cli and (launcher.exists() or launcher.is_symlink()):
        raise SystemExit('CLI already exists; refusing to overwrite: ' + str(launcher))
    target.parent.mkdir(parents=True, exist_ok=True)
    if not args.no_cli:
        launcher.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    if not args.no_cli:
        root = shlex.quote(str(target))
        script = ("#!/bin/sh\n"
                  "package=" + root + "\n"
                  "export PYTHONUTF8=1\n"
                  "if [ -z \"${BAIDU_TUI_SECRETS_FILE:-}\" ] && [ -f \"$HOME/.config/model-download/secrets.env\" ]; then\n"
                  "  export BAIDU_TUI_SECRETS_FILE=\"$HOME/.config/model-download/secrets.env\"\n"
                  "fi\n"
                  "if [ -x \"$package/.venv/bin/python\" ]; then\n"
                  "  exec \"$package/.venv/bin/python\" \"$package/tui/tui.py\" \"$@\"\n"
                  "fi\n"
                  "exec python3 \"$package/tui/tui.py\" \"$@\"\n")
        fd = os.open(launcher, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(script)
        print('CLI: ' + str(launcher))
    print('Installed: ' + str(target))
    print('Installation was offline and did not start a transfer.')


if __name__ == '__main__':
    main()
