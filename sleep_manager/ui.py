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
from tkinter import ttk, messagebox, simpledialog, font as tkfont
from .job_view import JobCatalog, job_row, group_jobs, group_summary

from .integrations import install_hooks
from .claude_details import ClaudeDetails
from .session_presence import ClaudePresence
from . import startup
from .power_state import action_message
from .relaunch import is_administrator, request_admin_relaunch
from .themes import PALETTES, THEME_LABELS

BG='#f3f5f8'
CARD='#ffffff'
TEXT='#172231'
MUTED='#536174'
TEAL='#245dcb'
BORDER='#d8dfe9'
INSET='#eef2f7'
AMBER='#976212'
ERROR='#b23b43'
SUCCESS='#1b775e'
MODES={
 'idle':('작업을 기다리고 있어요','AI 작업이 시작되면 자동으로 절전을 막습니다.'),
 'running':('AI가 작업 중이에요','작업이 진행되는 동안 컴퓨터를 깨어 있게 합니다.'),
 'countdown':('이제 쉴 준비를 해요','새 작업이나 마우스·키보드 입력이 있으면 대기 시간을 다시 계산합니다.'),
 'waiting':('입력 대기 작업이 남아 있어요','정상 종료는 모든 작업이 끝난 뒤에만 요청합니다. 지금은 Windows의 기존 절전 정책을 따릅니다.'),
 'paused':('자동 관리가 멈춰 있어요','현재는 Windows의 기존 전원 설정을 따릅니다.'),
 'unknown':('작업 상태를 확인 중이에요','확인되지 않은 작업을 완료로 판단하지 않습니다. 복구 유예 중입니다.'),
 'fault':('자동 관리에 확인이 필요해요','감지 복구 유예가 끝나 Windows의 기존 전원 설정을 따릅니다.'),
 'interrupted':('재부팅 전 작업의 종료를 확인해야 해요','작업 결과를 확인할 때까지 자동 전원 전환을 보류합니다.'),
}
ACTIONS={'절전':'sleep','정상 종료':'shutdown'}
TEXT_SIZES={'기본':12,'크게':14,'아주 크게':16}
FONT_SIZES={8:11,9:11,10:12,11:13,12:14,13:16,20:22,25:28}
STATES={'running':'작업 중','complete':'완료','waiting':'입력 대기','unknown':'확인 필요','interrupted':'재부팅 전 미확인','idle':'연결됨'}

