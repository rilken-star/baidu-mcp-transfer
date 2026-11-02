"""Bounded concurrent multipart upload for the isolated Baidu transfer controller."""
import concurrent.futures
import contextlib
import hashlib
import io
import json
import os
import threading
import time
import tempfile


def build(official, event, workers=8, checkpoint_dir=None):
    def upload(path, remote_path, file_size, access_token, configuration=None):
        chunk_size = official.CHUNK_SIZE
        count = (file_size + chunk_size - 1) // chunk_size
        hashes = []
        fd = None
        lock = threading.Lock()
        done_bytes = 0
        done_count = 0
        failure = {}
        part_contexts = contextlib.ExitStack()
        part_local = threading.local()
        checkpoint_root = checkpoint_dir or os.path.join(os.path.expanduser('~'), '.local/state/baidu-tui/upload-checkpoints')
        checkpoint_path = os.path.join(checkpoint_root, hashlib.sha256(remote_path.encode()).hexdigest()+'.json')
        def save_checkpoint(value):
            os.makedirs(checkpoint_root, mode=0o700, exist_ok=True)
            fd2, temp = tempfile.mkstemp(prefix='.checkpoint-', dir=checkpoint_root)
            try:
                with os.fdopen(fd2,'w') as out:
                    json.dump(value,out);out.flush();os.fsync(out.fileno())
                os.replace(temp,checkpoint_path)
            finally:
                if os.path.exists(temp):os.unlink(temp)
        def record_failure(i, attempt, kind, error=None, response=None):
            info = {'part':i, 'attempt':attempt, 'kind':kind}
            if error is not None:
                info['error_type'] = type(error).__name__
                nested=getattr(error,'diagnostic',{})
                if isinstance(nested,dict):
                    for key in ('operation_stage','error_type','cause_types','http_status','api_errno','outcome','attempt'):
                        if key in nested:info[key]=nested[key]
                status = getattr(error, 'status', None)
                if type(status) is int: info['http_status'] = status
                errno = getattr(error, 'errno', None)
                if type(errno) is int: info['errno'] = errno
            if isinstance(response, dict):
                info['returned_md5'] = bool(response.get('md5'))
                code = response.get('errno')
                if type(code) is int: info['api_errno'] = code
            with lock:
                failure.clear(); failure.update(info)
        try:
            fd = os.open(path, os.O_RDONLY)
            start_stat = os.fstat(fd)
            last_event = 0
            for i in range(count):
                block = os.pread(fd, min(chunk_size, file_size-i*chunk_size), i*chunk_size)
                if len(block) != min(chunk_size, file_size-i*chunk_size):
                    return {'status':'error','message':'source read failed'}
                hashes.append(hashlib.md5(block).hexdigest())
                if time.monotonic()-last_event >= 3:
                    event('hashing', source_bytes=file_size, hashed_bytes=min(file_size,(i+1)*chunk_size))
                    last_event=time.monotonic()
            s = os.fstat(fd)
            if (s.st_size,s.st_mtime_ns) != (start_stat.st_size,start_stat.st_mtime_ns):
                return {'status':'error','message':'source changed'}
            block_list = json.dumps(hashes)
            with official.openapi_client.ApiClient(configuration) as client:
                api = official.fileupload_api.FileuploadApi(client)
                identity={'path':path,'remote_path':remote_path,'size':file_size,
                          'mtime_ns':start_stat.st_mtime_ns,'chunk_size':chunk_size,'hashes':hashes}
                saved=None
                if os.path.exists(checkpoint_path):
                    with open(checkpoint_path) as src:saved=json.load(src)
                    if any(saved.get(k)!=v for k,v in identity.items()):
                        return {'status':'error','message':'checkpoint does not match source'}
                    if not isinstance(saved.get('completed'),list) or not saved.get('uploadid'):
                        return {'status':'error','message':'invalid checkpoint'}
                if saved is None:
                    pre = api.xpanfileprecreate(access_token=access_token, path=remote_path,
                        isdir=0, size=file_size, autoinit=1, block_list=block_list, rtype=3)
                    if pre.get('errno') != 0 or not pre.get('uploadid'):
                        return {'status':'error','message':'预创建文件失败: %s' % pre.get('errno')}
                    saved={**identity,'uploadid':pre['uploadid'],'completed':[]}
                    save_checkpoint(saved)
                uploadid=saved['uploadid']
                completed=set(saved['completed'])
                if any(type(i) is not int or i<0 or i>=count for i in completed):
                    return {'status':'error','message':'invalid checkpoint parts'}
                done_bytes=sum(min(chunk_size,file_size-i*chunk_size) for i in completed)
                done_count=len(completed)
                event('sending_part', completed_bytes=done_bytes, completed_parts=done_count, total_parts=count)
                def send(i):
                    nonlocal done_bytes, done_count
                    size = min(chunk_size,file_size-i*chunk_size)
                    try:
                        data = os.pread(fd,size,i*chunk_size)
                    except OSError as error:
                        record_failure(i,0,'source_read_error',error=error);return False
                    if len(data)!=size or hashlib.md5(data).hexdigest()!=hashes[i]:
                        record_failure(i,0,'source_mismatch');return False
                    attempts = 1 if getattr(official.fileupload_api.FileuploadApi, "_baidu_safe_retry", False) else 8
                    for attempt in range(attempts):
                        try:
                            cfg = official.openapi_client.Configuration()
                            cfg.connection_pool_maxsize=4
                            cfg.retries=0
                            if not hasattr(part_local,'api'):
                                with lock:
                                    part_client=part_contexts.enter_context(official.openapi_client.ApiClient(cfg))
                                part_local.api=official.fileupload_api.FileuploadApi(part_client)
                            stream=io.BytesIO(data);stream.name=os.path.basename(path)
                            result=part_local.api.pcssuperfile2(access_token=access_token,
                                partseq=str(i),path=remote_path,uploadid=uploadid,
                                type='tmpfile',file=stream,_request_timeout=120)
                            if result.get('md5')==hashes[i]:
                                with lock:
                                    completed.add(i);saved['completed']=sorted(completed);save_checkpoint(saved)
                                    done_bytes+=size;done_count+=1
                                    event('accepted_part', part=max(-1,done_bytes//chunk_size-1), completed_bytes=done_bytes,
                                        completed_parts=done_count,total_parts=count)
                                return True
                            record_failure(i,attempt+1,'part_response_mismatch',response=result)
                        except Exception as error:
                            record_failure(i,attempt+1,'part_request_failed',error=error)
                        if attempt<attempts-1:time.sleep(min(30,2**attempt))
                    return False
                # Limit the queue to the actual number of connections; stop sending on failure.
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                    remaining=iter(i for i in range(count) if i not in completed);pending=set()
                    for _ in range(workers):
                        i=next(remaining,None)
                        if i is not None:pending.add(pool.submit(send,i))
                    while pending:
                        finished,pending=concurrent.futures.wait(pending,return_when=concurrent.futures.FIRST_COMPLETED)
                        if any(not f.result() for f in finished):
                            for f in pending:f.cancel()
                            return {'status':'error','message':'parallel part failed','failure':dict(failure)}
                        for _ in finished:
                            i=next(remaining,None)
                            if i is not None:pending.add(pool.submit(send,i))
                s=os.fstat(fd)
                if (s.st_size,s.st_mtime_ns)!=(start_stat.st_size,start_stat.st_mtime_ns):
                    return {'status':'error','message':'source changed'}
                result=api.xpanfilecreate(access_token=access_token,path=remote_path,
                    isdir=0,size=file_size,uploadid=uploadid,block_list=block_list,rtype=3)
                if result.get('errno') != 0:
                    return {'status':'error','message':'创建文件失败: %s' % result.get('errno')}
                os.unlink(checkpoint_path)
                return {'status':'success'}
        except Exception as error:
            from safe_retry import diagnostic
            return {'status':'error','message':'parallel transfer failed','failure':getattr(error,'diagnostic',diagnostic(error,'multipart',1))}
        finally:
            part_contexts.close()
            if fd is not None:os.close(fd)
    return upload
