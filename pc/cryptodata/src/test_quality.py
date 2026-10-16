import importlib.util, pathlib, sqlite3, unittest
script=pathlib.Path(__file__).with_name('collect_pilot.py')
spec=importlib.util.spec_from_file_location('collector',script);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class QualityTests(unittest.TestCase):
    def test_valid_candle(self): m.validate_candle((300000,'10','12','9','11','0'))
    def test_invalid_values(self):
        for row in [(300001,'10','12','9','11','1'),(300000,'10','9','8','11','1'),(300000,'10','12','9','11','-1'),(300000,'NaN','12','9','11','1')]:
            with self.assertRaises(ValueError):m.validate_candle(row)
    def test_source_isolation_and_duplicate_key(self):
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE f(source TEXT,symbol TEXT,time INTEGER,value TEXT,PRIMARY KEY(source,symbol,time))')
        for row in [('A','BTC',1,'1'),('A','BTC',1,'1'),('B','BTC',1,'2')]:db.execute('INSERT OR IGNORE INTO f VALUES (?,?,?,?)',row)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM f').fetchone()[0],2)
        db.close()
if __name__=='__main__':unittest.main()

