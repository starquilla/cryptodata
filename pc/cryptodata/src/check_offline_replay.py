"""Replay saved real rows on an isolated DB only; not a cloud ingestion acceptance test."""
import datetime as dt,hashlib,json,pathlib,sqlite3
R=pathlib.Path(r'E:\CryptoData tai nguyen')
TABLES=('candles','funding','oi','oi_history','daily_candles','defi_metrics','venue_snapshots','perpetual_context')
def main():
 source=sqlite3.connect('file:'+str(R/'normalized/market.sqlite3').replace('\\','/')+'?mode=ro',uri=True)
 target=sqlite3.connect(':memory:');checks=[]
 try:
  for table in TABLES:
   schema=source.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()[0]
   target.execute(schema)
   rows=source.execute('SELECT * FROM '+table+' LIMIT 3').fetchall()
   if not rows:raise ValueError('no fixture rows for '+table)
   sql='INSERT OR IGNORE INTO '+table+' VALUES ('+','.join('?' for _ in rows[0])+')'
   target.executemany(sql,rows);target.commit()
   before=target.execute('SELECT * FROM '+table).fetchall()
   for _ in range(2):target.executemany(sql,rows);target.commit()
   after=target.execute('SELECT * FROM '+table).fetchall()
   assert after==before and len(after)==len(rows),table
   checks.append({'table':table,'fixture_rows':len(rows),'replays':3,'count_unchanged':True,'content_unchanged':True})
  assert target.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 finally:target.close();source.close()
 report={'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'status':'PASS','checks':checks,'production_database_modified':False,'scope':'existing database key constraints with real fixture rows; cloud packet importer NOT_IMPLEMENTED/NOT_TESTED'}
 (R/'reports/offline-replay-check.json').write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
