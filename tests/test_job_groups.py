import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sleep_manager.engine import Job, Engine
from sleep_manager.job_view import JobCatalog, job_row, group_jobs, group_summary, observe_details


def row(sid, state='running', parent='', provider='codex'):
    return job_row(Job(provider,sid,state=state),dict(title=sid,parent_id=parent),'sleep')


class JobGroupTests(unittest.TestCase):
    def test_two_main_jobs_and_one_child(self):
        rows=group_jobs([row('reading'),row('stock'),row('review',parent='stock')])
        counts=group_summary(rows)
        self.assertEqual((counts['running'],counts['child_running']),(2,1))
        self.assertEqual(next(r for r in rows if r['session_id']=='review')['parent_key'],'codex:stock')

    def test_completed_parent_keeps_running_descendants_visible_without_changing_engine(self):
        engine=Engine()
        engine.ingest(dict(provider='codex',session_id='stock',kind='complete'),0)
        engine.ingest(dict(provider='codex',session_id='review',kind='start'),0)
        before=engine.tick(1,3600)
        rows=group_jobs([job_row(j,dict(parent_id='stock' if j.session_id=='review' else ''),'sleep') for j in engine.jobs.values()])
        parent=next(r for r in rows if r['session_id']=='stock')
        self.assertTrue(parent['pending'])
        self.assertEqual(parent['category'],'running')
        self.assertIn('하위',parent['state'])
        self.assertEqual(engine.jobs['codex:stock'].state,'complete')
        self.assertEqual(engine.tick(1,3600),before)

    def test_nested_waiting_and_unknown_propagate_to_root(self):
        rows=group_jobs([row('main','complete'),row('child','complete','main'),row('nested','unknown','child')])
        self.assertEqual(group_summary(rows)['unknown'],1)
        self.assertEqual(group_summary(rows)['child_unknown'],1)
        self.assertTrue(all(r['pending'] for r in rows))
        rows=group_jobs([row('main','complete'),row('child','waiting','main')])
        self.assertEqual(group_summary(rows)['waiting'],1)

    def test_missing_parent_is_child_not_a_third_main_task(self):
        rows=group_jobs([row('orphan',parent='absent')])
        self.assertEqual(group_summary(rows)['running'],0)
        self.assertEqual(group_summary(rows)['child_running'],1)
        self.assertTrue(rows[0]['is_child'])
        self.assertEqual(rows[0]['parent_key'],'')
        self.assertIn('부모',rows[0]['relation'])

    def test_cycles_and_self_parent_do_not_hide_or_recurse(self):
        rows=group_jobs([row('a',parent='b'),row('b',parent='a'),row('self',parent='self')])
        self.assertEqual(len(rows),3)
        self.assertTrue(all(not r['parent_key'] for r in rows))
        self.assertTrue(all(r['pending'] for r in rows))

    def test_same_session_id_in_other_provider_is_not_parent(self):
        rows=group_jobs([row('p',provider='claude'),row('c',parent='p')])
        self.assertEqual(next(r for r in rows if r['session_id']=='c')['parent_key'],'')

    def test_source_metadata_is_read_only_and_malformed_source_is_safe(self):
        with tempfile.TemporaryDirectory() as d:
            db=sqlite3.connect(Path(d)/'state_5.sqlite')
            db.execute('create table threads(id text,title text,cwd text,source text)')
            source=json.dumps({'subagent':{'thread_spawn':{'parent_thread_id':'main'}}})
            db.executemany('insert into threads values(?,?,?,?)',[('child','검토','app',source),('bad','작업','app','{broken')])
            db.commit(); db.close()
            result=JobCatalog(d).lookup([Job('codex','child'),Job('codex','bad')])
            self.assertEqual(result['codex:child']['parent_id'],'main')
            self.assertEqual(result['codex:bad']['title'],'작업')
            self.assertFalse(result['codex:bad'].get('parent_id'))

    def test_session_metadata_fallback_keeps_parent_relation(self):
        info={}
        observe_details(info,dict(type='session_meta',payload=dict(source={'subagent':{'thread_spawn':{'parent_thread_id':'p'}}})))
        self.assertEqual(info['parent_id'],'p')



@unittest.skipUnless(__import__('os').name=='nt','Windows Tk UI')
class GroupedUITests(unittest.TestCase):
    def test_counts_tree_collapse_selection_and_completed_parent(self):
        from types import SimpleNamespace
        from sleep_manager.controller import Controller
        from sleep_manager.storage import Store
        from sleep_manager.ui import Window
        from test_app import Power,Reader
        with tempfile.TemporaryDirectory() as d:
            c=Controller(Store(Path(d)),Power(),Reader())
            for sid in ('reading','stock','review'):
                c._accept(dict(provider='codex',session_id=sid,kind='start'))
            w=Window(c,SimpleNamespace(smoke_ui=False,data_dir=Path(d)),[])
            w.root.withdraw()
            w.catalog=SimpleNamespace(lookup=lambda jobs:{'codex:review':dict(title='검토 · Mencius',parent_id='stock')})
            try:
                w.render_jobs()
                w.root.geometry('1080x600'); w.root.update_idletasks()
                w.page_canvas.yview_moveto(1)
                self.assertAlmostEqual(w.page_canvas.yview()[1],1,places=2)
                w.page_canvas.yview_moveto(0)
                self.assertEqual(w.table.parent('codex:review'),'codex:stock')
                self.assertEqual(len(w.table.get_children()),2)
                self.assertEqual(w.jobs_heading.cget('text'),'진행 중인 작업 2개 · 하위 작업 1개')
                self.assertEqual(w.metric_values[0].cget('text'),'2')
                self.assertEqual(w.metric_values[1].cget('text'),'1')
                w.table.item('codex:stock',open=False)
                w.table.selection_set('codex:reading')
                w.render_jobs()
                self.assertFalse(w.table.item('codex:stock','open'))
                self.assertEqual(w.table.selection(),('codex:reading',))
                c._accept(dict(provider='codex',session_id='stock',kind='complete'))
                w.render_jobs()
                self.assertTrue(w.table.exists('codex:stock'))
                self.assertIn('하위 작업 중',w.table.item('codex:stock','values'))
                w.table.selection_set('codex:review'); w.show_job_details()
                self.assertIn('상위 작업:',w.details.get('1.0','end'))
                c._accept(dict(provider='codex',session_id='review',kind='complete'))
                w.render_jobs()
                self.assertFalse(w.table.exists('codex:stock'))
                w.show_complete.set(True); w.render_jobs()
                self.assertEqual(w.table.parent('codex:review'),'codex:stock')
                # A removed parent must not delete its still-observed child.
                del c.engine.jobs['codex:stock']
                w.render_jobs()
                self.assertTrue(w.table.exists('codex:review'))
                self.assertEqual(w.table.parent('codex:review'),'')
            finally: w.close()
