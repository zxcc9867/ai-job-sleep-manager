"""Small per-user SQLite metadata queue, safe for simultaneous hook processes."""
import json
import os
from pathlib import Path
from .job_view import clean_text, project_name
import sqlite3
import time
import uuid

FIELDS = {'provider','session_id','kind','task_id','turn_id','timestamp','background_active','metadata_complete','source','project'}

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

