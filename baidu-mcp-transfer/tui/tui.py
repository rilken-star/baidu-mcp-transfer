#!/usr/bin/env python3
"""Baidu Netdisk terminal dashboard: python3 tui.py or baidu-tui."""
import argparse
import curses
import json
import locale
import sys
import threading
import time
import unicodedata
from pathlib import Path

import core

PHASE = {'starting':'启动中','queued':'待启动','scanning':'整理清单','processing':'传输中',
         'verifying':'核验中','complete':'已完成','stopped':'已停止','needs_attention':'需处理',
         'failed':'失败','interrupted':'进程已退出','mcp_online':'在线'}
STAGE = {'hashing':'计算分片校验值','starting_download':'准备下载','starting':'计算分片校验值 / 准备上传','metadata':'查询网盘元信息',
         'sending_part':'正在发送分片','accepted_part':'分片已确认','finished':'文件传输结束，等待核验',
         'downloading':'正在接收文件','listing':'列出指定目录','complete':'完成','unknown_telemetry':'未接入进度'}


def width(s):
    return sum(2 if unicodedata.east_asian_width(c) in ('W','F') else 0 if unicodedata.combining(c) else 1 for c in s)


def clip(s, n):
    out='';used=0
    for c in core.clean(s):
        size=width(c)
        if used+size>max(0,n):break
        out+=c;used+=size
    return out


def size(n):
    for u in ('B','KiB','MiB','GiB','TiB'):
        if n<1024 or u=='TiB':return ('%d %s' if u=='B' else '%.2f %s')%(n,u)
        n/=1024


def duration(n):
    if n is None or n<0:return '--'
    n=int(n);h,n=divmod(n,3600);m,s=divmod(n,60)
    return '%dh %02dm'%(h,m) if h else '%dm %02ds'%(m,s)


def bar(n,total,length):
    ratio=min(1,max(0,n/total)) if total else 0
    filled=int(ratio*max(0,length))
    return '['+'#'*filled+'-'*(max(0,length)-filled)+']'


