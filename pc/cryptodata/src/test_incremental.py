import sqlite3,unittest
from collect_incremental import candle_start,STEP
class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.execute('CREATE TABLE candles(source TEXT,symbol TEXT,interval TEXT,open_time INTEGER)')
    def put(self,times,source='A'):
        self.db.executemany('INSERT INTO candles VALUES (?,?,?,?)',[(source,'BTC','5m',t) for t in times])
    def test_tail_overlap(self):
        self.put([0,STEP,2*STEP]);self.assertEqual(candle_start(self.db,'A','BTC',4*STEP,0),STEP)
    def test_hole_before_tail_is_repaired_first(self):
        self.put([0,STEP,3*STEP,4*STEP]);self.assertEqual(candle_start(self.db,'A','BTC',6*STEP,0),2*STEP)
    def test_other_venue_does_not_fill_gap(self):
        self.put([0,2*STEP]);self.put([0,STEP,2*STEP],'B');self.assertEqual(candle_start(self.db,'A','BTC',4*STEP,0),STEP)
    def test_bootstrap(self):self.assertEqual(candle_start(self.db,'A','BTC',10*STEP,2*STEP),2*STEP)
    def tearDown(self):self.db.close()
if __name__=='__main__':unittest.main()

