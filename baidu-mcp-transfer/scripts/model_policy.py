"""Reusable model-only synchronization policy; enumerates metadata, never media contents."""
import os,stat
from pathlib import Path,PurePosixPath

MODEL_EXTENSIONS=frozenset({'.safetensors','.ckpt','.pt','.pth','.bin','.gguf','.sft','.onnx'})
POLICY={'version':1,'mode':'models_only','model_extensions':sorted(MODEL_EXTENSIONS),
        'case_insensitive':True,'overwrite':False,'skip_existing_same_name_size':True,
        'exclude_auxiliary_files':True,'follow_directory_links':False,
        'resume_same_upload_session':True,'preserve_unknown_records':True,
        'safe_retry_attempts':5,'refresh_before_expiry_seconds':3600}

def is_model(path):return Path(path).suffix.casefold() in MODEL_EXTENSIONS

def scan_models(source,follow_file_links=False):
    source=Path(source).expanduser().absolute()
    if not source.exists():raise ValueError('模型源路径不存在')
    if source.is_dir() and source.is_symlink():raise ValueError('不跟随目录符号链接')
    root=source if source.is_dir() else source.parent
    if not source.is_dir() and not is_model(source):raise ValueError('当前策略仅上传模型权重文件；TXT、图片等辅助文件不上传')
    files=[];issues=[];excluded=0
    def add(path):
        nonlocal excluded
        if not is_model(path):excluded+=1;return
        rel=path.relative_to(root).as_posix()
        if path.is_symlink() and not follow_file_links:
            issues.append({'rel':rel,'reason':'file_symlink_not_authorized'});return
        try:
            s=path.stat()
            if not stat.S_ISREG(s.st_mode):
                issues.append({'rel':rel,'reason':'not_regular_file'});return
            if s.st_size==0:
                issues.append({'rel':rel,'reason':'empty_model_file'});return
            files.append({'rel':rel,'size':s.st_size,'mtime_ns':s.st_mtime_ns,'realpath':str(path.resolve())})
        except OSError:issues.append({'rel':rel,'reason':'source_stat_failed'})
    if source.is_dir():
        for parent,dirs,names in os.walk(source,followlinks=False):
            kept=[]
            for name in sorted(dirs):
                p=Path(parent)/name
                if name in ('.git','.venv','__pycache__'):continue
                if p.is_symlink():issues.append({'rel':p.relative_to(root).as_posix(),'reason':'directory_symlink_not_followed'})
                else:kept.append(name)
            dirs[:]=kept
            for name in sorted(names):add(Path(parent)/name)
    else:add(source)
    files.sort(key=lambda r:(-r['size'],r['rel']))
    return str(root),{'files':files,'issues':issues,'excluded_auxiliary_files':excluded,
                     'effective_files':len(files),'effective_bytes':sum(r['size'] for r in files)}

def resolve_target(api,target):
    path=PurePosixPath(target)
    if not path.is_absolute() or '..' in path.parts or str(path)!=target or target=='/':
        raise ValueError('请选择具体且规范的网盘目标目录')
    current='/'
    for name in path.parts[1:]:
        rows=api.listing(current)
        matches=[r for r in rows if str(r.get('server_filename','')).casefold()==name.casefold()]
        if len(matches)!=1 or type(matches[0].get('isdir')) is not int or matches[0]['isdir']!=1:
            raise ValueError('网盘目标目录不存在或忽略大小写后有冲突，请先确认目标目录')
        actual=PurePosixPath(matches[0]['path'])
        if str(actual.parent)!=current or '..' in actual.parts:raise ValueError('网盘目录返回了范围之外的路径')
        current=str(actual)
    return current

def chunk_mib(vip_type,max_size):
    # Use the smallest larger supported configuration needed to stay under 2048 parts.
    return 8 if type(vip_type) is int and vip_type in (1,2) and max_size>8*1024**3 else 4

def resumable_checkpoint(saved,path,target,row,chunk_size):
    if not isinstance(saved,dict):return False
    count=(row['size']+chunk_size-1)//chunk_size
    completed=saved.get('completed')
    return (saved.get('path')==str(path) and saved.get('remote_path')==target
        and saved.get('size')==row['size'] and saved.get('mtime_ns')==row['mtime_ns']
        and saved.get('chunk_size')==chunk_size and isinstance(saved.get('uploadid'),str)
        and bool(saved['uploadid']) and isinstance(saved.get('hashes'),list) and len(saved['hashes'])==count
        and isinstance(completed,list) and all(type(i) is int and 0<=i<count for i in completed)
        and len(set(completed))<count)
