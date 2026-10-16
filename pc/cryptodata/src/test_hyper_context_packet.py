import datetime as dt
import hashlib
import json
import sqlite3
import unittest
from hyper_context_packet import SYMBOLS,initialize_sandbox,import_packet

NOW=dt.datetime(2026,10,15,8,tzinfo=dt.timezone.utc)


def fixture(change=None):
    p={'schema':1,'dataset':'hyperliquid_context_raw','packet_id':'test-packet','source':'hyperliquid_perpetual','observed_at':'2026-10-15T07:59:00Z','provider_timestamp':None,'unit_status':'vendor fields retained; semantic acceptance pending','rows':[{'symbol':s,'context':{'markPx':'1','openInterest':'0','funding':'-0.001'}} for s in sorted(SYMBOLS)]}
    if change:change(p)
    text=json.dumps(p,separators=(',',':'));return json.dumps({'schema':1,'payload_text':text,'sha256':hashlib.sha256(text.encode()).hexdigest()}).encode()


class PacketTests(unittest.TestCase):
    def setUp(self):self.db=sqlite3.connect(':memory:');initialize_sandbox(self.db)
    def tearDown(self):self.db.close()
    def test_replay_no_duplicates(self):
        raw=fixture();self.assertEqual(import_packet(self.db,raw,NOW),{'inserted':27,'duplicates':0})
        self.assertEqual(import_packet(self.db,raw,NOW),{'inserted':0,'duplicates':27})
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM pilot_hyper_context').fetchone()[0],27)
    def test_conflict_does_not_overwrite(self):
        import_packet(self.db,fixture(),NOW)
        with self.assertRaises(ValueError):import_packet(self.db,fixture(lambda p:p['rows'][0]['context'].update(markPx='2')),NOW)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM pilot_hyper_context').fetchone()[0],27)
    def test_corruption_rejected(self):
        raw=json.loads(fixture());raw['sha256']='0'*64
        with self.assertRaises(ValueError):import_packet(self.db,json.dumps(raw).encode(),NOW)
    def test_semantics_mapping_timestamp_and_numbers_rejected(self):
        mutations=[lambda p:p.update(source='bybit_perpetual'),lambda p:p.update(provider_timestamp=1),lambda p:p.update(observed_at='2026-10-16T00:00:00Z'),lambda p:p['rows'][0].update(symbol='UNKNOWN'),lambda p:p['rows'][0].update(symbol=p['rows'][1]['symbol']),lambda p:p['rows'][0]['context'].update(openInterest='-1'),lambda p:p['rows'][0]['context'].update(funding='NaN'),lambda p:p['rows'][0]['context'].update(markPx=True)]
        for change in mutations:
            with self.subTest(change=change),self.assertRaises(ValueError):import_packet(self.db,fixture(change),NOW)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM pilot_packets').fetchone()[0],0)
    def test_database_failure_rolls_back_entire_packet(self):
        self.db.execute("CREATE TRIGGER force_error BEFORE INSERT ON pilot_hyper_context WHEN NEW.symbol='SOL' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):import_packet(self.db,fixture(),NOW)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM pilot_packets').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM pilot_hyper_context').fetchone()[0],0)
    def test_duplicate_json_keys_rejected(self):
        raw=fixture().replace(b'"schema": 1',b'"schema": 1,"schema": 1',1)
        with self.assertRaises(ValueError):import_packet(self.db,raw,NOW)


if __name__=='__main__':unittest.main()
