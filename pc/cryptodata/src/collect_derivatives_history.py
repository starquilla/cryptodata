"""Historical derivatives addon; separate tables preserve source field semantics."""
import json,sqlite3,time
from decimal import Decimal
from collect_pilot import R,Collector,utc

def collect(c,source,asset,symbol):
 db=c.db; end=c.end; start=end-7*86400000
 for kind in ['funding','oi_history']:
  entry={'source':source,'symbol':symbol,'asset':asset,'dataset':kind,'status':'running'}
  try:
   records={};cursor=None;left=start;right=end-1;complete=False;seen_cursors=set();pagination_stop='page_budget_exhausted'
   expected=set(range(((start+3599999)//3600000)*3600000,end,3600000)) if kind=='oi_history' else set()
   pending_windows=[(start,end-1)]
   for page in range(30):
    if kind=='funding':
     if source=='binance_perpetual':
      rows=c.get(source,'https://fapi.binance.com/fapi/v1/fundingRate',{'symbol':symbol,'startTime':left,'endTime':right,'limit':1000});limit=1000
      timestamps=[int(x['fundingTime']) for x in rows]
     else:
      rows=c.get(source,'https://api.bybit.com/v5/market/funding/history',{'category':'linear','symbol':symbol,'startTime':left,'endTime':right,'limit':200})['result']['list'];limit=200
      timestamps=[int(x['fundingRateTimestamp']) for x in rows]
     for x,t in zip(rows,timestamps):records[t]=(source,symbol,t,x['fundingRate'],utc(),c.id)
     if len(rows)<limit:complete=True;break
     if source=='binance_perpetual':
      nxt=max(timestamps)+1
      if nxt<=left:raise ValueError('pagination stalled')
      left=nxt
     else:
      nxt=min(timestamps)-1
      if nxt>=right:raise ValueError('pagination stalled')
      right=nxt
     if left>right:complete=True;break
    else:
     if source=='binance_perpetual':
      left,right=pending_windows.pop(0)
      try:rows=c.get(source,'https://fapi.binance.com/futures/data/openInterestHist',{'symbol':symbol,'period':'1h','startTime':left,'endTime':right,'limit':500})
      except Exception as e:pagination_stop='request_error';entry['pagination_error']=str(e);break
      limit=500
      known_before=len(expected & records.keys())
      for x in rows:
       t=int(x['timestamp'])
       if start<=t<end and left<=t<=right:records[t]=(source,symbol,'1h',t,x['sumOpenInterest'],'base_asset','sumOpenInterest','provider_native_unverified',x.get('sumOpenInterestValue'),utc(),c.id)
      if expected<=records.keys():complete=True;pagination_stop='expected_slots_covered';break
      if rows:
       if len(expected & records.keys())==known_before:pagination_stop='no_progress';break
       missing=sorted(t for t in expected if left<=t<=right and t not in records)
       groups=[]
       for t in missing:
        if groups and t==groups[-1][1]+3600000:groups[-1]=(groups[-1][0],t)
        else:groups.append((t,t))
       pending_windows.extend((first,min(last+3599999,right)) for first,last in groups)
      if not pending_windows:complete=True;pagination_stop='queried_windows_exhausted';break
     else:
      params={'category':'linear','symbol':symbol,'intervalTime':'1h','startTime':left,'endTime':right,'limit':200}
      if cursor:params['cursor']=cursor
      try:response=c.get(source,'https://api.bybit.com/v5/market/open-interest',params)
      except Exception as e:pagination_stop='request_error';entry['pagination_error']=str(e);break
      result=response['result'];rows=result['list']
      for x in rows:
       t=int(x['timestamp']);field='singleOpenInterest' if x.get('singleOpenInterest') is not None else 'openInterest'
       if start<=t<end:records[t]=(source,symbol,'1h',t,x[field],'base_asset',field,'single_side' if field=='singleOpenInterest' else 'both_sides',None,utc(),c.id)
      nxt=result.get('nextPageCursor')
      if not nxt:complete=True;pagination_stop='cursor_exhausted';break
      if nxt==cursor or nxt in seen_cursors:pagination_stop='cursor_cycle';break
      seen_cursors.add(nxt)
      cursor=nxt
   values=list(records.values())
   for row in values:
    v=Decimal(row[3] if kind=='funding' else row[4])
    if not v.is_finite() or (kind!='funding' and v<0):raise ValueError('invalid metric')
   if kind=='funding':db.executemany('INSERT OR IGNORE INTO funding VALUES (?,?,?,?,?,?)',values)
   else:db.executemany('INSERT OR REPLACE INTO oi_history VALUES (?,?,?,?,?,?,?,?,?,?,?)',values)
   db.commit();entry.update(status='available' if complete else 'partial',rows=len(values),pages=page+1,window_start=start,window_end=end,earliest=min(records) if records else None,latest=max(records) if records else None)
   if kind=='oi_history':
    entry['pagination_stop']=pagination_stop;entry['missing_hour_slots']=len(expected-set(records))
    if entry['missing_hour_slots']:entry['status']='partial'
  except Exception as e:db.rollback();entry.update(status='error',error=str(e))
  c.coverage.append(entry)

def main():
 import msvcrt
 lock=(R/'state/incremental.lock').open('a+b');lock.seek(0)
 try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
 except OSError:lock.close();return 3
 try:
  c=Collector(7)
  c.db.execute('CREATE TABLE IF NOT EXISTS oi_history(source TEXT,symbol TEXT,interval TEXT,time INTEGER,value TEXT,unit TEXT,provider_field TEXT,counting_convention TEXT,provider_quote_value TEXT,observed_at TEXT,run_id TEXT,PRIMARY KEY(source,symbol,interval,time))')
  mapping=json.loads((R/'config/instrument-map.json').read_text())
  for key,symbol in mapping.items():
   source,asset=key.split('|')
   if source in ('binance_perpetual','bybit_perpetual'):collect(c,source,asset,symbol)
  bad=[x for x in c.coverage if x['status'] in ('error','partial')]
  c.db.execute('UPDATE runs SET status=? WHERE run_id=?',('partial' if bad else 'completed',c.id));c.db.commit()
  count=c.db.execute('SELECT COUNT(*) FROM oi_history').fetchone()[0];c.db.close()
  report={'run_id':c.id,'seconds':round(time.monotonic()-c.started,2),'history_rows':count,'issues':bad,'coverage':c.coverage,'requests':c.calls,'cross_venue_aggregation_enabled':False}
  (R/'reports/derivatives-history.json').write_text(json.dumps(report,indent=2),encoding='utf8')
  print(json.dumps({k:v for k,v in report.items() if k not in ('coverage','requests')},indent=2))
  return 2 if bad else 0
 finally:
  lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1);lock.close()
if __name__=='__main__':raise SystemExit(main())
