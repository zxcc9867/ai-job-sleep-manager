"""Local Tk desktop view. The policy lives in Controller, not widgets."""
import ctypes
import json
import sqlite3
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from .job_view import JobCatalog, job_row, group_jobs, group_summary

from .integrations import install_hooks

BG='#0b111b'
CARD='#121d2a'
TEXT='#eef4fa'
MUTED='#a5b5c8'
TEAL='#7ee2c1'
BORDER='#26364a'
INSET='#0f1824'
AMBER='#f5c883'
MODES={
 'idle':('작업을 기다리고 있어요','AI 작업이 시작되면 자동으로 절전을 막습니다.'),
 'running':('AI가 작업 중이에요','작업이 진행되는 동안 컴퓨터를 깨어 있게 합니다.'),
 'countdown':('이제 쉴 준비를 해요','새 작업이나 마우스·키보드 입력이 있으면 대기 시간을 다시 계산합니다.'),
 'waiting':('입력 대기 작업이 남아 있어요','정상 종료는 모든 작업이 끝난 뒤에만 요청합니다. 지금은 Windows의 기존 절전 정책을 따릅니다.'),
 'paused':('자동 관리가 멈춰 있어요','현재는 Windows의 기존 전원 설정을 따릅니다.'),
 'unknown':('작업 상태를 확인 중이에요','확인되지 않은 작업을 완료로 판단하지 않습니다. 복구 유예 중입니다.'),
 'fault':('자동 관리에 확인이 필요해요','감지 복구 유예가 끝나 Windows의 기존 전원 설정을 따릅니다.'),
}
ACTIONS={'절전':'sleep','정상 종료':'shutdown'}
STATES={'running':'작업 중','complete':'완료','waiting':'입력 대기','unknown':'확인 필요','idle':'연결됨'}

