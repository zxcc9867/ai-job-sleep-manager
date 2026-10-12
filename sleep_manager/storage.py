"""Small per-user SQLite metadata queue, safe for simultaneous hook processes."""
import json
import os
from pathlib import Path
from .job_view import clean_text, project_name
import sqlite3
import time
import uuid
from .power_state import PreflightTransientError

FIELDS = {'provider','session_id','kind','task_id','turn_id','timestamp','background_active','metadata_complete','source','project','main_stop_candidate','parent_registry_empty'}

def data_directory():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'AIJobSleepManager'

class Store:
    def __init__(self, root):
        self.root=Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path=self.root/'events.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, received REAL NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS job_labels (provider TEXT NOT NULL, session_id TEXT NOT NULL, label TEXT NOT NULL, PRIMARY KEY(provider,session_id))')
            db.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS ui_preferences (id INTEGER PRIMARY KEY CHECK(id=1), font_size INTEGER NOT NULL)')
            db.execute("CREATE TABLE IF NOT EXISTS ui_theme (id INTEGER PRIMARY KEY CHECK(id=1), theme TEXT NOT NULL CHECK(theme IN ('light','dark')))")
            db.execute('CREATE TABLE IF NOT EXISTS runtime_state (id INTEGER PRIMARY KEY CHECK(id=1), boot_epoch REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS execution_mode (id INTEGER PRIMARY KEY CHECK(id=1), dry_run INTEGER NOT NULL CHECK(dry_run IN (0,1)))')
            db.execute('CREATE TABLE IF NOT EXISTS power_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, code TEXT NOT NULL, detail TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS power_action_state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')

    def connect(self):
        db=sqlite3.connect(self.path,timeout=5)
        db.execute('PRAGMA busy_timeout=5000')
        return db

    def append(self, event, event_id=None):
        safe={k:v for k,v in event.items() if k in FIELDS}
        if safe.get('provider') not in ('codex','claude') or not isinstance(safe.get('session_id'),str) or not safe['session_id']:
            raise ValueError('invalid metadata')
        if 'project' in safe: safe['project']=project_name(safe['project'])
        payload=json.dumps(safe,ensure_ascii=False,allow_nan=False)
        if len(payload)>8192:
            raise ValueError('metadata too large')
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO events(event_id,received,payload) VALUES (?,?,?)',(event_id or uuid.uuid4().hex,time.time(),payload))

    def after(self, seq, limit=2000):
        with self.connect() as db:
            return [(row[0],json.loads(row[1])) for row in db.execute('SELECT seq,payload FROM events WHERE seq>? ORDER BY seq LIMIT ?',(seq,limit))]

    def latest_sequence(self):
        with self.connect() as db:
            return db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]

    def recent(self, limit=10000):
        with self.connect() as db:
            rows=db.execute('SELECT seq,payload FROM events ORDER BY seq DESC LIMIT ?',(limit,)).fetchall()
            return [(seq,json.loads(payload)) for seq,payload in reversed(rows)]

    def settings(self):
        with self.connect() as db:
            row=db.execute('SELECT payload FROM settings WHERE id=1').fetchone()
            return json.loads(row[0]) if row else {}

    def save_settings(self, settings):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings(id,payload) VALUES (1,?)',(json.dumps(settings,allow_nan=False),))

    def power_action(self):
        with self.connect() as db:
            row=db.execute('SELECT payload FROM power_action_state WHERE id=1').fetchone()
            return json.loads(row[0]) if row else {}

    def save_power_action(self, payload, settings=None):
        """Commit the request intent and persisted disarm in the same transaction."""
        try:
            with self.connect() as db:
                # Use the existing save_settings contract for adapters and race tests.
                if settings is not None:
                    db.execute('INSERT OR REPLACE INTO settings(id,payload) VALUES (1,?)',
                               (json.dumps(settings,allow_nan=False),))
                db.execute('INSERT OR REPLACE INTO power_action_state(id,payload) VALUES (1,?)',
                           (json.dumps(payload,allow_nan=False),))
        except sqlite3.OperationalError as exc:
            code=getattr(exc,'sqlite_errorcode',None)
            if code is None:
                code=self._lock_probe()
            if code is not None and code & 255 in (5,6):
                raise PreflightTransientError('sqlite_busy') from exc
            raise

    def _lock_probe(self):
        """Python 3.10 lacks SQLite error codes. Probe the native numeric result.

        BEGIN/ROLLBACK writes no rows; never classify English exception text.
        Only the packaged/interpreter SQLite library is used, never PATH.
        """
        if os.name!='nt': return None
        import ctypes as C
        import sys
        candidates=[Path(getattr(sys,'_MEIPASS',sys.base_prefix))/'sqlite3.dll',
                    Path(sys.base_prefix)/'DLLs'/'sqlite3.dll']
        library=next((p for p in candidates if p.is_file()),None)
        if library is None: return None
        native=C.CDLL(str(library)); pointer=C.c_void_p()
        native.sqlite3_open_v2.argtypes=[C.c_char_p,C.POINTER(C.c_void_p),C.c_int,C.c_char_p]
        native.sqlite3_open_v2.restype=C.c_int
        native.sqlite3_exec.argtypes=[C.c_void_p,C.c_char_p,C.c_void_p,C.c_void_p,C.c_void_p]
        native.sqlite3_exec.restype=C.c_int
        native.sqlite3_close.argtypes=[C.c_void_p]; native.sqlite3_close.restype=C.c_int
        try:
            result=native.sqlite3_open_v2(str(self.path.resolve()).encode('utf-8'),C.byref(pointer),2,None)
            if result: return result
            result=native.sqlite3_exec(pointer,b'BEGIN IMMEDIATE',None,None,None)
            if result==0: native.sqlite3_exec(pointer,b'ROLLBACK',None,None,None)
            return result
        finally:
            if pointer: native.sqlite3_close(pointer)

    def record_power_event(self,code,detail):
        allowed={'reboot_detected','reboot_interrupted','user_confirmed','mode_changed',
                 'decision','shutdown_requested','shutdown_simulated','shutdown_failed','shutdown_deferred'}
        if code not in allowed: raise ValueError('Invalid power event')
        with self.connect() as db:
            db.execute('INSERT INTO power_events(at,code,detail) VALUES (?,?,?)',
                       (time.time(),code,str(detail)[:240]))
            db.execute('DELETE FROM power_events WHERE seq <= (SELECT COALESCE(MAX(seq),0)-200 FROM power_events)')

    def power_events(self,limit=20):
        with self.connect() as db:
            rows=db.execute('SELECT at,code,detail FROM power_events ORDER BY seq DESC LIMIT ?',(limit,)).fetchall()
            return [dict(at=at,code=code,detail=detail) for at,code,detail in rows]

    def boot_epoch(self):
        with self.connect() as db:
            row=db.execute('SELECT boot_epoch FROM runtime_state WHERE id=1').fetchone()
            return row[0] if row else None

    def save_boot_epoch(self,epoch):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO runtime_state(id,boot_epoch) VALUES (1,?)',(epoch,))

    def execution_dry_run(self):
        with self.connect() as db:
            row=db.execute('SELECT dry_run FROM execution_mode WHERE id=1').fetchone()
            return bool(row[0]) if row else True

    def save_execution_dry_run(self,dry_run):
        if not isinstance(dry_run,bool): raise ValueError('Mode must be a boolean')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO execution_mode(id,dry_run) VALUES (1,?)',(int(dry_run),))

    def text_size(self):
        with self.connect() as db:
            row=db.execute('SELECT font_size FROM ui_preferences WHERE id=1').fetchone()
            return row[0] if row and row[0] in (12,14,16) else 12

    def theme(self):
        with self.connect() as db:
            row=db.execute('SELECT theme FROM ui_theme WHERE id=1').fetchone()
            return row[0] if row and row[0] in ('light','dark') else 'light'

    def save_theme(self,theme):
        if theme not in ('light','dark'): raise ValueError('Unsupported theme')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO ui_theme(id,theme) VALUES (1,?)',(theme,))

    def set_text_size(self,size):
        if size not in (12,14,16): raise ValueError('Unsupported text size')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO ui_preferences(id,font_size) VALUES (1,?)',(size,))

    def labels(self):
        with self.connect() as db:
            return {provider+':'+session:label for provider,session,label in db.execute('SELECT provider,session_id,label FROM job_labels')}

    def set_label(self,provider,session,label):
        if provider not in ('codex','claude') or not isinstance(session,str) or not session:
            raise ValueError('Invalid job identity')
        label=clean_text(label,120)
        with self.connect() as db:
            if label:
                db.execute('INSERT OR REPLACE INTO job_labels VALUES (?,?,?)',(provider,session,label))
            else:
                db.execute('DELETE FROM job_labels WHERE provider=? AND session_id=?',(provider,session))

    def prune(self):
        with self.connect() as db:
            db.execute('DELETE FROM events WHERE received<? AND seq<(SELECT COALESCE(MAX(seq),0)-10000 FROM events)',(time.time()-7*86400,))

