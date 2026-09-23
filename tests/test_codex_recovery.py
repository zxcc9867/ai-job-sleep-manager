import json,tempfile,unittest,time
from pathlib import Path
from unittest.mock import patch
from sleep_manager.codex_logs import CodexLogs
from sleep_manager.engine import Engine
SID='00000000-0000-0000-0000-000000000001'
def rec(tag,kind,**kw):return dict(type=tag,timestamp='2026-09-21T09:42:52Z',payload=dict(type=kind,**kw))
def start(turn='t'):return rec('event_msg','task_started',turn_id=turn)
def filler():return rec('response_item','function_call_output',call_id='huge',output='x'*2000)
def wait():return rec('response_item','function_call',name='request_user_input',call_id='ask')
def result():return rec('response_item','function_call_output',call_id='ask',output='answer')
class RecoveryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/('rollout-'+SID+'.jsonl');self.reader=CodexLogs(self.tmp.name)
 def write(self,rows):self.path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
 def poll(self):
  with patch('sleep_manager.codex_logs.MAX_READ',1024):return self.reader.poll()
 def test_initial_big_log_recovers_explicit_start(self):
  self.write([start(),filler(),rec('response_item','message',role='assistant',channel='commentary',content=[])])
  events=self.poll();self.assertEqual(events[-1]['kind'],'start');self.assertEqual(events[-1]['turn_id'],'t')
 def test_midrun_gap_recovers_current_turn_and_allows_later_completion(self):
  self.write([start()]);self.poll()
  with self.path.open('a',encoding='utf-8') as f:f.write(json.dumps(filler())+'\n'+json.dumps(rec('event_msg','token_count'))+'\n')
  self.assertEqual(self.poll()[-1]['kind'],'start')
  with self.path.open('a',encoding='utf-8') as f:f.write(json.dumps(rec('event_msg','task_complete',turn_id='t'))+'\n')
  self.assertEqual(self.poll()[-1]['kind'],'complete')
 def test_waiting_and_pending_response_survive_recovery(self):
  self.write([start(),wait(),filler(),rec('event_msg','token_count')]);events=self.poll()
  self.assertEqual(events[-1]['kind'],'waiting');self.assertEqual(events[-1]['turn_id'],'t')
  with self.path.open('a',encoding='utf-8') as f:f.write(json.dumps(result())+'\n')
  self.assertEqual(self.poll()[-1]['kind'],'running')
 def test_resolved_wait_does_not_remain_waiting(self):
  self.write([start(),wait(),result(),filler(),rec('event_msg','token_count')])
  self.assertEqual(self.poll()[-1]['kind'],'running')
 def test_old_completed_history_never_arms_on_open(self):
  self.write([start(),rec('event_msg','task_complete',turn_id='t'),rec('event_msg','token_count',padding='x'*2000),rec('event_msg','token_count')])
  self.reader.started=time.time()+86400
  self.assertEqual(self.poll(),[])
 def test_missing_boundary_and_display_text_are_not_running_evidence(self):
  self.write([rec('response_item','message',role='assistant',channel='commentary',content=[dict(type='output_text',text='working')]),filler(),rec('event_msg','token_count')])
  self.assertEqual(self.poll()[-1]['kind'],'unknown')
 def test_malformed_or_out_of_budget_remains_unknown(self):
  self.write([start(),filler(),rec('event_msg','token_count')])
  with patch('sleep_manager.codex_logs.MAX_RECOVERY',1024):self.assertEqual(self.poll()[-1]['kind'],'unknown')
  self.reader=CodexLogs(self.tmp.name)
  self.path.write_text(json.dumps(start())+'\nBROKEN\n'+json.dumps(filler())+'\n'+json.dumps(rec('event_msg','token_count'))+'\n',encoding='utf-8')
  self.assertEqual(self.poll()[-1]['kind'],'unknown')
 def test_turn_context_mismatch_rejects_recovery(self):
  self.write([start('old'),dict(type='turn_context',payload=dict(turn_id='new')),filler(),rec('event_msg','token_count')])
  self.assertEqual(self.poll()[-1]['kind'],'unknown')
 def test_newest_turn_overrides_previous_completion(self):
  self.write([start('old'),rec('event_msg','task_complete',turn_id='old'),start('new'),filler(),rec('event_msg','token_count')])
  events=self.poll();self.assertEqual(events[-1]['kind'],'start');self.assertEqual(events[-1]['turn_id'],'new')
 def test_recovered_wait_replaces_restored_previous_turn(self):
  self.write([start('new'),wait(),filler(),rec('event_msg','token_count')])
  engine=Engine();engine.ingest(dict(provider='codex',session_id=SID,kind='start',turn_id='old'),0)
  for event in self.poll():engine.ingest(event,1)
  self.assertEqual(engine.jobs['codex:'+SID].state,'waiting');self.assertEqual(engine.jobs['codex:'+SID].turn_id,'new')
 def test_multiple_recovered_questions_wait_until_all_results(self):
  ask2=rec('response_item','function_call',name='request_user_input',call_id='ask2')
  self.write([start(),wait(),ask2,filler(),rec('event_msg','token_count')]);self.poll()
  with self.path.open('a',encoding='utf-8') as f:f.write(json.dumps(result())+'\n')
  self.assertEqual(self.poll()[-1]['kind'],'waiting')
  with self.path.open('a',encoding='utf-8') as f:f.write(json.dumps(rec('response_item','function_call_output',call_id='ask2',output='ok'))+'\n')
  self.assertEqual(self.poll()[-1]['kind'],'running')
 def test_new_turn_discards_old_pending_question(self):
  self.write([start(),wait(),filler(),rec('event_msg','token_count')]);self.poll()
  with self.path.open('a',encoding='utf-8') as f:f.write(json.dumps(start('new'))+'\n'+json.dumps(result())+'\n')
  events=self.poll();self.assertEqual([e['kind'] for e in events],['start']);self.assertEqual(self.reader.pending,{})
