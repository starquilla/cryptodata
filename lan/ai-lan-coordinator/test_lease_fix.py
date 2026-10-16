import json
import socket
import tempfile
import threading
import time
import unittest
import io
import sys
from pathlib import Path
from unittest.mock import patch
import coordinator as c

class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.now=[1000.];self.lock=threading.Lock()
        self.store=c.Store(self.root/'queue.sqlite',clock=lambda:self.now[0])
        self.jid=self.store.enqueue('real-read','hash_text',{'text':'fixture'})
        self.beats=0;self.renewed=threading.Event()
        owner=self
        class Local:
            identity={'name':'read-fixture'}
            def post(self,path,value):
                if path=='/claim':return {'job':owner.store.claim(self.identity['name'],['hash_text'])}
                if path=='/heartbeat':
                    owner.store.heartbeat(self.identity['name'],value['id'],value['fence']);owner.beats+=1;owner.renewed.set();return {'ok':True}
                if path=='/complete':return owner.store.complete(self.identity['name'],value['id'],value['fence'],value['result'],value['success'])
        self.client=Local()
    def tearDown(self):self.tmp.cleanup()
    def delay(self):
        for _ in range(12):
            # Advance virtual time only after a confirmed renewal. OS scheduling
            # and real fsync latency must not silently leap the fake clock by48s.
            self.renewed.clear();self.now[0]+=4
            self.assertTrue(self.renewed.wait(3),'No renewal while the protected phase was blocked')
    def assert_single(self):
        j=self.store.snapshot()['jobs'][0]
        self.assertEqual(('succeeded',1,1),(j['state'],j['attempt'],j['fence']))
        self.assertGreater(self.beats,0)
        self.assertGreaterEqual(self.now[0],1048.0)
        self.assertEqual(1,len(list((self.root/'out').glob('*.acked'))))
    def test_read_tool_over_30_seconds_renews(self):
        original=c.run_tool
        def slow(*args):self.delay();return original(*args)
        with patch.object(c,'run_tool',side_effect=slow):c.execute_once(self.client,self.root/'out',heartbeat_interval=.005)
        self.assert_single()
    def test_durable_outbox_over_30_seconds_renews(self):
        original=c.write_json
        def slow(*args):
            if Path(args[0]).name.endswith(".pending.json"):self.delay()
            return original(*args)
        with patch.object(c,'write_json',side_effect=slow):c.execute_once(self.client,self.root/'out',heartbeat_interval=.005)
        self.assert_single()
    def test_completion_exchange_over_30_seconds_renews(self):
        original=self.client.post
        def slow(path,value):
            if path=='/complete':self.delay()
            return original(path,value)
        self.client.post=slow
        c.execute_once(self.client,self.root/'out',heartbeat_interval=.005)
        self.assert_single()
    def test_stale_fence_still_rejected(self):
        job=self.client.post('/claim',{})['job'];self.now[0]+=31
        next_job=self.store.claim('another',['hash_text'])
        with self.assertRaises(c.Rejected):self.store.heartbeat('read-fixture',job['id'],job['fence'])
        with self.assertRaises(c.Rejected):self.store.complete('read-fixture',job['id'],job['fence'],{})
        self.assertEqual(3,next_job['fence'])
    def test_write_failure_leaves_no_renewal_thread(self):
        with patch.object(c,'write_json',side_effect=OSError('fixture disk unavailable')):
            with self.assertRaises(OSError):c.execute_once(self.client,self.root/'out',heartbeat_interval=.005)
        beats=self.beats;time.sleep(.03);self.assertEqual(beats,self.beats)

class TLSAcceptTests(unittest.TestCase):
    def test_real_clock_35_second_durable_write_keeps_same_fence(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);store=c.Store(root/'queue.sqlite')
            server,pin=c.make_server(store,root/'tls',port=0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            code=store.prepare_enrollment('slow-disk-fixture',['hash_text'])
            client=c.Client('https://127.0.0.1:'+str(server.server_port),pin)
            token=client.post('/enroll',{'name':'slow-disk-fixture','code':code})['token']
            client.identity={'name':'slow-disk-fixture','token':token}
            store.enqueue('slow-disk-real-clock','hash_text',{'text':'offline fixture'})
            original=c.write_json
            def slow(*args):
                if Path(args[0]).name.endswith(".pending.json"):time.sleep(35)
                return original(*args)
            try:
                with patch.object(c,'write_json',side_effect=slow):c.execute_once(client,root/'out')
                job=store.snapshot()['jobs'][0]
                self.assertEqual(('succeeded',1,1),(job['state'],job['attempt'],job['fence']))
                self.assertEqual(1,len(list((root/'out').glob('*.acked'))))
            finally:
                server.shutdown();server.server_close();thread.join(timeout=3)

    def test_idle_tls_peer_does_not_block_valid_pinned_request(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);store=c.Store(root/'queue.sqlite')
            server,pin=c.make_server(store,root/'tls',port=0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            idle=socket.create_connection(('127.0.0.1',server.server_port))
            time.sleep(.1)
            try:
                code=store.prepare_enrollment('offline-fixture',['hash_text'])
                start=time.monotonic()
                result=c.Client('https://127.0.0.1:'+str(server.server_port),pin).post('/enroll',{'name':'offline-fixture','code':code})
                self.assertIn('token',result);self.assertLess(time.monotonic()-start,1.5)
            finally:
                idle.close();server.shutdown();server.server_close();thread.join(timeout=3)

class LoggingTests(unittest.TestCase):
    def test_worker_network_failure_has_timestamp_without_secret(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);identity=root/'identity.json';identity.write_text(json.dumps({'name':'fixture','token':'never-print-me'}))
            err=io.StringIO()
            with patch.object(sys,'argv',['coordinator.py','worker','--url','https://127.0.0.1:1','--pin','0'*64,'--identity',str(identity),'--outbox',str(root/'out'),'--watch']),patch('runtime_singleton.acquire_once'),patch.object(c,'execute_once',side_effect=OSError('never-print-me')),patch.object(c.time,'sleep',side_effect=KeyboardInterrupt),patch.object(sys,'stderr',err):
                with self.assertRaises(KeyboardInterrupt):c.main()
            event=json.loads(err.getvalue())
            self.assertEqual('coordinator_unavailable',event['event'])
            self.assertIn('at',event);self.assertNotIn('never-print-me',err.getvalue())

if __name__=='__main__':unittest.main(verbosity=2)
