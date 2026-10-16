"""Recovery regression using disposable synthetic databases only."""
import gzip, pathlib, sqlite3, tempfile, unittest
from collect_incremental import Incremental, STEP
from collect_pilot import Collector
from test_packet_fixtures import SCHEMAS, sample_rows

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        for table,rows in sample_rows().items():
            self.db.execute(SCHEMAS[table])
            self.db.executemany('INSERT INTO '+table+' VALUES ('+','.join('?'*len(rows[0]))+')',rows)
        self.db.commit()
    def tearDown(self):self.db.close()
    def test_hole_repair_replay_and_outage_preserve_rows(self):
        row=sample_rows()['candles'][1]
        self.db.execute('DELETE FROM candles WHERE open_time=?',(row[6],));self.db.commit()
        c=object.__new__(Incremental);c.db=self.db;c.start=row[6];c.end=row[6]+STEP
        c.id='offline-recovery-test';c.coverage=[];c.mapping={'binance_spot|BTC':'BTCUSDT'}
        c.get=lambda *a,**k:[[row[6],*row[7:12],row[6]+STEP-1]]
        Collector.run_candles(c,'binance_spot','BTC','BTCUSDT','USDT')
        self.assertEqual(c.coverage[-1]['status'],'complete')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM candles WHERE open_time=?',(row[6],)).fetchone()[0],1)
        n=self.db.execute('SELECT COUNT(*) FROM candles').fetchone()[0]
        Collector.run_candles(c,'binance_spot','BTC','BTCUSDT','USDT')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM candles').fetchone()[0],n)
        def outage(*a,**k):raise TimeoutError('simulated outage')
        c.get=outage
        Collector.run_candles(c,'binance_spot','BTC','BTCUSDT','USDT')
        self.assertEqual(c.coverage[-1]['status'],'error')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM candles').fetchone()[0],n)
    def test_backup_restore_integrity_and_exact_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path=pathlib.Path(folder);snapshot=path/'snapshot.sqlite3'
            conn=sqlite3.connect(snapshot);self.db.backup(conn);conn.close()
            backup=path/'snapshot.sqlite3.gz'
            with gzip.open(backup,'wb') as f:f.write(snapshot.read_bytes())
            restored=path/'restored.sqlite3'
            with gzip.open(backup,'rb') as f:restored.write_bytes(f.read())
            conn=sqlite3.connect(restored)
            try:
                self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                for table in SCHEMAS:
                    self.assertEqual(conn.execute('SELECT * FROM '+table).fetchall(),self.db.execute('SELECT * FROM '+table).fetchall())
            finally:conn.close()

if __name__=='__main__':unittest.main(verbosity=2)
