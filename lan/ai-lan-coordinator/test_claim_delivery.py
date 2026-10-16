import json
from pathlib import Path
import tempfile
import unittest
import threading
from unittest.mock import patch
import coordinator as c

class ClaimDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=[1000.]
        self.store=c.Store(self.root/'queue.sqlite',clock=lambda:self.now[0])
        self.job=self.store.enqueue('delivery','hash_text',{'text':'lost claim response fixture'})
        owner=self
        class Local:
            identity={'name':'PC2_FIXTURE'}
            drop_claim=False;drop_ack=False
            def post(self,path,value):
                if path=='/claim':
                    kw={'request_id':value['request_id']} if 'request_id' in value else {}
                    job=owner.store.claim(self.identity['name'],['hash_text'],**kw)
                    if self.drop_claim and job:
                        self.drop_claim=False;raise TimeoutError('injected response lost AFTER claim committed')
                    return {'job':job}
                if path=='/heartbeat':owner.store.heartbeat(self.identity['name'],value['id'],value['fence']);return {'ok':True}
                if path=='/complete':
                    reply=owner.store.complete(self.identity['name'],value['id'],value['fence'],value['result'],value['success'])
                    if self.drop_ack:self.drop_ack=False;raise TimeoutError('injected ACK response lost')
                    return reply
        self.client=Local();self.out=self.root/'out'
    def tearDown(self):self.tmp.cleanup()
    def test_lost_claim_recovers_same_job_without_waiting_for_expiry(self):
        self.client.drop_claim=True
        with self.assertRaises(TimeoutError):c.execute_once(self.client,self.out)
        self.now[0]+=5
        recovered=c.execute_once(self.client,self.out)
        self.assertIsNotNone(recovered,'Lost claim response leaves worker polling empty until lease expiry')
        self.assertEqual(self.job,recovered['id'])
        snap=self.store.snapshot();job=snap['jobs'][0]
        self.assertEqual(('succeeded',1,1),(job['state'],job['attempt'],job['fence']))
        self.assertFalse(any(e['kind']=='lease_expired' for e in snap['events']))
    def test_request_replay_renews_same_fence(self):
        first=self.store.claim('A',['hash_text'],request_id='a'*32)
        self.now[0]+=25
        replay=self.store.claim('A',['hash_text'],request_id='a'*32)
        self.assertEqual((first['id'],1,1),(replay['id'],replay['attempt'],replay['fence']))
        self.now[0]+=10
        self.store.complete('A',first['id'],1,{'ok':True})
    def test_request_id_is_worker_scoped(self):
        first=self.store.claim('A',['hash_text'],request_id='a'*32)
        self.store.enqueue('other','hash_text',{'text':'second'},resource='second')
        other=self.store.claim('B',['hash_text'],request_id='a'*32)
        self.assertNotEqual(first['id'],other['id']);self.assertEqual('B',other['owner'])
    def test_expired_request_never_revives_old_fence(self):
        first=self.store.claim('A',['hash_text'],request_id='a'*32);self.now[0]+=31
        with self.assertRaises(c.Rejected):self.store.claim('A',['hash_text'],request_id='a'*32)
        new=self.store.claim('B',['hash_text'],request_id='b'*32)
        self.assertEqual(3,new['fence'])
        with self.assertRaises(c.Rejected):self.store.complete('A',first['id'],1,{})
    def test_ack_lost_replays_without_second_tool_execution(self):
        self.client.drop_ack=True;original=c.run_tool
        with patch.object(c,'run_tool',wraps=original) as tool:
            with self.assertRaises(TimeoutError):c.execute_once(self.client,self.out)
            self.assertIsNone(c.execute_once(self.client,self.out))
            self.assertEqual(1,tool.call_count)
        self.assertEqual(1,self.store.snapshot()['jobs'][0]['attempt'])
    def test_unfinished_execution_marker_never_silently_reexecutes(self):
        self.out.mkdir();c.write_json(self.out/'.claim-request.json',dict(worker='PC2_FIXTURE',request_id='a'*32,phase='executing',job_id=self.job,fence=1))
        with patch.object(c,'run_tool') as tool:
            with self.assertRaises(c.Rejected):c.execute_once(self.client,self.out)
            tool.assert_not_called()
    def test_marker_write_failure_prevents_tool_and_allows_safe_delivery_retry(self):
        original=c.write_json
        def fail_marker(path,value):
            if value.get('phase')=='executing':raise OSError('injected marker fsync error')
            return original(path,value)
        with patch.object(c,'write_json',side_effect=fail_marker),patch.object(c,'run_tool') as tool:
            with self.assertRaises(OSError):c.execute_once(self.client,self.out)
            tool.assert_not_called()
        self.assertEqual(self.job,c.execute_once(self.client,self.out)['id'])
        self.assertEqual(1,self.store.snapshot()['jobs'][0]['attempt'])
    def test_empty_queue_does_not_accumulate_claim_files(self):
        c.execute_once(self.client,self.out)
        for _ in range(3):self.assertIsNone(c.execute_once(self.client,self.out))
        self.assertFalse((self.out/'.claim-request.json').exists())
    def test_concurrent_duplicate_claim_request_is_one_assignment(self):
        results=[];barrier=threading.Barrier(2)
        def claim():
            barrier.wait();results.append(self.store.claim('A',['hash_text'],request_id='d'*32))
        ts=[threading.Thread(target=claim) for _ in range(2)]
        for t in ts:t.start()
        for t in ts:t.join()
        self.assertEqual(2,len(results));self.assertEqual({(self.job,1,1)},{(j['id'],j['attempt'],j['fence']) for j in results})
        self.assertEqual(1,sum(e['kind']=='claimed' for e in self.store.snapshot()['events']))
    def test_invalid_request_id_is_rejected_before_assignment(self):
        with self.assertRaises(c.Rejected):self.store.claim('A',['hash_text'],request_id='../unsafe')
        self.assertEqual('queued',self.store.snapshot()['jobs'][0]['state'])
    def test_replay_respects_disabled_capability(self):
        self.store.claim('A',['hash_text'],request_id='a'*32)
        self.store.registry['hash_text']['enabled']=False
        with self.assertRaises(c.Rejected):self.store.claim('A',['hash_text'],request_id='a'*32)

