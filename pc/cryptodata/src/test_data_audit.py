"""Isolated fixtures: funding polls, source gaps, missing backup, stale branches."""
import datetime as dt,json,pathlib,sqlite3,tempfile,unittest
from data_audit import audit,save
class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name);self.now=dt.datetime(2026,10,14,6,tzinfo=dt.timezone.utc);self.ms=int(self.now.timestamp()*1000)
        for name in ('config','normalized','raw/run','reports','state'):(self.root/name).mkdir(parents=True)
        save(self.root/'config/settings.json',{'assets':['BTC'],'automatic_drive_backup_enabled':True,'collection_interval_hours':2,'allowed_lateness_minutes':30,'local_storage_budget_bytes':50e9})
        save(self.root/'config/instrument-map.json',{'binance_spot|BTC':'BTCUSDT','binance_perpetual|BTC':'BTCUSDT'})
        self.db=sqlite3.connect(self.root/'normalized/market.sqlite3')
        self.db.executescript('CREATE TABLE candles(source,symbol,quote,open_time,observed_at);CREATE TABLE funding(source,symbol,time,observed_at);CREATE TABLE oi(source,symbol,time,observed_at);CREATE TABLE oi_history(source,symbol,time,observed_at,provider_field,counting_convention);')
        for i in (1,2,3):self.db.execute('INSERT INTO candles VALUES (?,?,?,?,?)',('binance_spot','BTCUSDT','USDT',self.ms-i*300000,self.now.isoformat()))
        old=self.now-dt.timedelta(hours=8)
        self.db.execute('INSERT INTO funding VALUES (?,?,?,?)',('binance_perpetual','BTCUSDT',self.ms-8*3600000,old.isoformat()))
        self.db.execute('INSERT INTO oi VALUES (?,?,?,?)',('binance_perpetual','BTCUSDT',self.ms-60000,self.now.isoformat()))
        self.db.execute('INSERT INTO oi_history VALUES (?,?,?,?,?,?)',('binance_perpetual','BTCUSDT',self.ms-3600000,self.now.isoformat(),'sumOpenInterest','unverified'));self.db.commit()
        requests=[]
        for source in ('defillama_protocols','defillama_chains','defillama_stablecoins'):
            path=self.root/'raw/run'/(source+'.gz');path.write_bytes(b'fixture');requests.append({'source':source,'status':'ok','file':str(path),'observed_at':self.now.isoformat()})
        save(self.root/'reports/main.json',{'requests':requests,'coverage':[{'source':'binance_perpetual','symbol':'BTCUSDT','dataset':'funding','status':'available'}]})
        save(self.root/'state/latest.json',{'report':'main.json','finished_at':self.now.isoformat(),'health':'ok'})
        save(self.root/'reports/derivatives-history.json',{'run_id':self.now.strftime('%Y%m%dT%H%M%S%fZ'),'issues':[]})
        save(self.root/'reports/daily-drive-backup.json',{'verified_at':self.now.isoformat(),'cloud_upload_verified':True,'cloud_restore_test':'passed'})
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def test_funding_successful_poll_not_old_payment(self):
        h,m=audit(self.root,self.now);self.assertEqual(h['issues'],[]);self.assertEqual(next(x for x in m if x['dataset']=='funding' and x['source']=='binance_perpetual')['status'],'available')
    def test_other_source_cannot_fill_hole(self):
        self.db.execute('DELETE FROM candles WHERE open_time=?',(self.ms-600000,));self.db.execute('INSERT INTO candles VALUES (?,?,?,?,?)',('okx_spot','BTCUSDT','USDT',self.ms-600000,self.now.isoformat()));self.db.commit()
        h,m=audit(self.root,self.now);self.assertIn('binance_spot|BTC|candles_5m:partial',h['issues'])
    def test_stale_all_branches_and_backup(self):
        h,m=audit(self.root,self.now+dt.timedelta(hours=27));self.assertIn('backup:older_than_26h_or_missing',h['issues']);self.assertIn('derivatives:report_missing_or_stale',h['issues']);self.assertTrue(any('raw_snapshot:stale' in x for x in h['issues']))
    def test_absent_backup_not_ok(self):
        (self.root/'reports/daily-drive-backup.json').unlink();h,m=audit(self.root,self.now);self.assertIn('backup:not_verified',h['issues'])
if __name__=='__main__':unittest.main()
