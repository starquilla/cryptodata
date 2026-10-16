import copy, datetime as dt, json, sqlite3, unittest
from test_packet_fixtures import NOW_MS, SCHEMAS, sample_rows
from candle_packet import encode
from derivative_packet import import_packet

class Tests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.rows={t:rows for t,rows in sample_rows().items() if t in ('funding','oi_history')}
        for t in self.rows:self.db.execute(SCHEMAS[t])
        self.mapping={tuple(r[:2]) for rows in self.rows.values() for r in rows};self.semantics={}
        for r in self.rows['oi_history']:self.semantics.setdefault(tuple(r[:2]),set()).add(tuple(r[5:8]))
        self.now=NOW_MS
    def tearDown(self):self.db.close()
    def put(self,t,rows):return import_packet(self.db,encode({'schema':1,'dataset':t,'rows':rows}),self.mapping,self.semantics,self.now)
    def test_replay_both(self):
        for t,rows in self.rows.items():
            self.assertEqual(self.put(t,rows)['inserted'],3);self.assertEqual(self.put(t,rows)['duplicates'],3)
    def test_oi_unit_not_substituted(self):
        rows=copy.deepcopy(self.rows['oi_history']);rows[0][5]='USD'
        with self.assertRaises(ValueError):self.put('oi_history',rows)
    def test_settled_not_current_funding(self):
        with self.assertRaises(ValueError):self.put('funding_current',self.rows['funding'])
    def test_negative_funding_valid(self):
        rows=copy.deepcopy(self.rows['funding']);rows[0][3]='-0.001';self.assertEqual(self.put('funding',rows)['inserted'],3)
    def test_future_rejected(self):
        rows=copy.deepcopy(self.rows['funding']);rows[0][2]=self.now+100000
        with self.assertRaises(ValueError):self.put('funding',rows)
    def test_conflict_atomic(self):
        for t,rows in self.rows.items():
            self.put(t,[rows[1]]);changed=copy.deepcopy(rows);i=4 if t=='oi_history' else 3;changed[1][i]=str(float(changed[1][i])+1)
            with self.assertRaises(ValueError):self.put(t,changed)
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0],1)
if __name__=='__main__':unittest.main(verbosity=2)