class App:
    def __init__(self):
        self.index=0;self.rates=core.Rates();self.notice='方向键选择任务；q 退出界面，后台传输继续运行。'
        self.busy=False

    def put(self, win,y,x,text,color=0):
        h,w=win.getmaxyx()
        if y<0 or y>=h or x>=w:return
        try:win.addstr(y,x,clip(str(text),w-x-1),color)
        except curses.error:pass

    def prompt(self, win, title, label, initial=''):
        text=initial
        win.timeout(-1)
        try:
            curses.curs_set(1)
            while True:
                win.erase();h,w=win.getmaxyx()
                self.put(win,2,3,title,curses.A_BOLD|curses.color_pair(1))
                self.put(win,4,3,label)
                visible=text
                while width(visible)>max(1,w-7):visible=visible[1:]
                self.put(win,6,3,visible,curses.A_REVERSE)
                self.put(win,9,3,'Enter 继续    Esc 取消    Ctrl+U 清空')
                win.move(6,min(w-2,3+width(visible)))
                win.refresh()
                c=win.get_wch()
                if c in ('\x1b','\x03'):return None
                if c in ('\n','\r',curses.KEY_ENTER):return text.strip()
                if c=='\x15':text=''
                elif c in (curses.KEY_BACKSPACE,'\x7f','\b'):text=text[:-1]
                elif isinstance(c,str) and c.isprintable():text+=c
        finally:
            curses.curs_set(0);win.timeout(250)

    def confirm(self,win,title,lines):
        win.erase();self.put(win,2,3,title,curses.A_BOLD|curses.color_pair(3))
        for i,line in enumerate(lines):self.put(win,4+i,3,line)
        self.put(win,7+len(lines),3,'y 确认    其他键取消')
        win.refresh();win.timeout(-1)
        try:return win.get_wch() in ('y','Y')
        finally:win.timeout(250)

    def create(self,win,kind):
        if kind=='upload':
            source=self.prompt(win,'新建上传','服务器上的源文件 / 目录：',str(Path.home() / 'models'))
            if not source:return
            target=self.prompt(win,'新建上传','网盘目标目录（不会覆盖同名文件）：','/百度网盘传输')
            if not target:return
            hint='目录中凭据文件将被排除；目录软链接不跟随。'
        else:
            source=self.prompt(win,'新建下载','网盘中的文件 / 目录路径：','/百度网盘传输/example.txt')
            if not source:return
            target=self.prompt(win,'新建下载','服务器本地保存目录：',str(Path.home() / 'Downloads/baidu'))
            if not target:return
            hint='保留已有文件；中断下载可按 c 使用临时文件继续。'
        if self.confirm(win,'确认'+('上传' if kind=='upload' else '下载'),['源：'+source,'目标：'+target,hint]):
            task=core.start_upload(source,target) if kind=='upload' else core.start_download(source,target)
            self.notice='任务已在后台启动：'+task['id'];self.index=max(0,len(core.jobs())-1)

    def action(self,fn):
        if self.busy:
            self.notice='前一操作尚未完成';return
        self.busy=True
        def work():
            try:fn();self.notice='操作请求已完成，请查看任务状态。'
            except core.SafeError as e:self.notice=str(e)
            except Exception:self.notice='操作失败，异常详情已隐藏；请检查任务状态。'
            finally:self.busy=False
        threading.Thread(target=work,daemon=True).start()

    def render(self,win,rows):
        win.erase();h,w=win.getmaxyx()
        self.put(win,0,2,'BAIDU NETDISK  /  传输中心',curses.A_BOLD|curses.color_pair(1))
        self.put(win,1,2,'本地任务状态 · 每秒刷新 · 凭据仅由后台读取 · '+time.strftime('%H:%M:%S'),curses.color_pair(4))
        if h<24 or w<82:
            self.put(win,4,2,'终端至少需要 82 列 × 24 行，请扩大窗口。')
            self.put(win,h-2,2,'q 退出（传输继续）');win.refresh();return
        active=sum(r['running'] for r in rows)
        total=sum(r['total'] for r in rows);done=sum(r['verified'] for r in rows)
        self.put(win,3,2,'任务 %d   活动 %d   已核验 %s / %s'%(len(rows),active,size(done),size(total)),curses.A_BOLD)
        self.put(win,5,2,'    方向  状态        完成率     速率          预计剩余     任务',curses.color_pair(4))
        maxrows=max(2,min(7,h-23))
        start=max(0,self.index-maxrows+1)
        for pos,r in enumerate(rows[start:start+maxrows],start):
            percent=100*r['accounted']/r['total'] if r['total'] else (100 if r['phase']=='complete' else 0)
            rate=r.get('rate',0);eta=(r['total']-r['accounted'])/rate if rate>0 else None
            line='%s   %s  %-6s   %6.2f%%   %-12s  %-12s  %s'%('>' if pos==self.index else ' ', '上传' if r['kind']=='upload' else '下载' if r['kind']=='download' else 'MCP ',PHASE.get(r['phase'],r['phase']),percent,size(rate)+'/s' if rate else '--',duration(eta),r['title'])
            self.put(win,6+pos-start,2,line,curses.A_REVERSE if pos==self.index else 0)
        top=7+maxrows
        self.put(win,top,2,'─'*(w-5),curses.color_pair(4))
        if rows:
            r=rows[self.index];bw=max(12,min(62,w-30))
            self.put(win,top+1,2,r['title']+'  ['+r['id']+']',curses.A_BOLD|curses.color_pair(1))
            self.put(win,top+3,2,'整体 '+bar(r['accounted'],r['total'],bw)+'  '+('%.2f%%'%(100*r['accounted']/r['total']) if r['total'] else '--'))
            self.put(win,top+4,2,'已核验 %s / %s   文件 %s / %s   待处理 %d'%(size(r['verified']),size(r['total']),r['files'],r['total_files'],r['pending']))
            self.put(win,top+5,2,'当前 '+bar(r['current_bytes'],r['current_size'],bw)+'  '+size(r['current_bytes'])+' / '+size(r['current_size']))
            self.put(win,top+6,2,'文件 '+Path(r['current']).name)
            self.put(win,top+7,2,'阶段 '+STAGE.get(r['stage'],r['stage'])+'  ·  '+('后台运行中' if r['running'] else PHASE.get(r['phase'],r['phase'])),curses.color_pair(2 if r['running'] else 3))
            self.put(win,top+8,2,'源   '+r['source'])
            self.put(win,top+9,2,'目标 '+r['target'])
            note=r['message'] or ('已确认分片计入整体进度，最终完成以逐文件核验为准。' if r['kind']=='upload' else '下载先写临时文件，完成后发布；不覆盖同名文件。' if r['kind']=='download' else 'MCP 未提供进度信息。')
            if r['api_error_code'] is not None:note+='  API错误码 '+str(r['api_error_code'])
            self.put(win,top+10,2,note,curses.color_pair(4))
        else:self.put(win,top+2,2,'没有任务。按 u 上传或 d 下载。')
        self.put(win,h-4,2,self.notice,curses.color_pair(3))
        self.put(win,h-2,2,'u 上传   d 下载   c 继续下载   s 停止   r 刷新   ↑↓ 选择   q 退出',curses.A_BOLD|curses.color_pair(1))
        win.refresh()

    def main(self,win):
        curses.curs_set(0);win.keypad(True);win.timeout(250)
        if curses.has_colors():
            curses.start_color();curses.use_default_colors()
            for i,c in enumerate((curses.COLOR_CYAN,curses.COLOR_GREEN,curses.COLOR_YELLOW,curses.COLOR_WHITE),1):curses.init_pair(i,c,-1)
        rows=[];updated=0;first_refresh=True
        while True:
            if time.monotonic()-updated>=1:
                rows=[core.snapshot(t) for t in core.jobs()]
                if first_refresh:
                    self.index=next((i for i, row in enumerate(rows) if row['running'] and row['kind'] in ('upload','download')),0)
                    first_refresh=False
                for r in rows:
                    if r['running'] and r['stage'] in ('sending_part','accepted_part','downloading'):
                        r['rate']=self.rates.update(r['id'],r['accounted'])
                    else:
                        self.rates.reset(r['id']);r['rate']=0
                self.index=min(self.index,max(0,len(rows)-1));updated=time.monotonic()
            self.render(win,rows)
            try:c=win.get_wch()
            except curses.error:continue
            try:
                if c in ('q','Q','\x03'):return
                if c in (curses.KEY_DOWN,'j'):self.index=min(max(0,len(rows)-1),self.index+1)
                elif c in (curses.KEY_UP,'k'):self.index=max(0,self.index-1)
                elif c in ('r','R',curses.KEY_RESIZE):updated=0
                elif c in ('u','d'):
                    self.create(win,'upload' if c=='u' else 'download');updated=0
                elif c=='c' and rows:
                    task=rows[self.index];self.action(lambda task=task:core.resume_download(task));updated=0
                elif c=='s' and rows:
                    task=rows[self.index]
                    warning='上传中断的当前文件须核验后处理，不自动重传。' if task['kind']=='upload' else '临时下载文件保留，可按 c 继续。'
                    if self.confirm(win,'停止所选任务？',[task['title'],warning]):
                        self.notice='正在发送停止请求…';self.action(lambda task=task:core.stop_task(task));updated=0
            except core.SafeError as e:self.notice=str(e)
            except Exception:self.notice='操作失败，异常详情已隐藏；请检查路径和任务状态。'


