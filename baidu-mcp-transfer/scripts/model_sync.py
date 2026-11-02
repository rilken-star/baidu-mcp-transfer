"""Model-only upload controller with durable per-file and multipart state."""
def run(job_path):
    import contextlib
    with contextlib.ExitStack() as resources:
        import collections, contextlib, fcntl, hashlib, importlib.util, json, logging, os
        from pathlib import Path, PurePosixPath
        import stat, sys, time

        PACK=Path(__file__).resolve().parents[1]
        JOB=Path(job_path).expanduser().absolute()
        sys.path[:0]=[str(PACK/'scripts'),str(PACK/'tui'),str(PACK/'vendor')]
        if not os.environ.get('BAIDU_TUI_SECRETS_FILE') and (Path.home()/'.config/model-download/secrets.env').exists():
            os.environ['BAIDU_TUI_SECRETS_FILE']=str(Path.home()/'.config/model-download/secrets.env')
        logging.disable(logging.CRITICAL)
        import download, worker
        from common import atomic_json, read_json, absolute
        from parallel_upload import build
        from baidu_auth import install_sdk_refresh
        from safe_retry import install_sdk_retry, diagnostic

        lock=resources.enter_context(open(JOB/'lock','a'))
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        plan=read_json(JOB/'plan.json')
        SOURCE=Path(absolute(plan['source']))
        TARGET=plan['target']
        absolute(TARGET)
        results=read_json(JOB/'results.json',{})
        status={'phase':'starting','started_at':time.time(),'pid':os.getpid(),'total_files':len(plan['inventory']['files']),'total_bytes':plan['inventory']['effective_bytes'],'source_issues':len(plan['inventory']['issues'])}
        active={}; last_event=0

        def save():
            status['updated_at']=time.time()
            status['counts']=dict(collections.Counter(v['state'] for v in results.values()))
            status['uploaded_bytes']=sum(v.get('size',0) for v in results.values() if v['state']=='uploaded')
            status['skipped_bytes']=sum(v.get('size',0) for v in results.values() if v['state']=='skipped_same_name_size')
            status['current']=dict(active)
            atomic_json(JOB/'results.json',results)
            atomic_json(JOB/'status.json',status)

        def event(stage,**values):
            nonlocal last_event
            active.update(stage=stage,**values)
            if stage in ('finished','failed') or time.monotonic()-last_event>=2:
                save(); last_event=time.monotonic()

        def matches(directory,name):
            rows=api.listing(directory)
            for r in rows:
                p=PurePosixPath(r.get('path',''))
                if str(p.parent)!=directory or '..' in p.parts:
                    raise worker.Attention('Remote listing outside requested directory')
            return [r for r in rows if str(r.get('server_filename','')).casefold()==name.casefold()]

        def parent_for(rel):
            parent=TARGET
            for part in PurePosixPath(rel).parent.parts:
                if part=='.': continue
                found=matches(parent,part)
                if found:
                    if len(found)!=1 or found[0].get('isdir')!=1:
                        raise worker.Attention('Directory case conflict')
                    parent=found[0]['path']
                else:
                    new=str(PurePosixPath(parent)/part)
                    attempts=read_json(JOB/'directories.json',{})
                    if new in attempts: raise worker.Attention('Directory creation previously uncertain')
                    attempts[new]='attempting';atomic_json(JOB/'directories.json',attempts)
                    api.request('create',{'path':new,'isdir':1,'size':0,'rtype':0},post=True)
                    found=matches(parent,part)
                    if len(found)!=1 or found[0].get('isdir')!=1: raise worker.Attention('Directory creation unverified')
                    parent=found[0]['path']
                    attempts[new]='verified';atomic_json(JOB/'directories.json',attempts)
            return parent

        def existing_state(found,size):
            if len(found)!=1: return 'case_conflict'
            r=found[0]
            return 'skipped_same_name_size' if type(r.get('isdir')) is int and r['isdir']==0 and type(r.get('size')) is int and r['size']==size else 'different_conflict'

        try:
            if plan.get('predecessor'):
                prior=Path(plan['predecessor'])
                status['phase']='waiting_for_regular_files';save()
                while True:
                    previous=read_json(prior/'status.json',{})
                    if previous.get('phase') in ('complete','needs_attention'):
                        if previous.get('reason') or previous.get('counts',{}).get('attempting'):
                            raise worker.Attention('Previous task needs review; linked-file task not started')
                        if previous.get('counts',{}).get('unknown'):
                            previous_results=read_json(prior/'results.json',{})
                            own_paths={r['rel'].casefold() for r in plan['inventory']['files']}
                            if any(rel.casefold() in own_paths for rel,r in previous_results.items() if r.get('state')=='unknown'):
                                raise worker.Attention('Uncertain destination overlaps linked-file task; stopped')
                        break
                    pid=previous.get('pid')
                    if pid:
                        try: os.kill(pid,0)
                        except ProcessLookupError: raise worker.Attention('Previous task stopped without final status')
                    time.sleep(30)
            from model_policy import resolve_target, chunk_mib, resumable_checkpoint, is_model
            if plan.get('policy',{}).get('mode')!='models_only':
                raise worker.Attention('Model-only policy missing; task not started')
            if not SOURCE.is_dir() or SOURCE.is_symlink():
                raise worker.Attention('Model source root must be a real directory')
            if type(plan.get('follow_file_links',False)) is not bool:
                raise worker.Attention('File-link authorization must be explicit boolean')
            if not all(is_model(row['rel']) and type(row['size']) is int and row['size']>0 for row in plan['inventory']['files']):
                raise worker.Attention('Auxiliary or invalid model entries rejected; no upload started')
            pair_dir=Path.home()/'.local/state/baidu-sync/.target-locks'
            pair_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
            pair_lock=resources.enter_context((pair_dir/(hashlib.sha256(TARGET.casefold().encode()).hexdigest()+'.lock')).open('a'))
            os.fchmod(pair_lock.fileno(),0o600)
            try:fcntl.flock(pair_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise worker.Attention('Another model task owns this cloud target; no duplicate started')
            token=download.token()
            api=worker.Baidu(token)
            try:TARGET=resolve_target(api,TARGET)
            except ValueError as error:raise worker.Attention(str(error)) from None
            plan['target']=TARGET
            atomic_json(JOB/'plan.json',plan)
            membership=download.Api(access_token=token).request('nas','uinfo').get('vip_type')
            selected_chunk=chunk_mib(membership,max((r['size'] for r in plan['inventory']['files']),default=0))
            os.environ['BAIDU_NETDISK_ACCESS_TOKEN']=token
            spec=importlib.util.spec_from_file_location('sync_official',PACK/'vendor/fileupload_tool.py')
            official=importlib.util.module_from_spec(spec)
            with open(os.devnull,'w') as sink,contextlib.redirect_stdout(sink),contextlib.redirect_stderr(sink):
                spec.loader.exec_module(official)
            install_sdk_refresh(official)
            os.environ.pop('BAIDU_NETDISK_ACCESS_TOKEN',None)
            cls=official.fileupload_api.FileuploadApi
            pre=cls.xpanfileprecreate; create=cls.xpanfilecreate
            def guarded(fn):
                def call(self,*args,**kw):
                    p=PurePosixPath(kw.get('path',''))
                    if not str(p).startswith(TARGET+'/') or '..' in p.parts:
                        raise worker.Attention('Upload outside requested target')
                    if matches(str(p.parent),p.name):
                        raise worker.Attention('Destination appeared; refusing overwrite')
                    kw['rtype']=0
                    kw['_request_timeout']=120
                    return fn(self,*args,**kw)
                return call
            cls.xpanfileprecreate=guarded(pre)
            cls.xpanfilecreate=guarded(create)
            install_sdk_retry(official,event=event)
            official.CHUNK_SIZE=selected_chunk*1024*1024
            atomic_json(JOB/'transfer-settings.json',{'chunk_mib':selected_chunk,'workers':4,'policy':'models_only','updated_at':time.time()})
            uploader=build(official,event,workers=4,checkpoint_dir=str(JOB/'checkpoints'))
            status['phase']='running'; save()
            local_counts=collections.Counter(r['rel'].casefold() for r in plan['inventory']['files'])
            resume_sources={read_json(p,{}).get('path') for p in (JOB/'checkpoints').glob('*.json')}
            ordered=sorted(plan['inventory']['files'],key=lambda r:0 if str(SOURCE/r['rel']) in resume_sources else 2 if results.get(r['rel'],{}).get('state') in ('attempting','unknown') else 1)
            for row in ordered:
                rel=row['rel'];size=row['size']
                if rel in results and results[rel]['state'] in ('uploaded','skipped_same_name_size','case_conflict','different_conflict'):
                    continue
                if local_counts[rel.casefold()]!=1:
                    results[rel]={'state':'case_conflict','size':size};save();continue
                path=SOURCE/rel
                if '..' in PurePosixPath(rel).parts or (path.is_symlink() and not plan.get('follow_file_links')) or (plan.get('follow_file_links') and str(path.resolve()) != row.get('realpath')) or (not plan.get('follow_file_links') and not path.resolve().is_relative_to(SOURCE.resolve())):
                    results[rel]={'state':'source_link_or_scope_issue','size':size};save();continue
                st=path.stat()
                if not stat.S_ISREG(st.st_mode) or (st.st_size,st.st_mtime_ns)!=(size,row['mtime_ns']):
                    results[rel]={'state':'source_changed','size':size};save();continue
                parent=parent_for(rel)
                found=matches(parent,path.name)
                if found:
                    results[rel]={'state':existing_state(found,size),'size':size};save();continue
                if results.get(rel,{}).get('state') in ('attempting','unknown'):
                    destination=str(PurePosixPath(parent)/path.name)
                    checkpoint=read_json(JOB/'checkpoints'/(hashlib.sha256(destination.encode()).hexdigest()+'.json'),{})
                    count=(size+official.CHUNK_SIZE-1)//official.CHUNK_SIZE
                    completed=checkpoint.get('completed',[])
                    same_session=resumable_checkpoint(checkpoint,path,destination,row,official.CHUNK_SIZE)
                    if same_session:
                        atomic_json(JOB/'resume-audit'/(hashlib.sha256(rel.encode()).hexdigest()+'.json'),
                            {'previous':results[rel],'source':{'size':size,'mtime_ns':row['mtime_ns']},
                             'confirmed_parts':len(set(completed)),'total_parts':count,'same_upload_session':True,'checked_at':time.time()})
                        results[rel]={'state':'multipart_paused','size':size};save()
                if results.get(rel,{}).get('state') in ('attempting','unknown'):
                    permissions=read_json(JOB/'retry-authorizations.json',{})
                    permission=permissions.get(rel,{})
                    signature={'size':size,'mtime_ns':row['mtime_ns']}
                    if permission.get('approved') is True and not permission.get('consumed_at') and permission.get('source')==signature:
                        audit={'previous':results[rel],'source':signature,'approved_at':permission.get('approved_at'),'consumed_at':time.time()}
                        atomic_json(JOB/'retry-audit'/(hashlib.sha256(rel.encode()).hexdigest()+'.json'),audit)
                        permission['consumed_at']=audit['consumed_at']
                        permissions[rel]=permission;atomic_json(JOB/'retry-authorizations.json',permissions)
                    else:
                        results[rel]={'state':'unknown','size':size};save();continue
                if (size+official.CHUNK_SIZE-1)//official.CHUNK_SIZE>2048:
                    results[rel]={'state':'chunk_limit','size':size};save();continue
                results[rel]={'state':'attempting','size':size}
                active.clear();active.update(size=size,stage='starting',completed_bytes=0)
                save()
                target=str(PurePosixPath(parent)/path.name)
                cfg=official.openapi_client.Configuration();cfg.retries=0
                with open(os.devnull,'w') as sink,contextlib.redirect_stdout(sink),contextlib.redirect_stderr(sink):
                    response=(official.upload_small_file(str(path),target,size,token,cfg) if size<=official.CHUNK_SIZE else uploader(str(path),target,size,token,cfg))
                found=matches(parent,path.name)
                st2=path.stat()
                if len(found)==1 and found[0].get('path')==target and existing_state(found,size)=='skipped_same_name_size' and (st2.st_size,st2.st_mtime_ns)==(size,row['mtime_ns']):
                    results[rel]={'state':'uploaded','size':size,'verified_at':time.time()}
                    active.clear();save()
                else:
                    results[rel]={'state':'unknown','size':size}
                    if isinstance(response,dict) and isinstance(response.get('failure'),dict):
                        status['diagnostic']={k:v for k,v in response['failure'].items() if k in ('kind','part','attempt','http_status','api_errno','errno','error_type','operation_stage','cause_types','outcome')}
                    raise worker.Attention('Upload not verified; stopped to avoid duplicate or overwrite')
            bad=any(v['state'] not in ('uploaded','skipped_same_name_size') for v in results.values()) or bool(plan['inventory']['issues'])
            status['phase']='needs_attention' if bad else 'complete'
            active.clear();status['finished_at']=time.time();save()
        except BaseException as error:
            status['phase']='needs_attention'
            status['error_type']=type(error).__name__
            status['diagnostic']=getattr(error,'diagnostic',diagnostic(error,'controller',1))
            status['reason']=str(error) if isinstance(error,(worker.Attention,download.SafeError)) else 'Stopped; exception details suppressed'
            save()
            return 1

        return 0

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job')
    raise SystemExit(run(parser.parse_args().job))
