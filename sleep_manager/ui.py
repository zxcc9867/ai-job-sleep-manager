"""Local Tk desktop view. The policy lives in Controller, not widgets."""
import ctypes
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox

from .integrations import install_hooks

BG='#10181d'
CARD='#18242b'
TEXT='#e8f2f1'
MUTED='#9badb4'
TEAL='#78dfc0'
BORDER='#2d3e46'
MODES={
 'idle':('작업을 기다리고 있어요','AI 작업이 시작되면 자동으로 절전을 막습니다.'),
 'running':('AI가 작업 중이에요','작업이 진행되는 동안 컴퓨터를 깨어 있게 합니다.'),
 'countdown':('이제 쉴 준비를 해요','새 작업이나 마우스·키보드 입력이 있으면 대기 시간을 다시 계산합니다.'),
 'paused':('자동 관리가 멈춰 있어요','현재는 Windows의 기존 전원 설정을 따릅니다.'),
 'unknown':('작업 상태를 확인 중이에요','확인되지 않은 작업을 완료로 판단하지 않습니다. 복구 유예 중입니다.'),
 'fault':('자동 관리에 확인이 필요해요','감지 복구 유예가 끝나 Windows의 기존 전원 설정을 따릅니다.'),
}
STATES={'running':'작업 중','complete':'완료','waiting':'입력 대기','unknown':'확인 필요','idle':'연결됨'}