def main():
    locale.setlocale(locale.LC_ALL,'')
    parser=argparse.ArgumentParser(description='百度网盘上传 / 下载 TUI（退出界面不停止任务）')
    parser.add_argument('--status',action='store_true',help='输出任务状态 JSON，不打开界面')
    parser.add_argument('--upload',metavar='LOCAL',help='新建上传，须同时指定 --to 网盘目录')
    parser.add_argument('--download',metavar='REMOTE',help='新建下载，须同时指定 --to 本地目录')
    parser.add_argument('--to',metavar='DEST')
    args=parser.parse_args()
    if args.upload and args.download:parser.error('一次只新建一种传输任务')
    if args.upload or args.download:
        if not args.to:parser.error('缺少 --to')
        task=core.start_upload(args.upload,args.to) if args.upload else core.start_download(args.download,args.to)
        print(json.dumps(task,ensure_ascii=False));return
    if args.status:
        print(json.dumps([core.snapshot(t) for t in core.jobs()],ensure_ascii=False,indent=2));return
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error('交互界面需要终端；请用 ssh -t 登录，或使用 --status')
    curses.wrapper(App().main)

if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:pass
    except core.SafeError as error:print(str(error),file=sys.stderr);sys.exit(2)
    except Exception:print('TUI 未能启动；请检查终端 TERM / UTF-8 设置。异常详情已隐藏。',file=sys.stderr);sys.exit(2)
