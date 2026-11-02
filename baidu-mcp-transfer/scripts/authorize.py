#!/usr/bin/env python3
"""Interactive OAuth exchange; never prints or logs credential values."""
import argparse,datetime,getpass,json,os,re,resource,stat,sys,tempfile,time
import urllib.parse,urllib.request
from pathlib import Path,PurePosixPath

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise RuntimeError('Redirect refused')

def fetch(url,values,post=False):
    data=urllib.parse.urlencode(values).encode()
    request=urllib.request.Request(url,data=data if post else None,
        headers={'Content-Type':'application/x-www-form-urlencoded'})
    if not post:request=urllib.request.Request(url+'?'+data.decode())
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=30) as response:
        return json.load(response)

def main(argv=None):
    parser=argparse.ArgumentParser(description='隐藏输入应用密钥与授权码，验证后保存百度访问及刷新凭据')
    parser.add_argument('--secrets-file',type=Path,default=Path(os.environ.get('BAIDU_TUI_SECRETS_FILE',str(Path.home()/'.config/model-download/secrets.env'))))
    parser.add_argument('--check-dir',default='/',help='只读验证可访问的网盘目录')
    args=parser.parse_args(argv)
    check_dir=PurePosixPath(args.check_dir)
    if not check_dir.is_absolute() or '..' in check_dir.parts or str(check_dir)!=args.check_dir:
        parser.error('--check-dir 必须是规范的网盘绝对路径')
    if not sys.stdin.isatty():raise SystemExit('请在自己的交互式服务器终端运行。')
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    target=args.secrets_file.expanduser().absolute()
    if any(p.is_symlink() for p in (target.parent.parent,target.parent,target)):
        raise SystemExit('凭据路径不能是软链接。')
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if stat.S_IMODE(target.parent.stat().st_mode)!=0o700 or (target.exists() and stat.S_IMODE(target.stat().st_mode)!=0o600):
        raise SystemExit('目录和文件权限应分别为700和600。')
    app=getpass.getpass('AppKey（隐藏输入）: ').strip()
    secret=getpass.getpass('SecretKey（隐藏输入）: ').strip()
    code=getpass.getpass('刚取得的 code 授权码（隐藏输入）: ').strip()
    if not all(re.fullmatch(r'[A-Za-z0-9._~+/=-]+',v) for v in (app,secret,code)):
        raise SystemExit('未更新：请只输入字段值，不要粘贴整段网址。')
    print('正在兑换并验证授权，请稍候……')
    try:
        result=fetch('https://openapi.baidu.com/oauth/2.0/token',{
            'grant_type':'authorization_code','code':code,'client_id':app,
            'client_secret':secret,'redirect_uri':'oob'},post=True)
    except Exception:
        raise SystemExit('兑换请求失败；未重试、未修改配置。请检查网络，必要时重新获取授权码。') from None
    if result.get('error'):
        known={'invalid_grant','invalid_client','invalid_request','unsupported_grant_type','invalid_scope'}
        kind=result.get('error')
        raise SystemExit('兑换失败：'+(kind if kind in known else 'OAuth请求被拒绝')+'。配置未修改；请检查同一应用的密钥及新的授权码。')
    token=result.get('access_token');refresh=result.get('refresh_token')
    if not all(isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9._~+/=-]+',v) for v in (token,refresh)):
        raise SystemExit('未收到完整令牌，配置未修改。')
    try:
        check=fetch('https://pan.baidu.com/rest/2.0/xpan/file',{
            'method':'list','access_token':token,'dir':args.check_dir,'start':0,'limit':1})
    except Exception:
        raise SystemExit('兑换已返回，但网盘访问验证失败，配置未修改；请重新授权后重试。') from None
    if type(check.get('errno')) is not int or check['errno']!=0 or not isinstance(check.get('list'),list):
        errno=check.get('errno')
        raise SystemExit('网盘授权验证未通过，errno='+str(errno if type(errno) is int else '?')+'；配置未修改。请确认授权包含basic,netdisk且账号、应用权限正确。')
    values={'BAIDU_APP_KEY':app,'BAIDU_SECRET_KEY':secret,'BAIDU_REFRESH_TOKEN':refresh,
            'BAIDU_ACCESS_TOKEN':token,'BAIDU_NETDISK_ACCESS_TOKEN':token}
    if type(result.get('expires_in')) is int:
        values['BAIDU_TOKEN_EXPIRES_AT']=str(int(time.time())+result['expires_in'])
    existed=target.exists()
    original=target.read_bytes() if existed else b''
    text=original.decode('utf-8')
    for key,value in values.items():
        pattern=re.compile(r'^(\s*(?:export\s+)?'+re.escape(key)+r'\s*=).*$',re.MULTILINE)
        text,n=pattern.subn(lambda m:m[1]+"'"+value+"'",text)
        if not n:text+=('' if text.endswith('\n') else '\n')+key+"='"+value+"'\n"
    stamp=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d-%H%M%S-%f')
    backup=target.with_name('secrets.env.backup-'+stamp)
    if existed:
        fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f:f.write(original);f.flush();os.fsync(f.fileno())
    fd,tmp=tempfile.mkstemp(prefix='.baidu-oauth-',dir=target.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            os.fchmod(f.fileno(),0o600);f.write(text);f.flush();os.fsync(f.fileno())
        if target.exists()!=existed or (existed and target.read_bytes()!=original):raise SystemExit('配置已被其他进程修改，本次未覆盖；请重新授权后重试。')
        os.replace(tmp,target)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    print('授权验证通过。访问令牌、刷新令牌和应用凭据已保存，其他配置保留，原文件已备份。')
    print('后续通过此凭据文件启动的任务会按需自动续期。本命令不会启动或恢复文件传输。')

if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:raise SystemExit('已取消，未显示任何凭据。')
    except Exception:raise SystemExit('处理失败，异常详情已隐藏。请检查目录权限和网络。') from None
