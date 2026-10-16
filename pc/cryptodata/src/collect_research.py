"""Daily UTC candles and source-labelled DeFi metrics, for multi-month research."""
import datetime as dt,gzip,json,math,sqlite3,sys,time
from decimal import Decimal
from collect_pilot import R,Collector,utc
from data_audit import save,load
DAY=86400000
def normalize_defi(db):
    count=0;errors=[]
    protocol_scope=set(load(R/'config/defi-protocol-scope.json',{}).get('protocol_ids',[]))
    for reportpath in sorted((R/'reports').glob('*-incremental.json')):
        report=load(reportpath,{})
        for req in report.get('requests',[]):
            source=req.get('source','')
            if not source.startswith('defillama_') or req.get('status')!='ok':continue
            stamp=req['observed_at'];key=req.get('sha256',req.get('file'))+'|'+stamp
            if db.execute('SELECT 1 FROM processed_defi WHERE file_key=?',(key,)).fetchone():continue
            try:
                path=R/'raw'/__import__('pathlib').Path(req['file']).parent.name/__import__('pathlib').Path(req['file']).name
                data=json.loads(gzip.decompress(path.read_bytes()));entries=data.get('peggedAssets',[]) if isinstance(data,dict) else data
                values=[]
                for x in entries:
                    entity=str(x.get('slug') or x.get('id') or x.get('name'));name=str(x.get('name',entity))
                    if source=='defillama_protocols' and protocol_scope and entity not in protocol_scope:continue
                    candidates=[('tvl_usd',x.get('tvl'))] if source!='defillama_stablecoins' else [('price_usd',x.get('price'))]
                    if source=='defillama_stablecoins':
                        for peg,amount in x.get('circulating',{}).items():candidates.append(('circulating_'+peg,amount))
                    for metric,value in candidates:
                        if value is None:continue
                        v=float(value)
                        if not math.isfinite(v) or v<0:raise ValueError('invalid nonnegative metric')
                        values.append((source,entity,name,metric,stamp,str(value),key))
                db.executemany('INSERT OR IGNORE INTO defi_metrics VALUES (?,?,?,?,?,?,?)',values)
                db.execute('INSERT INTO processed_defi VALUES (?,?)',(key,stamp));db.commit();count+=len(values)
            except Exception as e:db.rollback();errors.append({'source':source,'file':req.get('file'),'error':str(e)})
    return count,errors
def main():
    import msvcrt
    lock=(R/'state/incremental.lock').open('a+b');lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:lock.close();return 3
    try:
        c=Collector(7);c.db.executescript('''CREATE TABLE IF NOT EXISTS daily_candles(source TEXT,symbol TEXT,base TEXT,quote TEXT,open_time INTEGER,open TEXT,high TEXT,low TEXT,close TEXT,base_volume TEXT,observed_at TEXT,run_id TEXT,PRIMARY KEY(source,symbol,open_time));
        CREATE TABLE IF NOT EXISTS defi_metrics(source TEXT,entity_id TEXT,name TEXT,metric TEXT,observed_at TEXT,value TEXT,file_key TEXT,PRIMARY KEY(source,entity_id,metric,observed_at));
        CREATE TABLE IF NOT EXISTS processed_defi(file_key TEXT PRIMARY KEY,observed_at TEXT);''')
        coverage=[];end=int(time.time()*1000)//DAY*DAY
        for key,symbol in load(R/'config/instrument-map.json',{}).items():
            source,asset=key.split('|')
            if not source.endswith('_spot'):continue
            entry={'asset':asset,'source':source,'symbol':symbol,'dataset':'candles_1d'}
            try:
                latest=c.db.execute('SELECT MAX(open_time) FROM daily_candles WHERE source=? AND symbol=?',(source,symbol)).fetchone()[0]
                start=max(end-200*DAY,latest-DAY) if latest else end-200*DAY
                sample=c.db.execute('SELECT quote FROM candles WHERE source=? AND symbol=? LIMIT 1',(source,symbol)).fetchone()
                if not sample:raise ValueError('no frozen quote evidence')
                if source=='binance_spot':
                    raw=c.get(source,'https://data-api.binance.vision/api/v3/klines',{'symbol':symbol,'interval':'1d','startTime':start,'endTime':end-1,'limit':1000})
                    rows=[(int(x[0]),*x[1:6]) for x in raw if int(x[6])<end]
                else:
                    raw=c.get(source,'https://www.okx.com/api/v5/market/history-candles',{'instId':symbol,'bar':'1Dutc','after':end,'limit':300})['data']
                    rows=[(int(x[0]),*x[1:6]) for x in raw if x[8]=='1' and start<=int(x[0])<end]
                for t,o,h,l,cl,v in rows:
                    nums=[Decimal(x) for x in (o,h,l,cl,v)]
                    if t%DAY or any(not x.is_finite() for x in nums) or min(nums[:4])<=0 or nums[4]<0 or nums[1]<max(nums[0],nums[2],nums[3]) or nums[2]>min(nums[0],nums[3]):raise ValueError('invalid daily candle')
                values=[(source,symbol,asset,sample[0],*r,utc(),c.id) for r in rows]
                c.db.executemany('INSERT OR REPLACE INTO daily_candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',values);c.db.commit()
                entry.update(status='available' if rows else 'empty',rows=len(rows),window_start=start,window_end=end,latest=max((r[0] for r in rows),default=None))
            except Exception as e:entry.update(status='error',error=str(e))
            coverage.append(entry)
        n,errors=normalize_defi(c.db);bad=[x for x in coverage if x['status']!='available']
        c.db.execute('UPDATE runs SET status=? WHERE run_id=?',('partial' if bad or errors else 'completed',c.id));c.db.commit();c.db.close()
        save(R/'reports/research-collection.json',{'run_id':c.id,'finished_at':utc(),'coverage':coverage,'issues':bad+errors,'defi_insert_attempts':n,'seconds':round(time.monotonic()-c.started,2),'requests':c.calls,'identity':'no symbol-only DeFi asset joins'})
        print(json.dumps({'series':len(coverage),'issues':bad+errors,'defi_insert_attempts':n}));return 2 if bad or errors else 0
    finally:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1);lock.close()
if __name__=='__main__':sys.exit(main())
