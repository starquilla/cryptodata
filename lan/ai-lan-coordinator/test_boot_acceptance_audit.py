"""Synthetic audit fixtures only. These tests do NOT simulate an accepted real reboot."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import manage_boot_acceptance as m
from coordinator import Store,write_json


class AuditTests(unittest.TestCase):
    def run_case(self,users=0,pc2boot='2026-10-16T10:00:00Z',case='normal',offline=0,later_audit_boot=None):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);evidence=root/'evidence';auto=root/'auto'
            evidence.mkdir();auto.mkdir();folder=auto/'fixture';folder.mkdir()
            write_json(evidence/'baseline.json',{'protected':[]})
            write_json(root/'private/boot-v1/install.json',{'principal_sid':'SID-TEST'})
            store=Store(root/'private/coordinator/queue.sqlite')
            ids=[]
            for i in range(19):
                jid=store.enqueue('fixture-'+str(i),'hash_text',{'text':'fixture'},target=m.PC2)
                job=store.claim(m.PC2,['hash_text']);store.complete(m.PC2,job['id'],job['fence'],{'fixture':True});ids.append(jid)
            jid=store.enqueue('fixture-return','hash_text',{'text':'fixture'},target=m.PC1)
            job=store.claim(m.PC1,['hash_text']);store.complete(m.PC1,job['id'],job['fence'],{'fixture':True})
            write_json(folder/'state.json',{'status':'PASS','job_ids':ids,'return_job':jid})
            write_json(folder/'plan.json',{'files':[{'name':'fixture','sha256':'test-hash'}]})
            request={'session':'fixture','case':case,'armed_boot':'2026-10-16T09:00:00Z',
                     'pc2_prior_boot':'2026-10-16T08:00:00Z','input_hashes':{'fixture':'test-hash'}}
            write_json(evidence/'fixture-armed.json',request)
            boot='2026-10-16T10:01:00Z'
            snapshot=store.snapshot()
            first=min(e['at'] for e in snapshot['events'] if e['kind']=='claimed')
            last=max(e['at'] for e in snapshot['events'] if e['kind']=='completed')
            for role in ('worker','controller','coordinator'):
                status={'hostname':m.PC1,'role':role,'boot_utc':boot,'session_id':0,
                        'interactive_users':users,'pid':{'worker':1,'controller':2,'coordinator':3}[role],
                        'sid':'SID-TEST','offline_count':0}
                write_json(evidence/(role+'-start.json'),status)
                with store.db() as db:
                    db.execute('INSERT INTO events(at,job,kind,detail) VALUES(?,NULL,?,?)',((first+last)/2,'runtime_status',json.dumps({'worker':m.PC1,'peer':'127.0.0.1','status':status})))
            status={'hostname':m.PC2,'role':'worker','boot_utc':pc2boot,'session_id':0,
                    'interactive_users':users,'pid':4,'sid':'SID-PC2','offline_count':offline}
            with store.db() as db:
                db.execute('INSERT INTO events(at,job,kind,detail) VALUES(?,NULL,?,?)',((first+last)/2,'runtime_status',json.dumps({'worker':m.PC2,'peer':'192.168.1.9','status':status})))
            with patch.object(m,'ROOT',root),patch.object(m,'AUTO',auto),patch.object(m,'EVIDENCE',evidence),patch.object(m,'windows_context',return_value={'boot':later_audit_boot or boot}),patch.object(sys,'argv',['audit','audit','--session','fixture']),contextlib.redirect_stdout(io.StringIO()):
                m.main()
            return json.loads((evidence/'fixture-audit.json').read_text())

    def test_complete_prelogin_fixture_passes(self):
        self.assertEqual(self.run_case()['verdict'],'PASS')

    def test_login_screen_with_a_logged_on_user_is_hold(self):
        result=self.run_case(users=1)
        self.assertEqual(result['verdict'],'HOLD')
        self.assertFalse(result['checks']['pc1_before_login'])

    def test_unchanged_pc2_boot_is_hold(self):
        result=self.run_case(pc2boot='2026-10-16T08:00:00Z')
        self.assertEqual(result['verdict'],'HOLD')
        self.assertTrue(result['checks']['pc2_zero_users_during_work'])
        self.assertTrue(result['checks']['one_observed_runtime_pid_per_role'])
        self.assertFalse(result['checks']['pc2_boot_changed'])

    def test_later_operator_boot_does_not_erase_completed_work_boot(self):
        self.assertEqual(self.run_case(later_audit_boot='2026-10-16T12:00:00Z')['verdict'],'PASS')

    def test_old_boot_still_holds_even_when_recovery_and_zero_users_pass(self):
        result=self.run_case(pc2boot='2026-10-16T08:00:00Z',case='pc2-first',offline=5)
        self.assertEqual(result['verdict'],'HOLD')
        self.assertTrue(result['checks']['pc2_waited_offline_then_recovered'])
        self.assertTrue(result['checks']['pc2_zero_users_during_work'])
        self.assertFalse(result['checks']['pc2_booted_first'])

    def test_pc2_first_requires_offline_wait_evidence(self):
        self.assertEqual(self.run_case(case='pc2-first')['verdict'],'HOLD')
        self.assertEqual(self.run_case(case='pc2-first',offline=2)['verdict'],'PASS')


if __name__=='__main__':unittest.main(verbosity=2)
