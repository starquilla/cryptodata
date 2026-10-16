import sqlite3,unittest
from types import SimpleNamespace
from collect_derivatives_history import collect
class Tests(unittest.TestCase):
 def test_bybit_pagination_and_semantics(self):
  db=sqlite3.connect(':memory:');db.execute('CREATE TABLE funding(source,symbol,time,rate,observed_at,run_id,PRIMARY KEY(source,symbol,time))');db.execute('CREATE TABLE oi_history(source,symbol,interval,time,value,unit,provider_field,counting_convention,provider_quote_value,observed_at,run_id,PRIMARY KEY(source,symbol,interval,time))')
  calls=[]
  def get(s,u,p):
   calls.append(p)
   if 'funding' in u:return {'result':{'list':[]}}
   if not p.get('cursor'):return {'result':{'list':[{'timestamp':'3600000','openInterest':'20','singleOpenInterest':'10'}],'nextPageCursor':'next'}}
   return {'result':{'list':[{'timestamp':'7200000','openInterest':'30'}],'nextPageCursor':''}}
  c=SimpleNamespace(db=db,end=7*86400000,id='test',coverage=[],get=get)
  collect(c,'bybit_perpetual','BTC','BTCUSDT')
  self.assertEqual(db.execute('SELECT value,counting_convention FROM oi_history ORDER BY time').fetchall(),[('10','single_side'),('30','both_sides')])
  self.assertEqual(c.coverage[-1]['status'],'partial')
  collect(c,'bybit_perpetual','BTC','BTCUSDT');self.assertEqual(db.execute('SELECT COUNT(*) FROM oi_history').fetchone()[0],2)
  self.assertTrue(any(p.get('cursor')=='next' for p in calls));db.close()
if __name__=='__main__':unittest.main()