class Window:
    def __init__(self,controller,args,command):
        self.controller,self.args,self.command=controller,args,command
        self.theme_name=self.controller.store.theme()
        self.palette=PALETTES[self.theme_name]
        self.themed_buttons=[]
        self.root=tk.Tk()
        self.root.title('AI Job Sleep Manager')
        height=min(980,max(560,self.root.winfo_screenheight()-80))
        self.root.geometry(f'{min(1440,self.root.winfo_screenwidth()-60)}x{height}')
        self.root.minsize(900,560)
        self.root.configure(bg=self.palette.bg)
        self.closed=False
        self.last_presence=0
        self.rows=None
        self.job_rows={}
        self.detail_text=None
        self.save_message=''
        self.save_failed=False
        self.labels=self.controller.store.labels()
        codex_home=Path(os.environ.get("CODEX_HOME",str(Path.home()/".codex")))
        self.catalog=JobCatalog(codex_home)
        self.claude_details=ClaudeDetails(Path(os.environ.get('CLAUDE_CONFIG_DIR',str(Path.home()/'.claude')))/'projects')
        self.claude_presence=ClaudePresence(Path(os.environ.get('CLAUDE_CONFIG_DIR',str(Path.home()/'.claude')))/'sessions')
        self.admin=is_administrator()
        self.mode_message=('관리자 권한 요청이 취소되어 이번 실행은 상태만 확인합니다.'
                           if getattr(args,'elevation_cancelled',False) else '')
        self.shutdown_permission=getattr(self.controller.power,'shutdown_permission',
            lambda:{'available':None,'error_code':None})()
        self.font_size=self.controller.store.text_size()
        self.fonts={}
        self.root.option_add('*Font',self.font())
        style=ttk.Style(self.root)
        self.style=style
        style.theme_use('clam')
        self.configure_theme_styles()
        self.build()
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.root.after(100,self.refresh)
        if args.smoke_ui:
            self.root.after(1800,self.capture_and_close)

    def configure_theme_styles(self):
        style=self.style
        style.configure('.',background=self.palette.card,foreground=self.palette.text)
        style.configure('Treeview',background=self.palette.card,fieldbackground=self.palette.card,foreground=self.palette.text,rowheight=self.font(10).metrics('linespace')+22,borderwidth=0,font=self.font(10))
        style.layout('Treeview',[('Treeview.treearea',{'sticky':'nswe'})])
        style.configure('Treeview.Heading',background=self.palette.inset,foreground=self.palette.muted,relief='flat',font=self.font(9,True),padding=(8,9))
        style.map('Treeview.Heading',background=[('active',self.palette.secondary_hover)],foreground=[('active',self.palette.text)])
        style.map('Treeview',background=[('selected',self.palette.selected)],foreground=[('selected',self.palette.text)])
        style.configure('Vertical.TScrollbar',background=self.palette.border,troughcolor=self.palette.inset,bordercolor=self.palette.inset,arrowcolor=self.palette.muted,lightcolor=self.palette.border,darkcolor=self.palette.border,arrowsize=18)
        style.configure('Horizontal.TScrollbar',background=self.palette.border,troughcolor=self.palette.inset,bordercolor=self.palette.inset,arrowcolor=self.palette.muted,lightcolor=self.palette.border,darkcolor=self.palette.border,arrowsize=18)
        style.configure('TCombobox',fieldbackground=self.palette.inset,foreground=self.palette.text,background=self.palette.border,arrowcolor=self.palette.text,bordercolor=self.palette.border,lightcolor=self.palette.border,darkcolor=self.palette.border)
        style.map('TCombobox',fieldbackground=[('readonly',self.palette.inset)],foreground=[('readonly',self.palette.text)],selectbackground=[('readonly',self.palette.inset)],selectforeground=[('readonly',self.palette.text)])
        style.configure('TSpinbox',fieldbackground=self.palette.inset,foreground=self.palette.text,background=self.palette.border,arrowcolor=self.palette.text,bordercolor=self.palette.border,lightcolor=self.palette.border,darkcolor=self.palette.border)
        style.configure('TNotebook',background=self.palette.card,borderwidth=0,tabmargins=(0,4,0,12),bordercolor=self.palette.border,lightcolor=self.palette.border,darkcolor=self.palette.border)
        style.configure('TNotebook.Tab',background=self.palette.inset,foreground=self.palette.muted,font=self.font(9,True),padding=(16,10),borderwidth=0,bordercolor=self.palette.border,lightcolor=self.palette.border,darkcolor=self.palette.border)
        style.map('TNotebook.Tab',background=[('selected',self.palette.card),('active',self.palette.secondary_hover)],foreground=[('selected',self.palette.accent)])
        for option,color in [('Background',self.palette.inset),('Foreground',self.palette.text),
                             ('selectBackground',self.palette.selected),('selectForeground',self.palette.text)]:
            self.root.option_add('*TCombobox*Listbox*'+option,color)

    def apply_theme(self,event=None):
        wanted=THEME_LABELS[self.theme_choice.get()]
        try: self.controller.store.save_theme(wanted)
        except (ValueError,OSError,sqlite3.Error) as exc:
            self.theme_choice.set(next(label for label,name in THEME_LABELS.items() if name==self.theme_name))
            self.theme_feedback.config(text='테마를 저장하지 못했습니다. 기존 화면을 유지합니다. '+str(exc),fg=self.palette.error)
            return
        previous=self.palette
        self.theme_name=wanted;self.palette=PALETTES[wanted]
        # Recolor existing widgets; retain selection, unsaved inputs and scroll positions.
        roles=('bg','card','text','muted','border','inset','amber','error','success',
               'secondary_hover','selected','accent')
        replacements={getattr(previous,role):getattr(self.palette,role) for role in roles}
        options=('background','foreground','activebackground','activeforeground','selectcolor',
                 'selectbackground','selectforeground','insertbackground','highlightbackground',
                 'highlightcolor','disabledforeground')
        def recolor(widget):
            if not isinstance(widget,tk.Button):
                for option in options:
                    if option in widget.keys():
                        color=str(widget.cget(option))
                        if color in replacements: widget.configure(**{option:replacements[color]})
            if isinstance(widget,tk.Canvas):
                for item in widget.find_all():
                    for option in ('fill','outline'):
                        if option in widget.itemconfigure(item):
                            color=widget.itemcget(item,option)
                            if color in replacements: widget.itemconfigure(item,**{option:replacements[color]})
            for child in widget.winfo_children(): recolor(child)
        recolor(self.root)
        self.configure_theme_styles()
        for combo in (self.mode_select,self.action_select,self.text_size_select,self.theme_select):
            # Tk keeps combobox popups outside the Python widget tree after first use.
            popup=self.root.tk.call('ttk::combobox::PopdownWindow',str(combo))
            self.root.tk.call(popup+'.f.l','configure','-background',self.palette.inset,
                '-foreground',self.palette.text,'-selectbackground',self.palette.selected,
                '-selectforeground',self.palette.text)
        self.themed_buttons=[(button,primary) for button,primary in self.themed_buttons if button.winfo_exists()]
        for button,primary in self.themed_buttons: self.style_button(button,primary)
        for category,color in [('running',self.palette.text),('waiting',self.palette.amber),
                               ('unknown',self.palette.amber),('interrupted',self.palette.amber),
                               ('complete',self.palette.muted),('idle',self.palette.muted)]:
            self.table.tag_configure(category,foreground=color)
        for tag,color in [('heading',self.palette.accent),('request',self.palette.text),('muted',self.palette.muted)]:
            self.details.tag_configure(tag,foreground=color)
        self.theme_feedback.config(text=self.theme_choice.get()+' 적용됨 · 다음 실행에도 유지됩니다.',fg=self.palette.success)
        self.update_settings_feedback()

    def style_button(self,button,primary,hover=False):
        p=self.palette
        button.config(bg=(p.primary_hover if hover else p.primary) if primary else (p.secondary_hover if hover else p.card),
            fg=p.primary_text if primary else p.text,activebackground=p.primary_hover if primary else p.secondary_hover,
            activeforeground=p.primary_text if primary else p.text,disabledforeground=p.muted,
            highlightbackground=p.primary if primary else p.border,highlightcolor=p.accent)

    def font(self,size=10,bold=False):
        key=(size,bold)
        if key not in self.fonts:
            self.fonts[key]=tkfont.Font(root=self.root,family='Malgun Gothic',
                size=FONT_SIZES.get(size,size)+self.font_size-12,weight='bold' if bold else 'normal')
        return self.fonts[key]

    def apply_text_size(self,event=None):
        size=TEXT_SIZES[self.text_size.get()]
        try: self.controller.store.set_text_size(size)
        except (ValueError,OSError,sqlite3.Error) as exc:
            self.text_size.set(next(k for k,v in TEXT_SIZES.items() if v==self.font_size))
            messagebox.showerror('글자 크기 저장',str(exc),parent=self.root)
            return
        self.font_size=size
        for (original,bold),font in self.fonts.items():
            font.configure(size=FONT_SIZES.get(original,original)+size-12)
        self.style.configure('Treeview',rowheight=self.font(10).metrics('linespace')+22)
        self.layout_panels(self.page_canvas.winfo_width())

    def layout_panels(self,width):
        if not hasattr(self,'panels'): return
        stacked=width < self.font(10).measure('가')*76+100
        self.panels.columnconfigure(0,weight=1 if stacked else 48,uniform='panels')
        self.panels.columnconfigure(1,weight=0 if stacked else 52,uniform='' if stacked else 'panels')
        self.list_panel.grid_configure(row=0,column=0,padx=(0,0) if stacked else (0,16),pady=(0,16) if stacked else 0)
        self.detail_panel.grid_configure(row=1 if stacked else 0,column=0 if stacked else 1)

    def label(self,parent,text,fg=None,size=10,bold=False,**kw):
        if fg is None: fg=self.palette.text
        label=tk.Label(parent,text=text,bg=parent.cget('bg'),fg=fg,font=self.font(size,bold),**kw)
        if 'wraplength' in kw:
            label.bind('<Configure>',lambda event:label.config(wraplength=max(200,event.width-8)))
        return label

    def button(self,parent,text,command,primary=False,help_text=''):
        button=tk.Button(parent,text=text,command=command,
             relief='flat',bd=0,padx=14,pady=8,cursor='hand2',font=self.font(9,True),
             highlightthickness=1)
        self.themed_buttons.append((button,primary));self.style_button(button,primary)
        button.bind('<Enter>',lambda event:self.style_button(button,primary,hover=True) if button.cget('state')!='disabled' else None)
        button.bind('<Leave>',lambda event:self.style_button(button,primary) if button.cget('state')!='disabled' else None)
        if help_text: self.tooltip(button,help_text)
        return button

    def tooltip(self,widget,text):
        pending=[None]; popup=[None]
        def hide(event=None):
            if pending[0]:
                self.root.after_cancel(pending[0]);pending[0]=None
            if popup[0]: popup[0].destroy();popup[0]=None
        def show():
            pending[0]=None
            if self.closed: return
            tip=tk.Toplevel(self.root);popup[0]=tip;tip.overrideredirect(True)
            tip.geometry(f'+{widget.winfo_rootx()}+{widget.winfo_rooty()+widget.winfo_height()+6}')
            tk.Label(tip,text=text,bg=self.palette.inset,fg=self.palette.text,font=self.font(8),padx=12,pady=8,
                     wraplength=360,justify='left').pack()
        widget.bind('<Enter>',lambda event:pending.__setitem__(0,self.root.after(450,show)),add='+')
        widget.bind('<Leave>',hide,add='+');widget.bind('<ButtonPress>',hide,add='+')

    def build(self):
        # Keep all controls reachable on scaled displays without hiding the selected task.
        self.page_canvas=tk.Canvas(self.root,bg=self.palette.bg,highlightthickness=0)
        page_scroll=ttk.Scrollbar(self.root,orient='vertical',command=self.page_canvas.yview)
        self.page_canvas.configure(yscrollcommand=page_scroll.set)
        page_scroll.pack(side='right',fill='y'); self.page_canvas.pack(side='left',fill='both',expand=True)
        shell=tk.Frame(self.page_canvas,bg=self.palette.bg,padx=24,pady=20)
        self.page_window=self.page_canvas.create_window((0,0),window=shell,anchor='nw')
        shell.bind('<Configure>',lambda event:self.page_canvas.configure(scrollregion=self.page_canvas.bbox('all')))
        self.page_canvas.bind('<Configure>',self.resize_page)
        self.root.bind('<MouseWheel>',self.scroll_page,add='+')

        top=tk.Frame(shell,bg=self.palette.bg); top.pack(fill='x',pady=(0,18))
        top.columnconfigure(0,weight=1)
        self.header_brand=tk.Frame(top,bg=self.palette.bg);self.header_brand.grid(row=0,column=0,sticky='w')
        self.header_controls=tk.Frame(top,bg=self.palette.bg);self.header_controls.grid(row=0,column=1,sticky='e')
        mark=tk.Canvas(self.header_brand,width=36,height=36,bg=self.palette.bg,highlightthickness=0); mark.pack(side='left',padx=(0,12))
        mark.create_oval(6,6,30,30,outline=self.palette.accent,width=2)
        mark.create_line(18,1,18,16,fill=self.palette.accent,width=3)
        brand=tk.Frame(self.header_brand,bg=self.palette.bg); brand.pack(side='left')
        self.label(brand,'AI 작업 전원 관리',size=20,bold=True,anchor='w').pack(fill='x')
        self.label(brand,'작업 중에는 깨워 두고, 끝나면 쉬게 합니다',self.palette.muted,8,anchor='w').pack(fill='x')
        self.pause=self.button(self.header_controls,'자동 관리 끄기',self.toggle,True,
            help_text='켜면 AI 작업 중 절전을 막고, 완료 후 설정한 절전·종료를 실행합니다. 끄면 Windows의 기존 전원 설정을 따릅니다.'); self.pause.pack(side='right')
        self.settings_button=self.button(self.header_controls,'설정 열기  ▾',self.toggle_settings,
            help_text='전원 동작, Codex·Claude 작업 감지, 화면·로그인 시작 설정을 확인합니다.'); self.settings_button.pack(side='right',padx=8)
        badge='● 관찰 모드' if self.controller.power.dry_run else '● 자동 관리'
        self.mode_badge=self.label(self.header_controls,badge,self.palette.amber if self.controller.power.dry_run else self.palette.accent,9,padx=12); self.mode_badge.pack(side='right')

        overview=tk.Frame(shell,bg=self.palette.card,highlightbackground=self.palette.border,highlightthickness=1,padx=18,pady=14)
        overview.pack(fill='x',pady=(0,16))
        line=tk.Frame(overview,bg=self.palette.card); line.pack(fill='x')
        self.status_label=self.label(line,'작업 상태를 불러오는 중',self.palette.accent,13,True,anchor='w'); self.status_label.pack(side='left')
        self.description=self.label(overview,'',self.palette.muted,9,anchor='w',wraplength=960,justify='left'); self.description.pack(fill='x',pady=(4,12))
        self.applied_summary=self.label(overview,'',self.palette.text,10,True,anchor='w',justify='left',wraplength=1100)
        self.applied_summary.pack(fill='x',pady=(0,6))
        self.execution_status=self.label(overview,'',self.palette.amber,10,True,anchor='w',justify='left',wraplength=1100)
        self.execution_status.pack(fill='x',pady=(0,12))
        self.management_help=self.label(overview,'',self.palette.muted,9,anchor='w',justify='left',wraplength=1100)
        metrics=tk.Frame(overview,bg=self.palette.card); metrics.pack(fill='x')
        self.metric_values=[]; self.metric_titles=[]
        for index,(title,hint,color) in enumerate((('진행 중인 작업','메인 작업',self.palette.text),('진행 중인 하위 작업','AI가 나눠 맡긴 작업',self.palette.text),('절전까지 남은 시간','작업 종료 후 대기',self.palette.accent))):
            metrics.columnconfigure(index,weight=1,uniform='metric')
            frame=tk.Frame(metrics,bg=self.palette.card); frame.grid(row=0,column=index,sticky='ew',padx=(0,18))
            heading=self.label(frame,title,self.palette.muted,9,anchor='w'); heading.pack(fill='x'); self.metric_titles.append(heading)
            values=tk.Frame(frame,bg=self.palette.card); values.pack(fill='x')
            value=self.label(values,'—',color,25,True,anchor='w'); value.pack(side='left'); self.metric_values.append(value)
            self.label(values,hint,self.palette.muted,8).pack(side='left',padx=(10,0),pady=(12,0))

        self.settings_open=False
        self.settings_panel=tk.Frame(shell,bg=self.palette.card,padx=20,pady=18,highlightbackground=self.palette.border,highlightthickness=1)
        self.label(self.settings_panel,'앱 설정',size=13,bold=True,anchor='w').pack(fill='x',pady=(0,10))
        self.settings_tabs=ttk.Notebook(self.settings_panel);self.settings_tabs.pack(fill='x')
        power_tab=tk.Frame(self.settings_tabs,bg=self.palette.card,padx=4,pady=8)
        detect_tab=tk.Frame(self.settings_tabs,bg=self.palette.card,padx=4,pady=8)
        display_tab=tk.Frame(self.settings_tabs,bg=self.palette.card,padx=4,pady=8)
        self.settings_tabs.add(power_tab,text='전원 동작')
        self.settings_tabs.add(detect_tab,text='Codex·Claude 감지')
        self.settings_tabs.add(display_tab,text='화면·시작 설정')
        self.label(power_tab,'컴퓨터의 전원을 실제로 바꿀까요?',size=11,bold=True,anchor='w').pack(fill='x',pady=(0,10))
        mode_controls=tk.Frame(power_tab,bg=self.palette.card); mode_controls.pack(fill='x',pady=(0,8))
        self.label(mode_controls,'전원 제어 모드',bold=True).pack(side='left',padx=(0,12))
        self.execution_mode=tk.StringVar(value='관찰 모드' if self.controller.power.dry_run else '실제 전원 제어')
        self.mode_select=ttk.Combobox(mode_controls,textvariable=self.execution_mode,values=('관찰 모드','실제 전원 제어'),state='readonly',width=15,font=self.font())
        self.mode_select.pack(side='left')
        self.mode_select.bind('<<ComboboxSelected>>',lambda event:self.apply_execution_mode())
        self.label(mode_controls,'선택하면 바로 전환',self.palette.muted,9).pack(side='left',padx=12)
        self.mode_feedback=self.label(power_tab,'',self.palette.muted,9,anchor='w',justify='left',wraplength=1100)
        self.mode_feedback.pack(fill='x',pady=(0,8))
        self.execution_mode.trace_add('write',lambda *args:self.update_mode_feedback())
        self.label(power_tab,'관찰 모드: 상태와 예정된 동작만 확인합니다.\n실제 전원 제어: 작업 중 절전을 막고, 완료 후 아래 동작을 실행합니다.',self.palette.muted,9,
                   anchor='w',justify='left',wraplength=1000).pack(fill='x',pady=(0,20))
        self.label(power_tab,'모든 AI 작업이 끝나면',size=11,bold=True,anchor='w').pack(fill='x',pady=(0,10))
        settings=tk.Frame(power_tab,bg=self.palette.card); settings.pack(fill='x')
        self.label(settings,'작업이 끝나면',size=10,bold=True).pack(side='left')
        self.minutes=tk.StringVar(value=str(int(self.controller.engine.delay/60)))
        self.spin=ttk.Spinbox(settings,from_=1,to=120,width=5,textvariable=self.minutes,font=self.font(11),justify='center'); self.spin.pack(side='left',padx=10)
        self.label(settings,'분 후',self.palette.muted).pack(side='left')
        self.action=tk.StringVar(value='정상 종료' if self.controller.engine.action=='shutdown' else '절전')
        self.action_select=ttk.Combobox(settings,textvariable=self.action,values=list(ACTIONS),state='readonly',width=10,font=self.font(10)); self.action_select.pack(side='left',padx=10)
        self.save_button=self.button(settings,'설정 저장',self.apply_delay,True); self.save_button.pack(side='left')
        self.save_feedback=self.label(power_tab,'',self.palette.success,9,False,anchor='w',justify='left',wraplength=1100)
        self.save_feedback.pack(fill='x',pady=(8,0))
        self.minutes.trace_add('write',self.settings_edited)
        self.action.trace_add('write',self.settings_edited)

        self.label(detect_tab,'Codex·Claude의 작업 알림을 받습니다',size=11,bold=True,anchor='w').pack(fill='x')
        self.label(detect_tab,'AI 도구의 설정에 작업 시작·종료 알림을 추가합니다. 처음 설정하거나 앱 위치를 바꿨을 때 사용하세요.\n설정 후 AI 앱·터미널 세션을 새로 시작하면 알림을 받을 수 있습니다.',self.palette.muted,9,
                   anchor='w',justify='left',wraplength=1000).pack(fill='x',pady=(6,12))
        connection=tk.Frame(detect_tab,bg=self.palette.card); connection.pack(fill='x')
        self.connect_button=self.button(connection,'Codex·Claude 감지 설정',self.connect,True,
            help_text='기존 설정을 백업하고 이 앱에 작업 시작·종료를 알려 주는 설정을 추가합니다. AI 앱·터미널을 다시 시작해야 합니다.')
        self.connect_button.pack(side='left')
        self.disconnect_button=self.button(connection,'이 앱의 감지 설정 제거',self.disconnect,
            help_text='이 앱이 추가한 작업 알림 설정을 제거하고 자동 전원 관리를 끕니다.')
        self.disconnect_button.pack(side='left',padx=10)
        self.detection_feedback=self.label(detect_tab,'',self.palette.muted,9,anchor='w',justify='left',wraplength=1000)
        self.detection_feedback.pack(fill='x',pady=(12,18))
        self.label(detect_tab,'문제가 생겼나요?',size=11,bold=True,anchor='w').pack(fill='x')
        self.label(detect_tab,'감지 상태, 종료 권한, 마지막 오류와 전원 처리 기록을 별도 창에서 확인합니다.',self.palette.muted,9,
                   anchor='w',justify='left',wraplength=1000).pack(fill='x',pady=(6,10))
        self.diagnose_button=self.button(detect_tab,'상태·오류 확인',self.diagnose,
            help_text='읽기 전용 진단 창을 엽니다. 실행 모드·권한·설정·오류·기록을 확인하고 복사할 수 있습니다.')
        self.diagnose_button.pack(anchor='w')

        self.label(display_tab,'화면을 편하게 읽기',size=11,bold=True,anchor='w').pack(fill='x',pady=(0,12))
        appearance=tk.Frame(display_tab,bg=self.palette.card);appearance.pack(fill='x',pady=(0,8))
        self.label(appearance,'화면 테마',bold=True).pack(side='left',padx=(0,12))
        self.theme_choice=tk.StringVar(value=next(label for label,name in THEME_LABELS.items() if name==self.theme_name))
        self.theme_select=ttk.Combobox(appearance,textvariable=self.theme_choice,values=list(THEME_LABELS),state='readonly',width=10,font=self.font())
        self.theme_select.pack(side='left')
        self.theme_select.bind('<<ComboboxSelected>>',self.apply_theme)
        self.theme_feedback=self.label(display_tab,'선택 즉시 적용 · 다음 실행에도 유지',self.palette.muted,9,anchor='w',justify='left',wraplength=1000)
        self.theme_feedback.pack(fill='x',pady=(0,16))
        accessibility=tk.Frame(display_tab,bg=self.palette.card); accessibility.pack(fill='x')
        self.label(accessibility,'글자 크기',bold=True).pack(side='left',padx=(0,12))
        self.text_size=tk.StringVar(value=next(k for k,v in TEXT_SIZES.items() if v==self.font_size))
        self.text_size_select=ttk.Combobox(accessibility,textvariable=self.text_size,values=list(TEXT_SIZES),state='readonly',width=10,font=self.font())
        self.text_size_select.pack(side='left')
        self.text_size_select.bind('<<ComboboxSelected>>',self.apply_text_size)
        self.label(accessibility,'선택 즉시 적용 · 다음 실행에도 유지',self.palette.muted,9).pack(side='left',padx=12)
        self.start_on_login=tk.BooleanVar(value=startup.is_enabled())
        tk.Checkbutton(display_tab,text='Windows에 로그인하면 이 앱 열기',variable=self.start_on_login,command=self.toggle_login_startup,
                       bg=self.palette.card,fg=self.palette.text,selectcolor=self.palette.card,activebackground=self.palette.card,
                       activeforeground=self.palette.text,font=self.font(9)).pack(anchor='w',pady=(24,6))
        self.label(display_tab,'선택 즉시 저장됩니다. 실제 전원 모드로 저장된 앱은 시작 시 Windows 권한 승인을 요청합니다.',self.palette.muted,9,
                   anchor='w',justify='left',wraplength=1000).pack(fill='x')
        self.policy_note=self.label(power_tab,'',self.palette.muted,9,anchor='w',wraplength=980,justify='left'); self.policy_note.pack(fill='x',pady=(14,0))
        # Retain the callable for older integrations; mode selection now handles elevation.
        self.elevate_button=self.button(power_tab,'실제 전원 제어로 전환',self.elevate)

        self.workspace=tk.Frame(shell,bg=self.palette.bg); self.workspace.pack(fill='both',expand=True)
        table_header=tk.Frame(self.workspace,bg=self.palette.bg); table_header.pack(fill='x')
        self.jobs_heading=self.label(table_header,'남은 작업',size=11,bold=True); self.jobs_heading.pack(side='left')
        self.show_complete=tk.BooleanVar(value=False)
        tk.Checkbutton(table_header,text='완료한 작업도 보기',variable=self.show_complete,command=self.render_jobs,bg=self.palette.bg,fg=self.palette.muted,selectcolor=self.palette.card,activebackground=self.palette.bg,activeforeground=self.palette.text,font=self.font(9)).pack(side='right')
        self.jobs_summary=self.label(self.workspace,'',self.palette.muted,9,anchor='w'); self.jobs_summary.pack(fill='x',pady=(5,12))
        self.show_history=tk.BooleanVar(value=False)
        history_bar=tk.Frame(self.workspace,bg=self.palette.bg);history_bar.pack(fill='x',pady=(0,10))
        self.history_notice=self.label(self.workspace,'',self.palette.amber,9,anchor='w',justify='left',wraplength=1000);self.history_notice.pack(fill='x',pady=(0,10))
        self.history_toggle=tk.Checkbutton(history_bar,text='이전 실행에서 완료를 확인하지 못한 작업 보기',variable=self.show_history,command=self.render_jobs,bg=self.palette.bg,fg=self.palette.muted,selectcolor=self.palette.card,activebackground=self.palette.bg,activeforeground=self.palette.text,font=self.font(9),wraplength=440,justify='left')
        self.history_toggle.pack(side='left')
        self.refresh_button=self.button(history_bar,'작업 상태 새로고침',self.recheck_connections,
            help_text='Codex·Claude의 최신 작업 기록을 다시 읽습니다. 종료 확인이 없는 작업을 완료로 바꾸지 않습니다.')
        self.refresh_button.pack(side='right')
        panels=tk.Frame(self.workspace,bg=self.palette.bg); self.panels=panels; panels.pack(fill='both',expand=True)
        panels.columnconfigure(0,weight=55,uniform='panels'); panels.columnconfigure(1,weight=45,uniform='panels')
        panels.rowconfigure(0,weight=1)
        left=tk.Frame(panels,bg=self.palette.card,highlightbackground=self.palette.border,highlightthickness=1)
        self.list_panel=left
        left.grid(row=0,column=0,sticky='nsew',padx=(0,12))
        left_header=tk.Frame(left,bg=self.palette.card,padx=14,pady=12); left_header.pack(fill='x')
        self.label(left_header,'작업 목록',size=11,bold=True).pack(side='left')
        self.label(left_header,'▾ 하위 작업 펼치기',self.palette.muted,8).pack(side='right')
        table_frame=tk.Frame(left,bg=self.palette.card); table_frame.pack(fill='both',expand=True,padx=1)
        self.table=ttk.Treeview(table_frame,columns=('tool','latest_request','state','current_activity'),displaycolumns=('latest_request','state'),show='tree headings',height=6)
        for col,title,width in [('#0','대화 · 하위 작업',225),('tool','도구',85),('latest_request','최근 지시',175),('state','상태',105),('current_activity','현재 활동',220)]:
            self.table.heading(col,text=title,anchor='w'); self.table.column(col,width=width,minwidth=width,anchor='w')
        horizontal=ttk.Scrollbar(left,orient='horizontal',command=self.table.xview)
        horizontal.pack(side='bottom',fill='x',padx=8)
        self.table.configure(xscrollcommand=horizontal.set)
        scroll=ttk.Scrollbar(table_frame,orient='vertical',command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side='left',fill='both',expand=True); scroll.pack(side='right',fill='y')
        for category,color in [('running',self.palette.text),('waiting',self.palette.amber),('unknown',self.palette.amber),('interrupted',self.palette.amber),('complete',self.palette.muted),('idle',self.palette.muted)]: self.table.tag_configure(category,foreground=color)
        self.table.bind('<<TreeviewSelect>>',self.show_job_details)
        self.label(left,'작업을 선택하면 상세 설명을 확인할 수 있습니다.',self.palette.muted,8,anchor='w',padx=14,pady=10).pack(fill='x')

        right=tk.Frame(panels,bg=self.palette.card,highlightbackground=self.palette.border,highlightthickness=1)
        self.detail_panel=right
        right.grid(row=0,column=1,sticky='nsew')
        detail_header=tk.Frame(right,bg=self.palette.card,padx=14,pady=10); detail_header.pack(fill='x')
        self.label(detail_header,'지금 무슨 일을 하나요?',size=11,bold=True).pack(side='left')
        self.detail_badge=self.label(detail_header,'선택 없음',self.palette.muted,9); self.detail_badge.pack(side='right')
        self.details=tk.Text(right,width=1,height=12,bg=self.palette.card,fg=self.palette.text,relief='flat',wrap='word',font=self.font(11),padx=20,pady=16,state='disabled',spacing1=3,spacing2=3,spacing3=5,insertbackground=self.palette.accent,selectbackground=self.palette.selected,selectforeground=self.palette.text)
        detail_scroll=ttk.Scrollbar(right,orient='vertical',command=self.details.yview)
        self.details.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side='right',fill='y'); self.details.pack(fill='both',expand=True)
        for tag,color,size,bold in [('heading',self.palette.accent,10,True),('request',self.palette.text,12,True),('muted',self.palette.muted,9,False)]:
            self.details.tag_configure(tag,foreground=color,font=self.font(size,bold))
        detail_footer=tk.Frame(self.workspace,bg=self.palette.bg); detail_footer.pack(fill='x',pady=(10,0))
        self.notice=self.label(detail_footer,'설정 → Codex·Claude 감지에서 작업 알림 설정과 상태·오류를 확인할 수 있습니다.',self.palette.muted,8,anchor='w',wraplength=760,justify='left'); self.notice.pack(side='left',fill='x',expand=True)
        self.button(detail_footer,'선택한 작업 이름 지정',self.rename_job).pack(side='right')
        self.confirm_button=self.button(detail_header,'이 작업이 끝났음을 확인',self.confirm_selected_finished,
            help_text='원래 AI 창에서 작업이 끝난 것을 확인한 뒤 사용하세요. 선택한 미확인 작업에만 완료 기록을 남깁니다.')
        self.confirm_button.pack(side='right',padx=8)
        self.confirm_button.config(state='disabled')
        self.update_settings_feedback()
        self.set_detail('목록에서 작업을 선택하세요.\n최근 지시와 AI의 진행 설명이 여기에 표시됩니다.')

    def resize_page(self,event):
        self.page_canvas.itemconfigure(self.page_window,width=event.width)
        self.layout_panels(event.width)
        compact=event.width < self.header_brand.winfo_reqwidth()+self.header_controls.winfo_reqwidth()+70
        self.header_controls.grid_configure(row=1 if compact else 0,column=0 if compact else 1,
            columnspan=2 if compact else 1,sticky='w' if compact else 'e',pady=(14,0) if compact else 0)
        for name,padding in (('description',100),('management_help',100),('policy_note',100),('notice',350),('jobs_summary',70),('jobs_heading',290),('applied_summary',100),('execution_status',100),('save_feedback',100),('mode_feedback',100),('detection_feedback',100),('history_notice',100)):
            if hasattr(self,name): getattr(self,name).config(wraplength=max(300,event.width-padding))

    def toggle_settings(self):
        self.settings_open=not self.settings_open
        if self.settings_open:
            self.settings_panel.pack(fill='x',before=self.workspace,pady=(0,16))
        else: self.settings_panel.pack_forget()
        self.settings_button.config(text='설정 닫기  ▴' if self.settings_open else '설정 열기  ▾')

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
                description=f'작업 완료 후 {int(self.controller.engine.delay/60)}분 뒤 정상 종료합니다. 새 작업이나 사용자 입력이 있으면 시간을 다시 계산합니다.'
            power_message=action_message(self.controller.power_action,self.controller.clock()) if shutdown else ''
            if power_message:
                title=('종료 준비를 다시 확인하고 있어요' if self.controller.power_action.phase=='deferred'
                       else 'Windows에 종료를 요청했어요' if self.controller.power_action.phase=='requested'
                       else '자동 관리가 중지됐어요')
                description=power_message
            elif self.controller.error:
                title='종료 요청을 보류했어요' if shutdown else '절전을 보류했어요'
                description=('상태·오류 확인에서 원인을 확인한 뒤 자동 관리를 다시 켜 주세요. 자동으로 재시도하지 않습니다.' if shutdown else '권한 또는 감지 상태를 확인하세요. 자세한 이유는 상태·오류 확인에서 볼 수 있습니다.')
            elif self.controller.shutdown_notice:
                title='정상 종료 요청을 처리했어요'
                description=self.controller.shutdown_notice+' 다시 사용하려면 자동 관리 켜기를 누르세요.'
            elif not shutdown and not self.admin and not self.controller.power.dry_run:
                description+='  자동 전원 전환에는 관리자 권한 확인이 필요합니다.'
            elif shutdown and self.shutdown_permission['available'] is False and not self.controller.power.dry_run:
                description+='  종료 권한 검사에 실패했습니다. 상태·오류 확인에서 권한 상태를 확인해 주세요.'
            if self.controller.power.dry_run:
                description+=' 관찰 모드에서 확인한 상태입니다.'
                self.metric_titles[2].config(text='모의 종료 대기 시간' if shutdown else '모의 절전 대기 시간')
            status_color=self.palette.amber if status.mode in ('waiting','paused','interrupted','unknown') else self.palette.error if status.mode=='fault' or self.controller.error else self.palette.accent
            self.status_label.config(text=title,fg=status_color)
            if not self.controller.power.dry_run:
                self.mode_badge.config(text='● 자동 관리' if self.controller.engine.enabled else '○ 관리 중지',fg=self.palette.accent if self.controller.engine.enabled else self.palette.amber)
            self.description.config(text=description)
            self.metric_values[2].config(text='—' if status.remaining is None else f'{status.remaining//60:02d}:{status.remaining%60:02d}')
            self.pause.config(text='자동 관리 끄기' if self.controller.engine.enabled else '자동 관리 켜기')
            self.render_jobs()
            self.update_settings_feedback()
        except Exception as exc:
            self.notice.config(text='상태 확인 오류: '+str(exc),fg=self.palette.error)
        self.root.after(500,self.refresh)

    def render_jobs(self):
        jobs=list(self.controller.engine.jobs.values())
        connections=self.claude_presence.lookup()
        metadata=dict(self.catalog.lookup(jobs))
        for key,details in self.claude_details.lookup(jobs,getattr(self.controller,'claude_request_starts',{})).items():
            metadata[key]={**metadata.get(key,{}),**details}
        rows=[]
        for job in jobs:
            key=job.provider+':'+job.session_id
            info=dict(getattr(self.controller.reader,'details',{}).get(job.session_id,{})) if job.provider=='codex' else {}
            info.update(metadata.get(key,{}))
            if key in self.labels: info['label']=self.labels[key]
            row=job_row(job,info,self.controller.engine.action)
            row.update(self.controller.job_observation(job,connections))
            rows.append(row)
        rows=group_jobs(rows)
        # A current child keeps its parent visible as context.
        by_key={r['key']:r for r in rows}
        for row in reversed(rows):
            if not row['historical'] and row['parent_key'] in by_key:
                by_key[row['parent_key']]['historical']=False
        historical=[r for r in rows if r['historical']]
        interrupted_history=sum(r['own_category']=='interrupted' for r in historical)
        unknown_history=len(historical)-interrupted_history
        self.history_notice.config(text=(f'이전 작업 {len(historical)}개의 완료 확인이 필요합니다 · 재부팅 전 {interrupted_history}개 · 과거 미확인 {unknown_history}개. 현재 실행 중이라는 뜻은 아닙니다. 원래 AI 창에서 결과를 확인할 때까지 전원 전환을 보류합니다.'
                                        if historical else '이전 실행에서 완료를 확인하지 못한 작업이 없습니다. 새로고침은 최신 작업 기록을 다시 읽습니다.'),fg=self.palette.amber if historical else self.palette.muted)
        for row in historical:
            if row['own_category']=='interrupted':
                row['state']='이전 작업 · 완료 미확인';row['current_activity']='재부팅 전 작업 확인 대기'
            else:
                row['state']='이전 작업 · 완료 미확인';row['current_activity']='과거 기록 · 현재 실행 미확인'
        counts=group_summary([r for r in rows if not r['historical']])
        self.metric_values[0].config(text=str(counts['running']))
        self.metric_values[1].config(text=str(counts['child_running']))
        self.jobs_heading.config(text=f"진행 중인 작업 {counts['running']}개 · 하위 작업 {counts['child_running']}개")
        self.jobs_summary.config(text=f"응답 필요: 작업 {counts['waiting']} · 하위 {counts['child_waiting']}   |   확인 필요: 작업 {counts['unknown']} · 하위 {counts['child_unknown']}   |   중단: 작업 {counts['interrupted']} · 하위 {counts['child_interrupted']}   |   완료: 작업 {counts['complete']} · 하위 {counts['child_complete']}")
        previous_keys=set(self.job_rows)
        self.job_rows={row['key']:row for row in rows}
        visible=[row for row in rows if (self.show_history.get() if row['historical'] else row['pending'] or self.show_complete.get())]
        wanted={row['key'] for row in visible}
        selected=self.table.selection()
        scroll_position=self.table.yview()[0]
        # Detach first so deleting a missing parent cannot delete a surviving child.
        for key in previous_keys:
            if self.table.exists(key): self.table.detach(key)
        for key in previous_keys-wanted:
            if self.table.exists(key): self.table.delete(key)
        for row in visible:
            values=tuple(row[field] for field in ('tool','request_display','state','current_activity'))
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
            self.confirm_button.config(state='disabled')
            self.detail_badge.config(text='선택 없음',fg=self.palette.muted)
            self.set_detail('현재 목록에 표시할 작업이 없습니다. 위의 완료 작업·이전 작업 보기로 기록을 확인할 수 있습니다.' if rows else 'AI 작업이 감지되면 최근 지시와 진행 설명을 보여 드립니다.\n\n처음 사용한다면 설정 → Codex·Claude 감지 → 감지 설정을 누르고, AI 앱·터미널에서 새 작업을 시작하세요.')
        else:
            self.show_job_details()

    def set_detail(self,text):
        if text==self.detail_text: return
        self.detail_text=text
        self.details.config(state='normal')
        self.details.delete('1.0',tk.END); self.details.insert('1.0',text)
        for line_number,line in enumerate(text.splitlines(),1):
            tag=('request' if line.startswith(('최근 지시:','상위 사용자 지시:')) else 'heading' if line.startswith(('현재 활동:','AI의 최근 설명:','최근 실행 흐름','AI가 공유한 계획:','남은 하위 작업')) else 'muted' if line.startswith(('관측 시점:','설명 기록 시각:','대화 제목:','세션 ID:')) else None)
            if tag: self.details.tag_add(tag,f'{line_number}.0',f'{line_number}.end')
        self.details.config(state='disabled')

    def show_job_details(self,event=None):
        selected=self.table.selection()
        row=self.job_rows.get(selected[0]) if selected else None
        if not row: return
        self.detail_badge.config(text=row['state'],fg={'running':self.palette.accent,'waiting':self.palette.amber,'unknown':self.palette.amber,'interrupted':self.palette.amber}.get(row['category'],self.palette.muted))
        job=self.controller.engine.jobs.get(row['key'])
        confirmable=bool(job and job.state in ('unknown','interrupted') and not job.active)
        self.confirm_button.config(state='normal' if confirmable else 'disabled')
        if row['is_child'] and row['parent_request']:
            lines=['상위 사용자 지시: '+row['parent_request']]
            if row['latest_request']=='최신 지시 미제공':
                lines.append('하위 지시 본문은 기록에 제공되지 않았습니다. 아래는 이 에이전트의 공개 설명입니다.')
            else: lines.append('최근 지시: '+row['latest_request'])
        else: lines=[('과거 기록의 마지막 지시: ' if row.get('historical') else '최근 지시: ')+row['latest_request']]
        lines.extend(['',row.get('connection',''),'마지막 상태 이벤트: '+row.get('last_event','시각 미제공')])
        if row.get('historical'):
            lines.extend(['과거 기록입니다. 현재 실행 중이라는 증거가 아닙니다.',
                          '재부팅 전 종료 기록이 없습니다. 원래 AI 창에서 결과를 확인해 주세요.' if row['own_category']=='interrupted' else '전체 작업 종료는 확인되지 않아 자동 전원 전환을 보류합니다.'])
        if row['commentary']:
            lines.extend(['', 'AI의 최근 설명:',row['commentary']])
            if row['commentary_at']: lines.append('설명 기록 시각: '+row['commentary_at'])
        else:
            current=next((p['step'] for p in row['plan'] if p['status']=='in_progress'),'')
            lines.extend(['', 'AI의 최근 설명:',
                '공유된 계획의 현재 단계: '+current if current else '이 지시에 대한 공개 진행 설명이 아직 없습니다.'])
        lines.extend(['', '현재 활동: '+row['current_activity'],'관측 시점: '+row['activity_updated']])
        if row['child_work']:
            lines.extend(['', '남은 하위 작업 · 수행 내용:'])
            for child in row['child_work']:
                lines.extend(['  · '+child['title']+' — '+child['state'], '    '+child['description']])
                if child['commentary_at']: lines.append('    설명 기록 시각: '+child['commentary_at'])
                else: lines.append('    '+child['updated'])
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
        if row['activity_history']:
            lines.extend(['', '최근 실행 흐름 (관측된 도구 요청·응답):'])
            for item in row['activity_history'][-4:]: lines.append('  · '+item['text'])
        lines.append('세션 ID: '+row['session_id'])
        self.set_detail('\n'.join(lines))

    def confirm_selected_finished(self):
        selected=self.table.selection()
        row=self.job_rows.get(selected[0]) if selected else None
        if not row: return
        if not messagebox.askyesno('작업 종료 직접 확인',
                '원래 AI 창에서 이 작업이 더 이상 실행 중이지 않고 결과를 확인했나요?\n\n확인하면 이 작업은 자동 전원 전환의 차단 대상에서 제외됩니다. 미완료 작업을 성공으로 처리하지는 않습니다.',parent=self.root):
            return
        try:
            self.controller.confirm_job_finished(row['key'])
            self.notice.config(text='사용자 확인을 기록했습니다. 남은 작업과 전원 전환 조건을 다시 계산합니다.',fg=self.palette.accent)
            self.render_jobs()
            self.update_settings_feedback()
        except (ValueError,OSError,sqlite3.Error) as exc:
            messagebox.showerror('작업 확인 실패',str(exc),parent=self.root)

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

    def recheck_connections(self):
        try:
            if hasattr(self.controller.reader,'invalidate_scan'): self.controller.reader.invalidate_scan()
            self.controller.drain()
            self.claude_presence.lookup(force=True)
            self.render_jobs()
            self.update_settings_feedback()
            warning=self.claude_presence.last_error
            self.notice.config(text=warning or '작업 상태 새로고침 완료 · 최신 기록을 읽었습니다. 종료 확인이 없는 작업은 완료로 바꾸지 않습니다.',fg=self.palette.amber if warning else self.palette.success)
        except (OSError,ValueError,RuntimeError,sqlite3.Error) as exc:
            messagebox.showerror('작업 상태 새로고침 실패',str(exc),parent=self.root)

    def settings_edited(self,*args):
        self.save_message=''; self.save_failed=False
        self.update_settings_feedback()

    def update_settings_feedback(self):
        engine=self.controller.engine
        action='정상 종료' if engine.action=='shutdown' else '절전'
        applied=f'{int(engine.delay/60)}분 후 {action}'
        mode='관찰 모드' if self.controller.power.dry_run else '실제 전원 제어'
        management='자동 관리 켜짐' if engine.enabled else '자동 관리 중지'
        self.applied_summary.config(text=f'현재 적용값: {applied} · {mode} · {management}')
        try: dirty=int(self.minutes.get())*60!=engine.delay or ACTIONS.get(self.action.get())!=engine.action
        except ValueError: dirty=True
        if self.save_failed:
            feedback=self.save_message; color=self.palette.error
        elif dirty:
            feedback='● 미저장 변경 사항이 있습니다. 저장 전까지 '+applied+' 설정을 사용합니다.'; color=self.palette.amber
        else:
            feedback=self.save_message or '저장된 설정을 사용 중입니다. 값을 바꾸면 변경 내용을 저장할 수 있습니다.'; color=self.palette.success
        self.save_feedback.config(text=feedback,fg=color)
        self.save_button.config(text='변경 내용 저장' if dirty or self.save_failed else '설정 저장됨',state='normal' if dirty or self.save_failed else 'disabled',
            bg=self.palette.primary if dirty or self.save_failed else self.palette.inset,disabledforeground=self.palette.muted)
        jobs=list(engine.jobs.values())
        unknown=sum(j.state=='unknown' or bool(j.unknown_children) for j in jobs)
        interrupted=sum(j.state=='interrupted' for j in jobs)
        waiting=sum(j.state=='waiting' and not j.active for j in jobs)
        if self.controller.power.dry_run:
            execution='실제 종료 안 함 · 관찰 모드: 설정을 저장해도 절전 방지·절전·종료는 실행하지 않습니다.'
            if unknown: execution+=f' 상태 확인 필요 {unknown}개도 남아 있습니다.'
            if interrupted: execution+=f' 재부팅 전 종료 미확인 작업 {interrupted}개를 확인해야 합니다.'
            color=self.palette.amber
        elif not engine.enabled:
            execution=action_message(self.controller.power_action,self.controller.clock()) or '자동 관리 일시 중지 · 설정은 보관되지만 현재 실행되지 않습니다.'; color=self.palette.amber
        elif self.controller.power_action.phase=='deferred':
            execution=action_message(self.controller.power_action,self.controller.clock()); color=self.palette.amber
        elif self.controller.error:
            execution='전원 전환 보류 · 상태·오류 확인에서 원인을 확인해 주세요.'; color=self.palette.error
        elif interrupted:
            execution=f'전원 전환 보류 · 재부팅 전 종료 미확인 작업 {interrupted}개. 원래 AI 창에서 결과를 확인해 주세요.'; color=self.palette.amber
        elif unknown:
            execution=f'전원 전환 보류 · 확인 필요 작업 {unknown}개. 실행 종료 여부를 먼저 확인해야 합니다.'; color=self.palette.amber
        elif waiting and engine.action=='shutdown':
            execution=f'정상 종료 보류 · 사용자 답변·승인 대기 {waiting}개가 남아 있습니다.'; color=self.palette.amber
        elif not engine.seen_work:
            execution='실제 자동 관리 켜짐 · 새 작업을 감지한 뒤 완료 조건을 확인합니다.'; color=self.palette.accent
        else:
            execution=f'실제 자동 관리 켜짐 · 작업 상태와 사용자 입력을 확인한 뒤 {applied}를 요청합니다.'; color=self.palette.accent
        self.execution_status.config(text='다음 동작 · '+execution,fg=color)
        self.management_help.config(text=('자동 관리가 켜져 있습니다. 끄기를 누르면 이 앱의 절전 방지와 자동 전원 동작을 중지합니다.'
            if engine.enabled else '자동 관리가 꺼져 있습니다. 켜기를 누르면 작업 상태를 다시 확인하고 설정한 동작을 준비합니다.')
            +(' 관찰 모드에서는 실제 전원을 바꾸지 않습니다.' if self.controller.power.dry_run else ''))
        received=', '.join({'codex':'Codex','claude':'Claude Code'}.get(provider,provider) for provider in sorted(self.controller.live_providers))
        self.detection_feedback.config(text='이번 실행에서 받은 작업 알림: '+(received or '아직 없음. 감지 설정 후 AI 앱·터미널을 새로 시작해 주세요.'))
        self.update_mode_feedback()

    def update_mode_feedback(self):
        dry=self.controller.power.dry_run
        current='관찰 모드' if dry else '실제 전원 제어'
        pending=self.execution_mode.get()!=current
        saved='관찰 모드' if self.controller.store.execution_dry_run() else '실제 전원 제어'
        forced=bool(getattr(self.args,'dry_run',False) or getattr(self.args,'smoke_ui',False))
        next_start='다음 일반 실행: '+saved+'로 시작합니다.'
        if forced: next_start+=' 현재 실행 옵션 --dry-run은 시작 시 관찰 모드를 강제합니다.'
        privilege='관리자 권한으로 실행 중입니다.' if self.admin else '실제 전원 제어를 선택하면 Windows 권한 승인을 요청합니다.'
        self.mode_feedback.config(text=(self.mode_message+'\n' if self.mode_message else '')+('현재 '+current+' 적용 중. ')+privilege+'\n'+next_start,
            fg=self.palette.amber if self.mode_message or pending or saved!=current else self.palette.muted)
        self.mode_badge.config(text='● 관찰 모드' if dry else '● 실제 전원 제어' if self.controller.engine.enabled else '○ 실제 모드 · 일시 중지',fg=self.palette.amber if dry or not self.controller.engine.enabled else self.palette.accent)

    def apply_execution_mode(self):
        wanted=self.execution_mode.get()=='관찰 모드'
        if not wanted and getattr(self.args,'smoke_ui',False):
            self.mode_message='UI 미리보기에서는 실제 전원 제어로 전환하지 않습니다.'
            self.execution_mode.set('관찰 모드');self.update_mode_feedback();return
        if not wanted and not self.admin:
            try:
                if request_admin_relaunch(self.args.data_dir,activate=True):
                    self.close();return
                self.mode_message='관리자 권한 요청이 취소되었습니다. 기존 모드와 자동 관리 상태를 유지합니다.'
            except (OSError,RuntimeError) as exc:
                self.mode_message='관리자 권한으로 다시 열지 못했습니다: '+str(exc)
            self.execution_mode.set('관찰 모드' if self.controller.power.dry_run else '실제 전원 제어')
            self.update_mode_feedback();return
        try:
            self.controller.set_execution_mode(wanted)
            self.mode_message='모드가 적용됐습니다. 자동 관리 켜짐·중지 상태는 유지됩니다.'
            # Do not retain an old save message claiming that the former mode is active.
            self.save_message=''
            self.notice.config(text='실행 모드를 전환했습니다. 전원 전환 대기 시간은 새로 계산합니다.',fg=self.palette.accent)
        except (ValueError,OSError,RuntimeError,sqlite3.Error) as exc:
            messagebox.showerror('모드 전환 실패',str(exc),parent=self.root)
        self.execution_mode.set('관찰 모드' if self.controller.power.dry_run else '실제 전원 제어')
        self.update_settings_feedback()

    def toggle_login_startup(self):
        wanted=self.start_on_login.get()
        try:
            startup.set_enabled(wanted)
            self.notice.config(text=('로그인 시 자동 실행을 켰습니다. Windows 로그인 후 앱이 다시 열립니다.'
                                     if wanted else '로그인 시 자동 실행을 껐습니다.'),fg=self.palette.accent)
        except (OSError,RuntimeError) as exc:
            try: self.start_on_login.set(startup.is_enabled())
            except OSError: self.start_on_login.set(not wanted)
            messagebox.showerror('자동 실행 설정 실패',str(exc),parent=self.root)

    def apply_delay(self):
        try:
            minutes=int(self.minutes.get())
            action=ACTIONS.get(self.action.get())
            self.controller.set_power_settings(minutes*60,action)
            self.save_failed=False
            self.save_message=f'✓ 저장 완료 · {time.strftime("%H:%M:%S")} · {minutes}분 후 {self.action.get()}'
            if self.controller.power.dry_run: self.save_message+=' · 관찰 모드라 실제 종료는 하지 않습니다.'
        except (ValueError,OSError,sqlite3.Error) as exc:
            self.save_failed=True
            self.save_message='저장 실패 · 기존 적용값을 유지합니다. 입력값과 저장소 상태를 확인해 주세요.'
            messagebox.showerror('설정 저장 실패',str(exc),parent=self.root)
        self.update_settings_feedback()

    def toggle(self):
        try:
            self.controller.set_enabled(not self.controller.engine.enabled)
            self.pause.config(text='자동 관리 끄기' if self.controller.engine.enabled else '자동 관리 켜기')
            self.notice.config(text=('자동 관리가 켜졌습니다. 작업 상태를 확인한 뒤 설정한 동작을 준비합니다.'
                if self.controller.engine.enabled else '자동 관리가 꺼졌습니다. 이제 Windows의 기존 전원 설정을 따릅니다.'),fg=self.palette.success)
            self.update_settings_feedback()
        except Exception as exc: messagebox.showerror('설정 오류',str(exc),parent=self.root)

    def connect(self):
        try:
            if os.environ.get('CLAUDE_CONFIG_DIR') or os.environ.get('CODEX_HOME'):
                raise RuntimeError('사용자 지정 AI 설정 경로가 감지되었습니다. 기본 경로 연결을 자동 적용하지 않습니다.')
            backups=install_hooks(Path.home(),self.command)
            self.notice.config(text='Codex·Claude 작업 알림 설정을 저장했습니다. AI 앱·터미널 세션을 새로 시작해 주세요.',fg=self.palette.success)
            messagebox.showinfo('Codex·Claude 감지 설정 완료',f'기존 설정을 보존하고 작업 시작·종료 알림 설정을 추가했습니다.\n백업 {len(backups)}개 생성.\n\nAI 앱·터미널 세션을 새로 시작해 주세요.\n새 작업 알림이 도착하면 감지 여부를 확인할 수 있습니다.',parent=self.root)
        except Exception as exc:
            messagebox.showerror('연결 설정 오류',str(exc),parent=self.root)

    def disconnect(self):
        try:
            install_hooks(Path.home(),self.command,remove=True)
            self.controller.set_enabled(False)
            self.notice.config(text='이 앱의 작업 알림 설정을 제거하고 자동 관리를 껐습니다.',fg=self.palette.muted)
            self.update_settings_feedback()
        except Exception as exc:
            messagebox.showerror('연결 해제 오류',str(exc),parent=self.root)

    def diagnose(self):
        try:
            data=self.controller.diagnostics()
            data['administrator']=self.admin
            data['completion_action']=self.controller.engine.action
            data['shutdown_notice']=self.controller.shutdown_notice
            data['last_error']=self.controller.error
            data['codex_log_status']=self.controller.reader.last_error or '로컬 이벤트 형식 관측 중 (버전 의존)'
            data['claude_connection_status']=self.claude_presence.last_error or '세션 ID/PID/프로세스 시작 시각으로 연결 확인'
            data['claude_display_status']=self.claude_details.last_error or '일치하는 로컬 세션의 공개 기록 표시 (전원 판단과 별도)'
            data['events_observed']=sorted(self.controller.live_providers)
            data['data_directory']=str(self.controller.store.root)
            data['recent_events']=self.controller.recent_events
            data['power_events']=self.controller.store.power_events()
            self.show_diagnostics(data)
        except Exception as exc:
            messagebox.showerror('진단 오류',str(exc),parent=self.root)

    def show_diagnostics(self,data):
        previous=getattr(self,'diagnostics_window',None)
        if previous is not None and previous.winfo_exists(): previous.destroy()
        dialog=tk.Toplevel(self.root);self.diagnostics_window=dialog
        dialog.title('상태·오류 확인');dialog.configure(bg=self.palette.card)
        dialog.transient(self.root)
        dialog.geometry(f'760x{min(680,self.root.winfo_screenheight()-100)}')
        dialog.minsize(580,450)
        body=tk.Frame(dialog,bg=self.palette.card,padx=24,pady=20);body.pack(fill='both',expand=True)
        self.label(body,'상태·오류 확인',size=13,bold=True,anchor='w').pack(fill='x')
        self.label(body,'현재 상태와 로컬 기록을 읽는 화면입니다. 전원 설정이나 작업 상태를 바꾸지 않습니다.',self.palette.muted,9,
            anchor='w',justify='left',wraplength=700).pack(fill='x',pady=(6,16))
        tabs=ttk.Notebook(body);tabs.pack(fill='both',expand=True)
        summary=tk.Frame(tabs,bg=self.palette.card,padx=12,pady=14)
        technical=tk.Frame(tabs,bg=self.palette.card)
        tabs.add(summary,text='읽기 쉬운 요약');tabs.add(technical,text='상세 기록')
        permission=data.get('shutdown_permission',{}).get('available')
        settings=data.get('settings',{})
        state=data.get('power_action',{})
        fields=[('전원 제어 모드','관찰 모드 · 실제 전원 변경 없음' if self.controller.power.dry_run else '실제 전원 제어'),
            ('자동 관리','켜짐' if self.controller.engine.enabled else '꺼짐 · 다시 사용하려면 상단에서 켜기'),
            ('완료 후 동작',f"{int(settings.get('delay',self.controller.engine.delay)/60)}분 후 "+('정상 종료' if settings.get('action',self.controller.engine.action)=='shutdown' else '절전')),
            ('종료 권한','사용 가능' if permission is True else '사용 불가 · Windows 권한 확인 필요' if permission is False else '검사 결과 없음'),
            ('마지막 오류',data.get('last_error') or state.get('message') or '기록된 오류 없음'),
            ('앱 버전',str(data.get('version','미확인'))),
            ('실행 정보',f"PID {data.get('pid','미확인')} · 실행 ID {data.get('run_id','미확인')}"),
            ('실제 데이터 파일',str(data.get('database_path',self.controller.store.path)))]
        summary.columnconfigure(1,weight=1)
        for index,(title,value) in enumerate(fields):
            self.label(summary,title,self.palette.muted,9,anchor='nw').grid(row=index,column=0,sticky='nw',padx=(0,18),pady=7)
            self.label(summary,value,self.palette.text,9,anchor='w',justify='left',wraplength=470).grid(row=index,column=1,sticky='ew',pady=7)
        report=json.dumps(data,ensure_ascii=False,indent=2)
        self.diagnostic_details=tk.Text(technical,height=12,bg=self.palette.inset,fg=self.palette.text,font=self.font(9),wrap='word',relief='flat',padx=16,pady=14,selectbackground=self.palette.selected,selectforeground=self.palette.text,insertbackground=self.palette.accent)
        scroll=ttk.Scrollbar(technical,orient='vertical',command=self.diagnostic_details.yview)
        self.diagnostic_details.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.diagnostic_details.pack(fill='both',expand=True)
        self.diagnostic_details.insert('1.0',report);self.diagnostic_details.config(state='disabled')
        footer=tk.Frame(body,bg=self.palette.card);footer.pack(side='bottom',fill='x',pady=(16,0),before=tabs)
        feedback=self.label(footer,'진단 내용은 이 PC에서만 확인합니다.',self.palette.muted,9);feedback.pack(anchor='w',pady=(0,8))
        buttons=tk.Frame(footer,bg=self.palette.card);buttons.pack(fill='x')
        def copy():
            self.root.clipboard_clear();self.root.clipboard_append(report)
            feedback.config(text='진단 내용을 복사했습니다.',fg=self.palette.success)
        self.button(buttons,'닫기',dialog.destroy).pack(side='right')
        self.button(buttons,'진단 내용 복사',copy,True).pack(side='right',padx=10)
        dialog.bind('<Escape>',lambda event:dialog.destroy())

    def elevate(self):
        self.execution_mode.set('실제 전원 제어')
        self.apply_execution_mode()

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
        # Cancel callbacks before destroying widgets, including repeated preview windows.
        for callback in self.root.tk.call('after','info'):
            self.root.after_cancel(callback)
        self.root.update_idletasks()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


