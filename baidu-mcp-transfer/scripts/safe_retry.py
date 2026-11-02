"""Bounded retries for reads/identical parts; never blindly repeat file publication."""
import errno, time
from pathlib import PurePosixPath

class RequestFailed(Exception):
    def __init__(self,diagnostic):
        self.diagnostic=diagnostic
        super().__init__('Baidu request failed; sanitized diagnostic available')
class UnknownOutcome(RequestFailed):pass
class Throttled(Exception):pass
class TemporaryAPIError(Exception):pass

def causes(error):
    pending=[error];seen=set();output=[]
    while pending:
        item=pending.pop()
        if not isinstance(item,BaseException) or id(item) in seen:continue
        seen.add(id(item));output.append(item)
        for name in ('reason','__cause__','__context__'):
            pending.append(getattr(item,name,None))
    return output

def transient(error):
    chain=causes(error);names={type(x).__name__ for x in chain}
    if names & {'AuthError','SSLError','SSLCertVerificationError','CertificateError'}:return False
    for x in chain:
        code=getattr(x,'status',None) or getattr(x,'code',None)
        if type(code) is int and code in (429,500,502,503,504):return True
        if isinstance(x,OSError) and x.errno in (errno.ECONNRESET,errno.ECONNREFUSED,errno.ETIMEDOUT,errno.ENETUNREACH,errno.EHOSTUNREACH,errno.EPIPE):return True
    return bool(names & {'Throttled','TemporaryAPIError','gaierror','TimeoutError','ConnectionResetError','ConnectionRefusedError','BrokenPipeError','NewConnectionError','ConnectTimeoutError','ReadTimeoutError','ProtocolError','RemoteDisconnected','IncompleteRead','JSONDecodeError'})

def before_send(error):
    names={type(x).__name__ for x in causes(error)}
    return transient(error) and bool(names & {'gaierror','NewConnectionError','ConnectTimeoutError','ConnectionRefusedError'}) and not bool(names & {'ReadTimeoutError','ProtocolError','RemoteDisconnected','IncompleteRead'})

def diagnostic(error,stage,attempt):
    value={'operation_stage':stage,'attempt':attempt,'error_type':type(error).__name__,
           'cause_types':list(dict.fromkeys(type(x).__name__ for x in causes(error)))[:6]}
    for item in causes(error):
        status=getattr(item,'status',None) or getattr(item,'code',None)
        if type(status) is int and status>0:value['http_status']=status;break
    previous=getattr(error,'diagnostic',{})
    if isinstance(previous,dict) and type(previous.get('api_errno')) is int:value['api_errno']=previous['api_errno']
    return value

def retry_call(fn,stage='metadata',attempts=5,allowed=transient,rewind=None,event=None):
    for attempt in range(1,attempts+1):
        try:
            if rewind:rewind()
            return fn()
        except Exception as error:
            if isinstance(error,InterruptedError) or type(error).__name__=='Stopped':raise
            info=diagnostic(error,stage,attempt)
            if not allowed(error) or attempt==attempts:raise RequestFailed(info) from None
            delay=min(8,2**(attempt-1))
            if event:event('retry_wait',operation_stage=stage,attempt=attempt,next_delay_seconds=delay,diagnostic=info)
            time.sleep(delay)


def install_sdk_retry(official,event=None,probe=None):
    from baidu_auth import Session
    session=Session(official.access_token)
    cls=official.fileupload_api.FileuploadApi
    if getattr(cls,'_baidu_safe_retry',False):return
    pre=cls.xpanfileprecreate;part=cls.pcssuperfile2;create=cls.xpanfilecreate
    # The shared wrapper owns part retries, not the original nested retry loop.
    official.MAX_RETRIES=1
    def prepare(self,kw):
        kw.setdefault('_request_timeout',(10,120))
        client=getattr(self,'api_client',None)
        rest=getattr(client,'rest_client',None)
        pool=getattr(rest,'pool_manager',None)
        if pool is not None:
            pool.connection_pool_kw['retries']=0
            # A client can reuse pools created by a prior call.
            for key in list(pool.pools.keys()):
                connection=pool.pools.get(key)
                if connection is not None:connection.retries=0
    def start(self,*args,**kwargs):
        prepare(self,kwargs)
        return retry_call(lambda:pre(self,*args,**kwargs),stage='precreate',allowed=before_send,event=event)
    def send(self,*args,**kwargs):
        prepare(self,kwargs)
        stream=kwargs.get('file');position=None
        if stream is not None:
            try:position=stream.tell()
            except (AttributeError,OSError):pass
        rewind=(lambda:stream.seek(position)) if position is not None else None
        attempts=5 if rewind is not None else 1
        return retry_call(lambda:part(self,*args,**kwargs),stage='upload_part',attempts=attempts,rewind=rewind,event=event)
    def verify(path,size):
        from worker import Baidu
        rows=Baidu(session.get()).listing(str(PurePosixPath(path).parent))
        found=[r for r in rows if str(r.get('path','')).casefold()==path.casefold()]
        if len(found)==1:
            row=found[0]
            if type(row.get('isdir')) is int and row['isdir']==0 and type(row.get('size')) is int and row['size']==size:
                return {'errno':0,'fs_id':row.get('fs_id'),'path':row['path'],'size':size}
        return None
    def finish(self,*args,**kwargs):
        prepare(self,kwargs)
        try:return create(self,*args,**kwargs)
        except Exception as error:
            info=diagnostic(error,'finalize',1)
            if transient(error):
                if event:event('verifying_after_error',operation_stage='finalize',diagnostic=info)
                try:
                    result=retry_call(lambda:(probe or verify)(kwargs.get('path',''),kwargs.get('size')),stage='verify_after_finalize',attempts=5 if probe is not None else 1,event=event)
                    if result is not None:return result
                except Exception as verification:
                    info['verification_error_type']=type(verification).__name__
                info['outcome']='unknown_not_resubmitted'
                raise UnknownOutcome(info) from None
            raise RequestFailed(info) from None
    cls.xpanfileprecreate=start;cls.pcssuperfile2=send;cls.xpanfilecreate=finish
    cls._baidu_safe_retry=True