class RealHTTPSDeliveryTests(unittest.TestCase):
    def test_committed_claim_response_lost_over_pinned_https(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);store=c.Store(root/'queue.sqlite');server,pin=c.make_server(store,root/'tls',port=0)
            t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
            real=c.Client('https://127.0.0.1:'+str(server.server_port),pin)
            code=store.prepare_enrollment('HTTPS_FIXTURE',['hash_text']);token=real.post('/enroll',{'name':'HTTPS_FIXTURE','code':code})['token']
            real.identity={'name':'HTTPS_FIXTURE','token':token}
            jid=store.enqueue('real-claim-loss','hash_text',{'text':'fixture'})
            class LoseFirst:
                identity=real.identity;lost=False
                def post(self,path,value):
                    result=real.post(path,value)
                    if path=='/claim' and result.get('job') and not self.lost:
                        self.lost=True;raise TimeoutError('injected lost claim response')
                    return result
            client=LoseFirst()
            try:
                with self.assertRaises(TimeoutError):c.execute_once(client,root/'out')
                self.assertEqual(jid,c.execute_once(client,root/'out')['id'])
                snap=store.snapshot();j=snap['jobs'][0]
                self.assertEqual(('succeeded',1,1),(j['state'],j['attempt'],j['fence']))
                self.assertEqual(1,sum(e['kind']=='claim_replayed' for e in snap['events']))
            finally:server.shutdown();server.server_close();t.join(timeout=3)

if __name__=='__main__':unittest.main(verbosity=2)