class Window:
    def __init__(self,controller,args,command):
        self.controller,self.args,self.command=controller,args,command
        self.root=tk.Tk()
        self.root.title('AI Job Sleep Manager')
        height=min(900,max(560,self.root.winfo_screenheight()-80))
        self.root.geometry(f'{min(1180,self.root.winfo_screenwidth()-60)}x{height}')
        self.root.minsize(900,560)
        self.root.configure(bg=BG)
        self.closed=False
        self.last_presence=0
        self.rows=None
        self.job_rows={}
        self.detail_text=None
        self.labels=self.controller.store.labels()
        codex_home=Path(os.environ.get("CODEX_HOME",str(Path.home()/".codex")))
        self.catalog=JobCatalog(codex_home)
        self.admin=bool(ctypes.windll.shell32.IsUserAnAdmin()) if os.name=='nt' else False
        style=ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('Treeview',background=CARD,fieldbackground=CARD,foreground=TEXT,rowheight=42,borderwidth=0,font=('Malgun Gothic',10))
        style.layout('Treeview',[('Treeview.treearea',{'sticky':'nswe'})])
        style.configure('Treeview.Heading',background=INSET,foreground=MUTED,relief='flat',font=('Malgun Gothic',9))
        style.map('Treeview',background=[('selected','#23413f')],foreground=[('selected','#effff8')])
        style.configure('Vertical.TScrollbar',background=BORDER,troughcolor=INSET,bordercolor=INSET,arrowcolor=MUTED,lightcolor=BORDER,darkcolor=BORDER,arrowsize=12)
        style.configure('TCombobox',fieldbackground=INSET,foreground=TEXT,background=BORDER,arrowcolor=TEXT)
        style.map('TCombobox',fieldbackground=[('readonly',INSET)],foreground=[('readonly',TEXT)],selectbackground=[('readonly',INSET)],selectforeground=[('readonly',TEXT)])
        style.configure('TSpinbox',fieldbackground=BG,foreground=TEXT,arrowcolor=TEXT)
        self.build()
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.root.after(100,self.refresh)
        if args.smoke_ui:
            self.root.after(1800,self.capture_and_close)

    def label(self,parent,text,fg=TEXT,size=10,bold=False,**kw):
        return tk.Label(parent,text=text,bg=parent.cget('bg'),fg=fg,font=('Malgun Gothic',size,'bold' if bold else 'normal'),**kw)

    def button(self,parent,text,command,primary=False):
        color=TEAL if primary else BORDER
        button=tk.Button(parent,text=text,command=command,bg=color,fg=BG if primary else TEXT,
             activebackground='#a5eed8' if primary else '#34485f',activeforeground=BG if primary else TEXT,
             relief='flat',bd=0,padx=14,pady=8,cursor='hand2',font=('Malgun Gothic',9,'bold'),
             highlightthickness=1,highlightbackground=color,highlightcolor=TEAL)
        button.bind('<Enter>',lambda event:button.config(bg='#a5eed8' if primary else '#34485f'))
        button.bind('<Leave>',lambda event:button.config(bg=color))
        return button

    def build(self):
        # Keep all controls reachable on scaled displays without hiding the selected task.
        self.page_canvas=tk.Canvas(self.root,bg=BG,highlightthickness=0)
        page_scroll=ttk.Scrollbar(self.root,orient='vertical',command=self.page_canvas.yview)
        self.page_canvas.configure(yscrollcommand=page_scroll.set)
        page_scroll.pack(side='right',fill='y'); self.page_canvas.pack(side='left',fill='both',expand=True)
        shell=tk.Frame(self.page_canvas,bg=BG,padx=24,pady=20)
        self.page_window=self.page_canvas.create_window((0,0),window=shell,anchor='nw')
        shell.bind('<Configure>',lambda event:self.page_canvas.configure(scrollregion=self.page_canvas.bbox('all')))
        self.page_canvas.bind('<Configure>',self.resize_page)
        self.root.bind('<MouseWheel>',self.scroll_page,add='+')

        top=tk.Frame(shell,bg=BG); top.pack(fill='x',pady=(0,18))
        mark=tk.Canvas(top,width=36,height=36,bg=BG,highlightthickness=0); mark.pack(side='left',padx=(0,12))
        mark.create_oval(3,3,32,32,fill=TEAL,outline='')
        mark.create_oval(13,0,37,24,fill=BG,outline='')
        brand=tk.Frame(top,bg=BG); brand.pack(side='left')
        self.label(brand,'작업 모니터',size=20,bold=True,anchor='w').pack(fill='x')
        self.label(brand,'AI JOB SLEEP MANAGER',MUTED,8,anchor='w').pack(fill='x')
        self.pause=self.button(top,'일시 중지',self.toggle,True); self.pause.pack(side='right')
        self.settings_button=self.button(top,'전원 설정  ▾',self.toggle_settings); self.settings_button.pack(side='right',padx=8)
        badge='● 관찰 모드' if self.controller.power.dry_run else '● 자동 관리'
        self.mode_badge=self.label(top,badge,AMBER if self.controller.power.dry_run else TEAL,9,padx=12); self.mode_badge.pack(side='right')

        overview=tk.Frame(shell,bg=CARD,highlightbackground=BORDER,highlightthickness=1,padx=18,pady=14)
        overview.pack(fill='x',pady=(0,16))
        line=tk.Frame(overview,bg=CARD); line.pack(fill='x')
        self.status_label=self.label(line,'작업 상태를 불러오는 중',TEAL,13,True,anchor='w'); self.status_label.pack(side='left')
        self.description=self.label(overview,'',MUTED,9,anchor='w',wraplength=960,justify='left'); self.description.pack(fill='x',pady=(4,12))
        metrics=tk.Frame(overview,bg=CARD); metrics.pack(fill='x')
        self.metric_values=[]; self.metric_titles=[]
        for index,(title,hint,color) in enumerate((('진행 중인 작업','메인 작업',TEAL),('진행 중인 하위 작업','AI가 나눠 맡긴 작업','#9fbaff'),('절전까지 남은 시간','작업 종료 후 대기',TEXT))):
            metrics.columnconfigure(index,weight=1,uniform='metric')
            frame=tk.Frame(metrics,bg=CARD); frame.grid(row=0,column=index,sticky='ew',padx=(0,18))
            heading=self.label(frame,title,MUTED,9,anchor='w'); heading.pack(fill='x'); self.metric_titles.append(heading)
            values=tk.Frame(frame,bg=CARD); values.pack(fill='x')
            value=self.label(values,'—',color,25,True,anchor='w'); value.pack(side='left'); self.metric_values.append(value)
            self.label(values,hint,MUTED,8).pack(side='left',padx=(10,0),pady=(12,0))

        self.settings_open=False
        self.settings_panel=tk.Frame(shell,bg=INSET,padx=16,pady=12,highlightbackground=BORDER,highlightthickness=1)
        settings=tk.Frame(self.settings_panel,bg=INSET); settings.pack(fill='x')
        self.label(settings,'작업이 끝나면',size=10,bold=True).pack(side='left')
        self.minutes=tk.StringVar(value=str(int(self.controller.engine.delay/60)))
        self.spin=ttk.Spinbox(settings,from_=1,to=120,width=5,textvariable=self.minutes,font=('Malgun Gothic',11),justify='center'); self.spin.pack(side='left',padx=10)
        self.label(settings,'분 후',MUTED).pack(side='left')
        self.action=tk.StringVar(value='정상 종료' if self.controller.engine.action=='shutdown' else '절전')
        self.action_select=ttk.Combobox(settings,textvariable=self.action,values=list(ACTIONS),state='readonly',width=10,font=('Malgun Gothic',10)); self.action_select.pack(side='left',padx=10)
        self.button(settings,'설정 저장',self.apply_delay).pack(side='left')
        self.button(settings,'진단',self.diagnose).pack(side='right')
        self.button(settings,'연결 해제',self.disconnect).pack(side='right',padx=8)
        self.button(settings,'도구 연결',self.connect).pack(side='right')
        self.policy_note=self.label(self.settings_panel,'',MUTED,9,anchor='w',wraplength=980,justify='left'); self.policy_note.pack(fill='x',pady=(10,0))
        if not self.admin and not self.controller.power.dry_run:
            self.button(self.settings_panel,'관리자 권한으로 열기',self.elevate).pack(anchor='e',pady=(8,0))

        self.workspace=tk.Frame(shell,bg=BG); self.workspace.pack(fill='both',expand=True)
        table_header=tk.Frame(self.workspace,bg=BG); table_header.pack(fill='x')
        self.jobs_heading=self.label(table_header,'남은 작업',size=11,bold=True); self.jobs_heading.pack(side='left')
        self.show_complete=tk.BooleanVar(value=False)
        tk.Checkbutton(table_header,text='완료한 작업도 보기',variable=self.show_complete,command=self.render_jobs,bg=BG,fg=MUTED,selectcolor=CARD,activebackground=BG,activeforeground=TEXT,font=('Malgun Gothic',9)).pack(side='right')
        self.jobs_summary=self.label(self.workspace,'',MUTED,9,anchor='w'); self.jobs_summary.pack(fill='x',pady=(5,12))
        panels=tk.Frame(self.workspace,bg=BG); panels.pack(fill='both',expand=True)
        panels.columnconfigure(0,weight=55,uniform='panels'); panels.columnconfigure(1,weight=45,uniform='panels')
        panels.rowconfigure(0,weight=1)
        left=tk.Frame(panels,bg=CARD,highlightbackground=BORDER,highlightthickness=1)
        left.grid(row=0,column=0,sticky='nsew',padx=(0,12))
        left_header=tk.Frame(left,bg=CARD,padx=14,pady=12); left_header.pack(fill='x')
        self.label(left_header,'작업 목록',size=11,bold=True).pack(side='left')
        self.label(left_header,'▾ 하위 작업 펼치기',MUTED,8).pack(side='right')
        table_frame=tk.Frame(left,bg=CARD); table_frame.pack(fill='both',expand=True,padx=1)
        self.table=ttk.Treeview(table_frame,columns=('tool','latest_request','state','current_activity'),displaycolumns=('latest_request','state'),show='tree headings',height=6)
        for col,title,width in [('#0','대화 · 하위 작업',225),('tool','도구',85),('latest_request','최근 지시',175),('state','상태',105),('current_activity','현재 활동',220)]:
            self.table.heading(col,text=title,anchor='w'); self.table.column(col,width=width,minwidth=65,anchor='w')
        scroll=ttk.Scrollbar(table_frame,orient='vertical',command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side='left',fill='both',expand=True); scroll.pack(side='right',fill='y')
        for category,color in [('running',TEAL),('waiting',AMBER),('unknown','#f3a8a8'),('complete',MUTED),('idle',MUTED)]: self.table.tag_configure(category,foreground=color)
        self.table.bind('<<TreeviewSelect>>',self.show_job_details)
        self.label(left,'작업을 선택하면 오른쪽에서 진행 상황을 확인합니다.',MUTED,8,anchor='w',padx=14,pady=10).pack(fill='x')

        right=tk.Frame(panels,bg=CARD,highlightbackground=BORDER,highlightthickness=1)
        right.grid(row=0,column=1,sticky='nsew')
        detail_header=tk.Frame(right,bg=CARD,padx=14,pady=10); detail_header.pack(fill='x')
        self.label(detail_header,'지금 무슨 일을 하나요?',size=11,bold=True).pack(side='left')
        self.detail_badge=self.label(detail_header,'선택 없음',MUTED,9); self.detail_badge.pack(side='right')
        self.details=tk.Text(right,width=1,height=15,bg=CARD,fg=TEXT,relief='flat',wrap='word',font=('Malgun Gothic',10),padx=16,pady=12,state='disabled',spacing1=3,spacing3=5,insertbackground=TEAL)
        detail_scroll=ttk.Scrollbar(right,orient='vertical',command=self.details.yview)
        self.details.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side='right',fill='y'); self.details.pack(fill='both',expand=True)
        for tag,color,size,bold in [('heading',TEAL,10,True),('request',TEXT,12,True),('muted',MUTED,9,False)]:
            self.details.tag_configure(tag,foreground=color,font=('Malgun Gothic',size,'bold' if bold else 'normal'))
        detail_footer=tk.Frame(self.workspace,bg=BG); detail_footer.pack(fill='x',pady=(10,0))
        self.notice=self.label(detail_footer,'실제 기록에 기반해 표시합니다. 전원 설정에서 도구 연결과 진단을 확인하세요.',MUTED,8,anchor='w',wraplength=760,justify='left'); self.notice.pack(side='left',fill='x',expand=True)
        self.button(detail_footer,'선택한 작업 이름 지정',self.rename_job).pack(side='right')
        self.set_detail('왼쪽에서 작업을 선택하세요.\n최근 지시와 AI의 진행 설명이 여기에 표시됩니다.')

    def resize_page(self,event):
        self.page_canvas.itemconfigure(self.page_window,width=event.width)
        for name,padding in (('description',100),('policy_note',100),('notice',260)):
            if hasattr(self,name): getattr(self,name).config(wraplength=max(300,event.width-padding))

    def toggle_settings(self):
        self.settings_open=not self.settings_open
        if self.settings_open:
            self.settings_panel.pack(fill='x',before=self.workspace,pady=(0,16))
        else: self.settings_panel.pack_forget()
        self.settings_button.config(text='전원 설정  ▴' if self.settings_open else '전원 설정  ▾')

    def scroll_page(self,event):
        if isinstance(event.widget,(ttk.Treeview,tk.Text,ttk.Spinbox,ttk.Combobox)): return
        if event.delta: self.page_canvas.yview_scroll(-1 if event.delta>0 else 1,'units')

    def refresh(self):
        if self.closed: return
        try:
            status=self.controller.tick()
            if time.monotonic()-self.last_presence>5:
                self.last_presence=time.monotonic()
                self.controller.set_presence(self.controller.power.process_names())
                status=self.controller.tick()
            title,description=MODES[status.mode]
            shutdown=self.controller.engine.action=='shutdown'
            self.metric_titles[2].config(text='종료 요청까지 남은 시간' if shutdown else '절전까지 남은 시간')
            self.policy_note.config(text=('정상 종료: 입력·승인 대기도 끝나야 합니다. 저장 확인 앱은 강제로 닫지 않으며, 종료 요청 후 자동 관리는 중지됩니다.' if shutdown else '절전: 모든 작업이 완료되거나 입력 대기일 때 시간을 셉니다. 새 작업이 시작되면 예약을 취소합니다.'))
            if shutdown and status.mode=='countdown':
                title='정상 종료를 준비하고 있어요'
                description='새 작업이나 사용자 입력이 있으면 시간을 다시 계산합니다. 앱을 강제로 닫지 않습니다.'
            if self.controller.error:
                title='종료 요청을 보류했어요' if shutdown else '절전을 보류했어요'
                description=('진단에서 원인을 확인한 뒤 자동 관리를 다시 켜 주세요. 자동으로 재시도하지 않습니다.' if shutdown else '권한 또는 연결 상태를 확인하세요. 자세한 이유는 진단에서 볼 수 있습니다.')
            elif self.controller.shutdown_notice:
                title='정상 종료 요청을 처리했어요'
                description=self.controller.shutdown_notice+' 다시 사용하려면 자동 관리 재개를 누르세요.'
            elif not self.admin and not self.controller.power.dry_run:
                description+='  자동 전원 전환에는 관리자 권한 확인이 필요합니다.'
            if self.controller.power.dry_run:
                description='관찰 모드에서는 작업 상태와 예약만 확인합니다. 실제 절전 방지·절전·종료는 실행하지 않습니다.'
            status_color=AMBER if status.mode in ('waiting','paused') else '#f3a8a8' if status.mode in ('unknown','fault') or self.controller.error else TEAL
            self.status_label.config(text=title,fg=status_color)
            if not self.controller.power.dry_run:
                self.mode_badge.config(text='● 자동 관리' if self.controller.engine.enabled else '○ 관리 중지',fg=TEAL if self.controller.engine.enabled else AMBER)
            self.description.config(text=description)
            self.metric_values[2].config(text='—' if status.remaining is None else f'{status.remaining//60:02d}:{status.remaining%60:02d}')
            self.pause.config(text='일시 중지' if self.controller.engine.enabled else '자동 관리 재개')
            self.render_jobs()
        except Exception as exc:
            self.notice.config(text='상태 확인 오류: '+str(exc),fg='#f0b786')
        self.root.after(500,self.refresh)

    def render_jobs(self):
        jobs=list(self.controller.engine.jobs.values())
        metadata=self.catalog.lookup(jobs)
        rows=[]
        for job in jobs:
            key=job.provider+':'+job.session_id
            info=dict(getattr(self.controller.reader,'details',{}).get(job.session_id,{})) if job.provider=='codex' else {}
            info.update(metadata.get(key,{}))
            if key in self.labels: info['label']=self.labels[key]
            rows.append(job_row(job,info,self.controller.engine.action))
        rows=group_jobs(rows)
        counts=group_summary(rows)
        self.metric_values[0].config(text=str(counts['running']))
        self.metric_values[1].config(text=str(counts['child_running']))
        self.jobs_heading.config(text=f"진행 중인 작업 {counts['running']}개 · 하위 작업 {counts['child_running']}개")
        self.jobs_summary.config(text=f"응답 필요: 작업 {counts['waiting']} · 하위 {counts['child_waiting']}   |   확인 필요: 작업 {counts['unknown']} · 하위 {counts['child_unknown']}   |   완료: 작업 {counts['complete']} · 하위 {counts['child_complete']}")
        previous_keys=set(self.job_rows)
        self.job_rows={row['key']:row for row in rows}
        visible=[row for row in rows if row['pending'] or self.show_complete.get()]
        wanted={row['key'] for row in visible}
        selected=self.table.selection()
        scroll_position=self.table.yview()[0]
        # Detach first so deleting a missing parent cannot delete a surviving child.
        for key in previous_keys:
            if self.table.exists(key): self.table.detach(key)
        for key in previous_keys-wanted:
            if self.table.exists(key): self.table.delete(key)
        for row in visible:
            values=tuple(row[field] for field in ('tool','latest_request','state','current_activity'))
            text=row['title']
            if row['child_count']: text+=f"  · 하위 {row['child_count']}개"
            if row['is_child'] and not row['parent_key']: text+='  (부모 미확인)'
            if self.table.exists(row['key']):
                self.table.item(row['key'],text=text,values=values,tags=(row['category'],))
            else:
                self.table.insert('',tk.END,iid=row['key'],text=text,values=values,tags=(row['category'],),open=True)
            self.table.move(row['key'],row['parent_key'],tk.END)
        self.table.yview_moveto(scroll_position)
        remaining_selection=[key for key in selected if key in wanted]
        if remaining_selection: self.table.selection_set(remaining_selection)
        if visible and not self.table.selection(): self.table.selection_set(visible[0]['key'])
        if not visible:
            self.detail_badge.config(text='선택 없음',fg=MUTED)
            self.set_detail('남은 작업이 없습니다. 완료된 작업은 위의 “완료·연결된 작업도 보기”에서 확인하세요.' if rows else 'AI 작업이 감지되면 제목과 상태가 표시됩니다. 도구 연결 후 새 작업을 시작해 주세요.')
        else:
            self.show_job_details()

    def set_detail(self,text):
        if text==self.detail_text: return
        self.detail_text=text
        self.details.config(state='normal')
        self.details.delete('1.0',tk.END); self.details.insert('1.0',text)
        for line_number,line in enumerate(text.splitlines(),1):
            tag=('request' if line.startswith('최근 지시:') else 'heading' if line.startswith(('현재 활동:','AI의 최근 설명:','최근 실행 흐름','AI가 공유한 계획:')) else 'muted' if line.startswith(('관측 시점:','설명 기록 시각:','대화 제목:','세션 ID:')) else None)
            if tag: self.details.tag_add(tag,f'{line_number}.0',f'{line_number}.end')
        self.details.config(state='disabled')

    def show_job_details(self,event=None):
        selected=self.table.selection()
        row=self.job_rows.get(selected[0]) if selected else None
        if not row: return
        self.detail_badge.config(text=row['state'],fg={'running':TEAL,'waiting':AMBER,'unknown':'#f3a8a8'}.get(row['category'],MUTED))
        lines=['최근 지시: '+row['latest_request'],
               '현재 활동: '+row['current_activity'],
               '관측 시점: '+row['activity_updated']]
        if row['commentary']:
            lines.extend(['', 'AI의 최근 설명:',row['commentary']])
            if row['commentary_at']: lines.append('설명 기록 시각: '+row['commentary_at'])
        else:
            lines.append('AI의 최근 설명: 이 지시에 대한 공개 진행 설명이 아직 없습니다.')
        if row['activity_history']:
            lines.extend(['', '최근 실행 흐름 (관측된 도구 요청·응답):'])
            for item in row['activity_history'][-4:]: lines.append('  · '+item['text'])
        lines.extend(['', '대화 제목: '+row['title'],row['tool']+'  ·  '+row['project']+'  ·  '+row['state']])
        if row['relation']: lines.append(row['relation'])
        if row['child_count']: lines.append(f"이 작업에 연결된 하위 작업 {row['child_count']}개 · 목록에서 각 상태를 확인하세요.")
        lines.append(row['reason'])
        if row['plan']:
            lines.append(f"AI가 공유한 계획: {row['plan_done']}/{row['plan_total']}단계 완료")
            for step in sorted(row['plan'],key=lambda step:{'in_progress':0,'pending':1,'completed':2}[step['status']]):
                marker={'completed':'완료','in_progress':'진행','pending':'예정'}[step['status']]
                lines.append('['+marker+'] '+step['step'])
        elif row['category']=='running':
            lines.append('공유된 계획이 생기면 예정·완료 단계도 함께 표시합니다.')
        lines.append('세션 ID: '+row['session_id'])
        self.set_detail('\n'.join(lines))

    def rename_job(self):
        selected=self.table.selection()
        row=self.job_rows.get(selected[0]) if selected else None
        if not row:
            messagebox.showinfo('작업 선택','이름을 붙일 작업을 먼저 선택해 주세요.',parent=self.root)
            return
        label=simpledialog.askstring('작업 이름 지정','알아보기 쉬운 이름을 입력하세요. 비우면 원래 이름으로 돌아갑니다.',initialvalue=self.labels.get(row['key'],row['title']),parent=self.root)
        if label is None: return
        try:
            provider,session=row['key'].split(':',1)
            self.controller.store.set_label(provider,session,label)
            self.labels=self.controller.store.labels()
            self.render_jobs()
        except (ValueError,OSError,sqlite3.Error) as exc:
            messagebox.showerror('작업 이름 저장',str(exc),parent=self.root)

    def apply_delay(self):
        try:
            minutes=int(self.minutes.get())
            self.controller.set_delay(minutes*60)
            self.controller.set_action(ACTIONS[self.action.get()])
            self.notice.config(text=f'작업 완료 후 {minutes}분 뒤 {self.action.get()}하도록 저장했습니다.',fg=TEAL)
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
            data['completion_action']=self.controller.engine.action
            data['shutdown_notice']=self.controller.shutdown_notice
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


