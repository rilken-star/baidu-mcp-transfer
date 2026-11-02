"""Opt-in, locked Baidu OAuth refresh. Credential values never enter logs."""
import datetime,fcntl,json,os,re,shlex,stat,tempfile,threading,time
from pathlib import Path
import urllib.parse,urllib.request

class AuthError(Exception):pass
_THREAD_LOCK=threading.RLock()
_KEYS={'BAIDU_ACCESS_TOKEN','BAIDU_NETDISK_ACCESS_TOKEN','BAIDU_REFRESH_TOKEN','BAIDU_APP_KEY','BAIDU_SECRET_KEY','BAIDU_TOKEN_EXPIRES_AT'}

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise AuthError('Baidu OAuth redirect refused')

def _read(path):
    if any(p.is_symlink() for p in (path.parent.parent,path.parent,path)):
        raise AuthError('Credential symlink refused')
    if stat.S_IMODE(path.parent.stat().st_mode)!=0o700 or stat.S_IMODE(path.stat().st_mode)!=0o600:
        raise AuthError('Credential permissions must be 700/600')
    raw=path.read_bytes();values={}
    for line in raw.decode('utf-8').splitlines():
        m=re.match(r'\s*(?:export\s+)?([A-Z_]+)\s*=\s*(.*)',line)
        if m and m[1] in _KEYS:
            parts=shlex.split(m[2],comments=True)
            if len(parts)==1:values[m[1]]=parts[0]
    return raw,values

def _token(values):return values.get('BAIDU_NETDISK_ACCESS_TOKEN') or values.get('BAIDU_ACCESS_TOKEN','')
def _version(path):
    s=path.stat();return str(s.st_mtime_ns)+':'+str(s.st_size)
def _atomic(path,data):
    if path.is_symlink():raise AuthError('State symlink refused')
    fd,tmp=tempfile.mkstemp(prefix='.baidu-auth-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            os.fchmod(f.fileno(),0o600);f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
def _state(path,data):_atomic(path,(json.dumps(data)+'\n').encode())

def _exchange(values):
    params={'grant_type':'refresh_token','refresh_token':values['BAIDU_REFRESH_TOKEN'],
            'client_id':values['BAIDU_APP_KEY'],'client_secret':values['BAIDU_SECRET_KEY']}
    req=urllib.request.Request('https://openapi.baidu.com/oauth/2.0/token',
        data=urllib.parse.urlencode(params).encode(),headers={'Content-Type':'application/x-www-form-urlencoded'})
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=30) as response:
        return json.load(response)

class Session:
    def __init__(self,initial=None):
        self.initial=initial;self.path=None
        configured=os.environ.get('BAIDU_TUI_SECRETS_FILE')
        if not configured:
            default=Path.home()/'.config/model-download/secrets.env'
            if default.exists():configured=str(default)
        if configured:
            p=Path(configured).expanduser()
            try:
                _,v=_read(p)
                if initial is None or initial==_token(v):self.path=p
            except Exception:
                if initial is None:raise AuthError('Configured Baidu credentials unavailable') from None
    def get(self,min_valid=3600):
        if self.path is None:
            if not self.initial:raise AuthError('Baidu access token missing')
            return self.initial
        try:return self._get(min_valid)
        except AuthError:raise
        except Exception:raise AuthError('Baidu authorization refresh failed; details suppressed') from None
    def _get(self,min_valid):
        path=self.path
        with _THREAD_LOCK:
            lockpath=path.parent/'.baidu-refresh.lock'
            fd=os.open(lockpath,os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'a') as lock:
                os.fchmod(lock.fileno(),0o600);fcntl.flock(lock,fcntl.LOCK_EX)
                original,values=_read(path);token=_token(values)
                if not token:raise AuthError('Baidu access token missing')
                needed=('BAIDU_REFRESH_TOKEN','BAIDU_APP_KEY','BAIDU_SECRET_KEY','BAIDU_TOKEN_EXPIRES_AT')
                if not all(values.get(k) for k in needed):return token
                if int(values['BAIDU_TOKEN_EXPIRES_AT'])>time.time()+min_valid:return token
                guard=path.parent/'.baidu-refresh-state.json';version=_version(path)
                if guard.is_symlink():raise AuthError('Refresh state symlink refused')
                prior=json.loads(guard.read_text()) if guard.exists() else {}
                if prior.get('version')==version and prior.get('phase') in ('refreshing','needs_reauthorization'):
                    raise AuthError('Previous refresh failed or uncertain; renew authorization before retry')
                _state(guard,{'version':version,'phase':'refreshing','checked_at':time.time()})
                try:
                    result=_exchange(values)
                    access=result.get('access_token');refresh=result.get('refresh_token');ttl=result.get('expires_in')
                    if not all(isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9._~+/=-]+',v) for v in (access,refresh)) or type(ttl) is not int or ttl<=0:
                        raise AuthError('Baidu rejected refresh; renew authorization')
                    replacement={'BAIDU_ACCESS_TOKEN':access,'BAIDU_NETDISK_ACCESS_TOKEN':access,
                        'BAIDU_REFRESH_TOKEN':refresh,'BAIDU_TOKEN_EXPIRES_AT':str(int(time.time())+ttl)}
                    text=original.decode('utf-8')
                    for key,value in replacement.items():
                        pattern=re.compile(r'^(\s*(?:export\s+)?'+re.escape(key)+r'\s*=).*$',re.MULTILINE)
                        text,n=pattern.subn(lambda m:m[1]+"'"+value+"'",text)
                        if not n:text+=('' if text.endswith('\n') else '\n')+key+"='"+value+"'\n"
                    if path.read_bytes()!=original:raise AuthError('Credentials changed during refresh; stopped without overwrite')
                    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
                    backup=path.with_name('secrets.env.backup-refresh-'+stamp)
                    bfd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    with os.fdopen(bfd,'wb') as f:f.write(original);f.flush();os.fsync(f.fileno())
                    _atomic(path,text.encode())
                    _state(guard,{'version':_version(path),'phase':'ready','checked_at':time.time(),'expires_at':int(time.time())+ttl})
                    return access
                except Exception:
                    _state(guard,{'version':version,'phase':'needs_reauthorization','checked_at':time.time()})
                    raise AuthError('Baidu refresh failed or uncertain; stopped, no automatic retry') from None

def install_sdk_refresh(official):
    session=Session(official.access_token)
    if session.path is None:return
    cls=official.fileupload_api.FileuploadApi
    if getattr(cls,'_baidu_auto_refresh',False):return
    def wrap(fn):
        def call(self,*args,**kwargs):
            kwargs['access_token']=session.get()
            return fn(self,*args,**kwargs)
        return call
    for name in ('xpanfileprecreate','pcssuperfile2','xpanfilecreate'):
        setattr(cls,name,wrap(getattr(cls,name)))
    cls._baidu_auto_refresh=True
