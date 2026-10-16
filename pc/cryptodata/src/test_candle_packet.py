import copy, datetime as dt, json, sqlite3, unittest
from candle_packet import encode,import_packet
from test_packet_fixtures import NOW_MS, SCHEMAS, sample_rows

class PacketTests(unittest.TestCase):
    def setUp(self):
        self.rows=sample_rows()['candles']
        self.db=sqlite3.connect(':memory:');self.db.execute(SCHEMAS['candles'])
        self.mapping={tuple(self.rows[0][:6])};self.now=NOW_MS
    def tearDown(self):self.db.close()
    def packet(self,rows=None):return encode({'schema':1,'dataset':'candles_5m','rows':rows or self.rows})
    def put(self,raw):return import_packet(self.db,raw,self.mapping,self.now)
    def count(self):return self.db.execute('SELECT COUNT(*) FROM candles').fetchone()[0]
    def test_replay_idempotent(self):
        self.assertEqual(self.put(self.packet()),{'inserted':3,'duplicates':0})
        self.assertEqual(self.put(self.packet()),{'inserted':0,'duplicates':3})
    def test_corruption(self):
        p=json.loads(self.packet());p['payload']['rows'][0][7]='123'
        with self.assertRaises(ValueError):self.put(json.dumps(p).encode())
        self.assertEqual(self.count(),0)
    def test_wrong_source(self):
        rows=copy.deepcopy(self.rows);rows[0][0]='okx_spot'
        with self.assertRaises(ValueError):self.put(self.packet(rows))
    def test_conflict_rolls_back_prior_new_row(self):
        self.put(self.packet([self.rows[1]])); rows=copy.deepcopy(self.rows);rows[1][11]=str(float(rows[1][11])+1)
        with self.assertRaises(ValueError):self.put(self.packet(rows))
        self.assertEqual(self.count(),1)
    def test_open_candle(self):
        rows=copy.deepcopy(self.rows);rows[0][6]=self.now//300000*300000
        with self.assertRaises(ValueError):self.put(self.packet(rows))
    def test_duplicate_in_packet(self):
        with self.assertRaises(ValueError):self.put(self.packet([self.rows[0],self.rows[0]]))
    def test_nonfinite(self):
        rows=copy.deepcopy(self.rows);rows[0][7]='NaN'
        with self.assertRaises(ValueError):self.put(self.packet(rows))

if __name__=='__main__':unittest.main(verbosity=2)
