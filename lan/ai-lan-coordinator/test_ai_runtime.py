import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from ai_runtime import Runtime,AIGateError,AIUncertain,validate,save
from coordinator import Store,Rejected,execute_once


class FakeRuntime(Runtime):
    mode='success'
    def auth_status(self,provider): return {'ready':True,'status':'TEST_FIXTURE_NO_REAL_AUTH'}
    def command(self,payload,folder):
        script="from pathlib import Path; import sys,time; p=Path('calls.txt'); p.write_text('1'); "
        if self.mode=='fail': script+='sys.exit(17)'
        elif self.mode=='timeout': script+='time.sleep(20)'
        else:
            events=[{'type':'thread.started'},{'type':'turn.started'},
                    {'type':'item.completed','item':{'id':'fixture','type':'agent_message','text':'Bounded synthetic result'}},
                    {'type':'turn.completed'}]
            script+="Path('answer.txt').write_text('Bounded synthetic result',encoding='utf-8'); print("+repr('\n'.join(map(json.dumps,events)))+")"
        return [sys.executable,'-c',script]


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.policy={'providers':{p:{'enabled':True,'timeout_seconds':3,'max_calls_24h':3} for p in ('codex','gemini')},'output_max_bytes':16000}
        save(self.root/'ai-policy.json',self.policy)
        self.runtime=FakeRuntime(self.root)
        self.payload={'provider':'codex','operation':'summarize','text':'Synthetic input only.'}
    def tearDown(self): self.tmp.cleanup()

    def test_input_schema_and_credential_material_rejected(self):
        for bad in ({'provider':'codex','operation':'shell','text':'x'},
                    {**self.payload,'path':'C:/secret'},
                    {**self.payload,'text':'x'*12001},
                    {**self.payload,'text':'sk-'+('A'*24)}):
            with self.assertRaises(AIGateError): validate(bad)

    def test_disabled_provider_does_not_spawn(self):
        self.runtime.policy['providers']['codex']['enabled']=False
        with self.assertRaises(AIGateError): self.runtime.run('disabled',self.payload)
        self.assertFalse((self.runtime.private/'runs').exists())

    def test_result_cache_prevents_second_model_invocation_and_rejects_changed_input(self):
        first=self.runtime.run('same',self.payload)
        second=self.runtime.run('same',self.payload)
        self.assertEqual(first,second)
        self.assertEqual('1',(self.runtime.private/'runs/same/calls.txt').read_text(encoding='utf-8-sig'))
        with self.assertRaises(AIGateError): self.runtime.run('same',{**self.payload,'text':'changed'})
        db=self.runtime.ledger()
        self.assertEqual(1,db.execute('SELECT count(*) FROM runs').fetchone()[0])
        db.close()

    def test_uncertain_failure_cannot_repeat_or_fallback(self):
        self.runtime.mode='fail'
        with self.assertRaises(AIUncertain): self.runtime.run('failed',self.payload)
        self.runtime.mode='success'
        with self.assertRaises(AIUncertain): self.runtime.run('failed',self.payload)
        with self.assertRaises(AIGateError): self.runtime.run('another',self.payload)
        db=self.runtime.ledger()
        self.assertEqual('needs_review',db.execute("SELECT state FROM runs WHERE id='failed'").fetchone()[0])
        db.close()

    def test_local_call_budget_blocks_extra_request(self):
        self.runtime.policy['providers']['codex']['max_calls_24h']=1
        self.runtime.run('first',self.payload)
        with self.assertRaises(AIGateError): self.runtime.run('second',self.payload)

    def test_timeout_terminates_owned_process_and_marks_review(self):
        self.runtime.mode='timeout'
        self.runtime.policy['providers']['codex']['timeout_seconds']=.01
        with self.assertRaises(AIUncertain): self.runtime.run('timeout',self.payload)
        db=self.runtime.ledger()
        self.assertEqual('needs_review',db.execute("SELECT state FROM runs WHERE id='timeout'").fetchone()[0])
        db.close()

    def test_child_environment_isolated_without_changing_global_or_copying_keys(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fixture-only','GEMINI_API_KEY':'fixture-only'}):
            env=self.runtime.environment('codex')
            self.assertNotIn('OPENAI_API_KEY',env)
            self.assertNotIn('GEMINI_API_KEY',env)
            self.assertEqual('fixture-only',os.environ['OPENAI_API_KEY'])
            self.assertTrue(Path(env['CODEX_HOME']).is_relative_to(self.root/'private'))

    def test_cli_arguments_are_fixed_read_only_and_disable_delegation(self):
        real=Runtime(self.root)
        with patch.object(real,'paths',return_value={'codex':'codex.exe','node':'node.exe','gemini':'gemini.js'}):
            cmd=real.command(self.payload,self.root)
        self.assertIn('read-only',cmd)
        self.assertIn('--ignore-user-config',cmd)
        self.assertIn('multi_agent',cmd)
        self.assertIn('shell_tool',cmd)
        self.assertNotIn(self.payload['text'],cmd)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',cmd)

    def test_modified_gemini_policy_is_blocked(self):
        self.runtime.prepare()
        policy=self.runtime.private/'gemini-home/.gemini/policies/text-only.toml'
        policy.write_text('[[rule]]\ntoolName="*"\ndecision="allow"\n',encoding='utf-8')
        with self.assertRaises(AIGateError): self.runtime.prepare()


class MeteredQueueTests(unittest.TestCase):
    def test_heartbeat_failure_cancels_ai_and_persists_review_completion(self):
        class ClientFixture:
            identity={'name':'fixture'}
            def post(self,path,value):
                if path=='/claim': return {'job':{'id':'fixture-ai','fence':1,'tool':'ai_text','effect':'metered',
                                                  'payload':{'provider':'codex','operation':'summarize','text':'fixture'}}}
                if path=='/heartbeat': raise OSError('Injected connectivity loss')
                if path=='/complete':
                    self.completion=value
                    return {'state':'needs_review'}
        def bounded_fake_model(tool,payload,job_id,cancelled):
            deadline=time.monotonic()+2
            while not cancelled() and time.monotonic()<deadline: time.sleep(.01)
            if not cancelled(): raise AssertionError('Lease failure was not delivered to runner')
            raise AIUncertain('Fixture cancelled on lease failure')
        client=ClientFixture()
        with tempfile.TemporaryDirectory() as folder,patch('coordinator.run_tool',side_effect=bounded_fake_model):
            result=execute_once(client,Path(folder),heartbeat_interval=.01)
            self.assertEqual('needs_review',result['state'])
            self.assertTrue(client.completion['result']['requires_review'])
            self.assertEqual(1,len(list(Path(folder).glob('*.acked'))))

    def test_metered_job_not_retried_after_expiry_and_uncertain_ack_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            clock=[1000.]
            registry={'ai_text':{'enabled':True,'effect':'metered'}}
            store=Store(Path(folder)/'q.sqlite',clock=lambda:clock[0],registry=registry)
            payload={'provider':'codex','operation':'summarize','text':'fixture'}
            job=store.enqueue('one','ai_text',payload)
            claim=store.claim('A',['ai_text'],lease=2)
            self.assertEqual(1,claim['max_attempts'])
            failure={'requires_review':True,'error':'fixture'}
            ack=store.complete('A',job,claim['fence'],failure,False)
            self.assertEqual('needs_review',ack['state'])
            self.assertTrue(store.complete('A',job,claim['fence'],failure,False)['duplicate_ack'])
            store.enqueue('two','ai_text',payload,resource='different')
            store.claim('A',['ai_text'],lease=2)
            clock[0]+=3
            self.assertIsNone(store.claim('B',['ai_text']))
            states=[j['state'] for j in store.snapshot()['jobs']]
            self.assertEqual(['needs_review','needs_review'],states)


if __name__=='__main__': unittest.main(verbosity=2)