class Window:
    def __init__(self,controller,args,command):
        self.controller,self.args,self.command=controller,args,command
        self.root=tk.Tk()
        self.root.title('AI Job Sleep Manager')
        self.root.geometry('990x800')
        self.root.minsize(860,780)
        self.root.configure(bg=BG)
        self.closed=False
        self.last_presence=0
        self.rows=None
        self.admin=bool(ctypes.windll.shell32.IsUserAnAdmin()) if os.name=='nt' else False
        style=ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('Treeview',background=CARD,fieldbackground=CARD,foreground=TEXT,rowheight=34,borderwidth=0,font=('Malgun Gothic',10))
        style.configure('Treeview.Heading',background=BORDER,foreground=MUTED,relief='flat',font=('Malgun Gothic',9))
        style.map('Treeview',background=[('selected','#294a47')])
        style.configure('TSpinbox',fieldbackground=BG,foreground=TEXT,arrowcolor=TEXT)
        self.build()
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.root.after(100,self.refresh)
        if args.smoke_ui:
            self.root.after(1800,self.capture_and_close)

    def label(self,parent,text,fg=TEXT,size=10,bold=False,**kw):
        return tk.Label(parent,text=text,bg=parent.cget('bg'),fg=fg,font=('Malgun Gothic',size,'bold' if bold else 'normal'),**kw)

    def button(self,parent,text,command,primary=False):
        return tk.Button(parent,text=text,command=command,bg=TEAL if primary else BORDER,fg=BG if primary else TEXT,
             activebackground='#a5eed8',activeforeground=BG,relief='flat',bd=0,padx=18,pady=10,cursor='hand2',font=('Malgun Gothic',10,'bold' if primary else 'normal'))

    def build(self):
        shell=tk.Frame(self.root,bg=BG,padx=30,pady=22); shell.pack(fill='both',expand=True)
        top=tk.Frame(shell,bg=BG); top.pack(fill='x')
        self.label(top,'◉  AI JOB SLEEP MANAGER',TEAL,11,True).pack(side='left')
        badge='관찰 모드 · 실제 절전 없음' if self.controller.power.dry_run else '로컬 자동 관리'
        self.label(top,badge,MUTED,9).pack(side='right')
        self.label(shell,'작업이 끝나면, 컴퓨터도 쉬도록.',size=22,bold=True,anchor='w').pack(fill='x',pady=(20,4))
        self.label(shell,'Codex와 Claude Code의 작업을 한곳에서 살펴봅니다.',MUTED,10,anchor='w').pack(fill='x',pady=(0,18))

        hero=tk.Frame(shell,bg=CARD,padx=22,pady=18,highlightbackground=BORDER,highlightthickness=1); hero.pack(fill='x')
        self.status_label=self.label(hero,'작업 상태를 불러오는 중',size=16,bold=True,anchor='w'); self.status_label.pack(fill='x')
        self.description=self.label(hero,'',MUTED,10,anchor='w',wraplength=850,justify='left'); self.description.pack(fill='x',pady=(6,12))
        metrics=tk.Frame(hero,bg=CARD); metrics.pack(fill='x')
        self.metric_values=[]
        for title in ('실행 중인 작업','사용자 입력 대기','절전까지 남은 시간'):
            frame=tk.Frame(metrics,bg=CARD); frame.pack(side='left',fill='x',expand=True)
            self.label(frame,title,MUTED,9,anchor='w').pack(fill='x')
            value=self.label(frame,'—',TEAL,24,True,anchor='w'); value.pack(fill='x')
            self.metric_values.append(value)

        settings=tk.Frame(shell,bg=BG); settings.pack(fill='x',pady=16)
        self.label(settings,'모든 작업이 멈추면',MUTED).pack(side='left')
        self.minutes=tk.StringVar(value=str(int(self.controller.engine.delay/60)))
        self.spin=ttk.Spinbox(settings,from_=1,to=120,width=5,textvariable=self.minutes,font=('Malgun Gothic',12),justify='center')
        self.spin.pack(side='left',padx=10)
        self.label(settings,'분 후 절전',MUTED).pack(side='left')
        self.button(settings,'적용',self.apply_delay).pack(side='left',padx=10)
        self.pause=self.button(settings,'일시 중지',self.toggle,True); self.pause.pack(side='right')

        table_header=tk.Frame(shell,bg=BG); table_header.pack(fill='x',pady=(0,8))
        self.label(table_header,'현재 작업',size=11,bold=True).pack(side='left')
        self.label(table_header,'작업 본문은 저장하지 않습니다',MUTED,9).pack(side='right')
        table_frame=tk.Frame(shell,bg=CARD); table_frame.pack(fill='both',expand=True)
        self.table=ttk.Treeview(table_frame,columns=('tool','session','state'),show='headings',height=5)
        for col,title,width in [('tool','도구',150),('session','세션',410),('state','상태',130)]:
            self.table.heading(col,text=title,anchor='w'); self.table.column(col,width=width,anchor='w')
        scroll=ttk.Scrollbar(table_frame,orient='vertical',command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side='left',fill='both',expand=True); scroll.pack(side='right',fill='y')
        self.notice=self.label(shell,'도구 연결 후 AI 앱·터미널 세션을 다시 시작하세요. 기존 작업은 확인 필요로 표시될 수 있습니다.',MUTED,9,anchor='w',wraplength=910,justify='left')
        self.notice.pack(fill='x',pady=(10,8))
        footer=tk.Frame(shell,bg=BG); footer.pack(fill='x')
        self.button(footer,'도구 연결',self.connect).pack(side='left')
        self.button(footer,'연결 해제',self.disconnect).pack(side='left',padx=8)
        self.button(footer,'진단',self.diagnose).pack(side='left')
        if not self.admin and not self.controller.power.dry_run:
            self.button(footer,'관리자 권한으로 열기',self.elevate).pack(side='right')
        self.label(shell,'Windows 전원 설정은 바꾸지 않습니다. 창을 닫으면 이 앱의 절전 방지를 해제합니다.',MUTED,9,anchor='w').pack(fill='x',pady=(13,0))

    def refresh(self):
        if self.closed: return
        try:
            status=self.controller.tick()
            if time.monotonic()-self.last_presence>5:
                self.last_presence=time.monotonic()
                self.controller.set_presence(self.controller.power.process_names())
                status=self.controller.tick()
            title,description=MODES[status.mode]
            if self.controller.error:
                title='절전을 보류했어요'
                description='권한 또는 연결 상태를 확인하세요. 자세한 이유는 진단에서 볼 수 있습니다.'
            elif not self.admin and not self.controller.power.dry_run:
                description+='  자동 절전 전환에는 관리자 권한 확인이 필요합니다.'
            self.status_label.config(text=title)
            self.description.config(text=description)
            values=[str(status.running),str(status.waiting),'—' if status.remaining is None else f'{status.remaining//60:02d}:{status.remaining%60:02d}']
            for label,value in zip(self.metric_values,values): label.config(text=value)
            self.pause.config(text='일시 중지' if self.controller.engine.enabled else '자동 관리 재개')
            jobs=sorted(self.controller.engine.jobs.values(),key=lambda j:(not j.active,-j.updated))
            rows=[('Codex' if j.provider=='codex' else 'Claude Code',j.session_id[:38], '하위 작업 실행 중' if j.active and j.state!='running' else STATES[j.state]) for j in jobs]
            if rows!=self.rows:
                self.rows=rows
                self.table.delete(*self.table.get_children())
                for row in rows: self.table.insert('',tk.END,values=row)
                if not rows: self.table.insert('',tk.END,values=('—','연결된 AI 작업을 기다리고 있습니다','대기'))
        except Exception as exc:
            self.notice.config(text='상태 확인 오류: '+str(exc),fg='#f0b786')
        self.root.after(500,self.refresh)

    def apply_delay(self):
        try:
            minutes=int(self.minutes.get())
            self.controller.set_delay(minutes*60)
            self.notice.config(text=f'대기 시간을 {minutes}분으로 저장했습니다.',fg=TEAL)
        except (ValueError,OSError) as exc:
            messagebox.showerror('설정 확인',str(exc),parent=self.root)

    def toggle(self):
        try: self.controller.set_enabled(not self.controller.engine.enabled)
        except Exception as exc: messagebox.showerror('설정 오류',str(exc),parent=self.root)

    def connect(self):
        try:
            if os.environ.get('CLAUDE_CONFIG_DIR') or os.environ.get('CODEX_HOME'):
                raise RuntimeError('사용자 지정 AI 설정 경로가 감지되었습니다. 기본 경로 연결을 자동 적용하지 않습니다.')
            backups=install_hooks(Path.home(),self.command)
            self.notice.config(text='연결 설정 저장 완료. AI 앱·터미널 세션을 다시 시작하고 필요한 hook 신뢰 검토를 완료하세요.',fg=TEAL)
            messagebox.showinfo('도구 연결',f'기존 설정을 보존하여 Codex·Claude Code 연결을 추가했습니다.\n백업 {len(backups)}개 생성.\n\nAI 앱·터미널 세션을 다시 시작해 주세요.\n실제 이벤트가 수신되어야 감지 여부를 확인할 수 있습니다.',parent=self.root)
        except Exception as exc:
            messagebox.showerror('연결 설정 오류',str(exc),parent=self.root)

    def disconnect(self):
        try:
            install_hooks(Path.home(),self.command,remove=True)
            self.controller.set_enabled(False)
            self.notice.config(text='이 실행 경로의 연결을 해제하고 자동 관리를 중지했습니다.',fg=MUTED)
        except Exception as exc:
            messagebox.showerror('연결 해제 오류',str(exc),parent=self.root)

    def diagnose(self):
        try:
            data=self.controller.power.diagnostics()
            data['administrator']=self.admin
            data['last_error']=self.controller.error
            data['codex_log_status']=self.controller.reader.last_error or '로컬 이벤트 형식 관측 중 (버전 의존)'
            data['events_observed']=sorted(self.controller.live_providers)
            data['data_directory']=str(self.controller.store.root)
            data['recent_events']=self.controller.recent_events
            messagebox.showinfo('진단',json.dumps(data,ensure_ascii=False,indent=2),parent=self.root)
        except Exception as exc:
            messagebox.showerror('진단 오류',str(exc),parent=self.root)

    def elevate(self):
        if getattr(sys,'frozen',False):
            executable=sys.executable
            argv=[]
        else:
            executable=sys.executable
            argv=[str(Path(__file__).resolve().parents[1]/'app.py')]
        argv+=['--data-dir',str(self.args.data_dir)]
        # Relaunch after the existing process exits, so the single-instance lock is free.
        quoted=' '.join("'"+x.replace("'","''")+"'" for x in [executable,*argv])
        script=f"Start-Sleep -Seconds 2; & {quoted}"
        encoded=__import__('base64').b64encode(script.encode('utf-16le')).decode('ascii')
        shell=ctypes.windll.shell32
        shell.ShellExecuteW.restype=ctypes.c_void_p
        result=shell.ShellExecuteW(None,'runas','powershell.exe','-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand '+encoded,None,0)
        if result and result>32: self.close()
        else: messagebox.showinfo('관리자 권한','관리자 권한 실행이 취소되었습니다.',parent=self.root)

    def capture_and_close(self):
        if self.args.screenshot:
            try:
                from PIL import ImageGrab
                self.root.update()
                self.args.screenshot.parent.mkdir(parents=True,exist_ok=True)
                x,y=self.root.winfo_rootx(),self.root.winfo_rooty()
                ImageGrab.grab(bbox=(x,y,x+self.root.winfo_width(),y+self.root.winfo_height())).save(self.args.screenshot)
            except Exception as exc:
                if sys.stderr: print(str(exc),file=sys.stderr)
        self.close()

    def close(self):
        if self.closed: return
        self.closed=True
        self.controller.close()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


