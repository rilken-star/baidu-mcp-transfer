"""Configuration and durable metadata; never stores credentials."""
import json
import os
from pathlib import Path, PurePosixPath


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def absolute(value):
    path = PurePosixPath(value)
    if (not path.is_absolute() or str(path) != value or '..' in path.parts
            or value == '/' or '\x00' in value):
        raise ValueError('Expected a normalized absolute path other than /')
    return value


def relative(value):
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or '..' in path.parts
            or str(path) != value or '\x00' in value or value == '.'):
        raise ValueError('Expected a normalized relative path')
    return value


def inside(path, root):
    return path == root or path.startswith(root + '/')


def validate(config):
    allowed = {'version', 'host', 'user', 'port', 'control_path', 'source', 'target',
               'remote_code', 'remote_state', 'min_bytes', 'exclude_roots', 'exclude_globs',
               'overwrite', 'follow_file_links', '_config_path'}
    if not isinstance(config, dict) or set(config) - allowed:
        raise ValueError('Unsupported config key; credentials must never be stored in config')
    if config.get('version') != 1:
        raise ValueError('Unsupported configuration version')
    for key in ('source', 'target', 'remote_code', 'remote_state'):
        absolute(config[key])
    for key in ('remote_code', 'remote_state'):
        if inside(config[key], config['source']):
            raise ValueError('Runtime and state must be outside the source tree')
    if inside(config['remote_code'], config['remote_state']) or inside(config['remote_state'], config['remote_code']):
        raise ValueError('Runtime and state directories must be disjoint')
    if type(config.get('min_bytes')) is not int or config['min_bytes'] < 0:
        raise ValueError('min_bytes must be a nonnegative integer')
    for root in config.get('exclude_roots', []):
        relative(root)
    for pattern in config.get('exclude_globs', []):
        if not isinstance(pattern, str) or not pattern or pattern.startswith('/') or '..' in PurePosixPath(pattern).parts:
            raise ValueError('Invalid source-relative glob')
    if type(config.get('overwrite')) is not bool or type(config.get('follow_file_links')) is not bool:
        raise ValueError('Explicit boolean overwrite and follow_file_links required')
    return config
