"""Public read-only pilot: seven days of candles, funding and current OI.
Data and reports belong to RESOURCE_ROOT. No private endpoints or scheduling.
"""
import argparse, csv, datetime as dt, gzip, hashlib, json, math, pathlib, shutil, sqlite3, time,os
import urllib.error, urllib.parse, urllib.request
from decimal import Decimal
R = pathlib.Path(os.environ.get('CRYPTODATA_RESOURCE_ROOT',r'E:\CryptoData tai nguyen'))
STEP = 300000

def utc(): return dt.datetime.now(dt.timezone.utc).isoformat()
def ms(): return int(time.time()*1000)
def validate_candle(row):
    t,o,h,l,c,v=row
    vals=[Decimal(str(x)) for x in (o,h,l,c,v)]
    if t % STEP or any(not x.is_finite() for x in vals): raise ValueError('invalid candle timestamp/value')
    o,h,l,c,v=vals
    if min(o,h,l,c)<=0 or v<0 or h<max(o,l,c) or l>min(o,h,c): raise ValueError('invalid candle OHLCV')

class Collector:
    def __init__(self, days, replay=None):
        self.cfg=json.loads((R/'config/settings.json').read_text(encoding='utf-8-sig'))
        if self.cfg['trading_enabled'] or len(self.cfg['assets'])>self.cfg['max_assets']: raise ValueError('unsafe configuration')
        total=sum(p.stat().st_size for p in R.rglob('*') if p.is_file())
        if total>self.cfg['local_storage_budget_bytes']*.8 or shutil.disk_usage(R).free<2_000_000_000: raise RuntimeError('storage guard')
        self.id=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        self.raw=R/'raw'/('pilot-'+self.id); self.raw.mkdir(parents=True)
        self.end=(ms()//STEP)*STEP; self.start=self.end-days*86400000
        self.started=time.monotonic(); self.calls=[]; self.coverage=[]; self.counter=0
        self.db=sqlite3.connect(R/'normalized/market.sqlite3')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS candles(source TEXT,market TEXT,symbol TEXT,base TEXT,quote TEXT,interval TEXT,open_time INTEGER,open TEXT,high TEXT,low TEXT,close TEXT,base_volume TEXT,observed_at TEXT,run_id TEXT,PRIMARY KEY(source,market,symbol,interval,open_time));
        CREATE TABLE IF NOT EXISTS funding(source TEXT,symbol TEXT,time INTEGER,rate TEXT,observed_at TEXT,run_id TEXT,PRIMARY KEY(source,symbol,time));
        CREATE TABLE IF NOT EXISTS oi(source TEXT,symbol TEXT,time INTEGER,value TEXT,unit TEXT,observed_at TEXT,run_id TEXT,PRIMARY KEY(source,symbol,time));
        CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY,start_time INTEGER,end_time INTEGER,started_at TEXT,status TEXT);
        ''')
        self.db.execute('INSERT INTO runs VALUES (?,?,?,?,?)',(self.id,self.start,self.end,utc(),'running')); self.db.commit()
    def get(self, source, base, params=None,body=None):
        url=base+('?' + urllib.parse.urlencode(params) if params else '')
        self.counter+=1
        item={'source':source,'url':url,'observed_at':utc(),'attempts':0}
        before=time.monotonic()
        try:
            for attempt in range(3):
                item['attempts']+=1
                try:
                    req=urllib.request.Request(url,data=json.dumps(body).encode() if body else None,headers={'User-Agent':'CryptoDataResearch/0.2','Content-Type':'application/json'})
                    with urllib.request.urlopen(req,timeout=20) as response:
                        raw=response.read(64000001); item['http_status']=response.status
                    if len(raw)>64000000: raise ValueError('response too large')
                    data=json.loads(raw)
                    path=self.raw/('%04d-%s.json.gz'%(self.counter,source)); path.write_bytes(gzip.compress(raw))
                    item.update(file=str(path),raw_bytes=len(raw),compressed_bytes=path.stat().st_size,sha256=hashlib.sha256(raw).hexdigest())
                    if isinstance(data,dict):
                        if 'retCode' in data and data['retCode']!=0: raise ValueError(str(data.get('retMsg')))
                        if 'code' in data and str(data['code'])!='0': raise ValueError(str(data.get('msg')))
                    item['status']='ok'; return data
                except urllib.error.HTTPError as e:
                    if e.code in (418,403,451): raise
                    if e.code!=429 and e.code<500: raise
                    if attempt==2: raise
                    delay=min(30,float(e.headers.get('Retry-After','2')))+attempt*2; time.sleep(delay)
                except (TimeoutError,urllib.error.URLError):
                    if attempt==2: raise
                    time.sleep(2**attempt)
        except Exception as e:
            item.update(status='error',error=str(e)); raise
        finally:
            item['seconds']=round(time.monotonic()-before,3); self.calls.append(item); time.sleep(.2)
    def run_candles(self, source, asset, symbol, quote):
        entry={'source':source,'asset':asset,'symbol':symbol,'dataset':'candles_5m','identity':'catalog_base_quote_only','status':'running'}
        fetched={}; pages=0
        try:
            cursor=self.start if source=='binance_spot' else self.end
            for _ in range(30):
                if source=='binance_spot':
                    data=self.get(source,'https://data-api.binance.vision/api/v3/klines',{'symbol':symbol,'interval':'5m','startTime':cursor,'endTime':self.end-1,'limit':1000})
                    rows=[(int(x[0]),*x[1:6]) for x in data if int(x[6])<self.end]
                else:
                    data=self.get(source,'https://www.okx.com/api/v5/market/history-candles',{'instId':symbol,'bar':'5m','after':cursor,'limit':300})['data']
                    rows=[(int(x[0]),*x[1:6]) for x in data if x[8]=='1']
                pages+=1
                if not data: break
                for row in rows:
                    if self.start<=row[0]<self.end:
                        validate_candle(row); fetched[row[0]]=row
                if source=='binance_spot':
                    nxt=max(int(x[0]) for x in data)+STEP
                    if nxt<=cursor: raise ValueError('pagination did not progress')
                    cursor=nxt
                    if cursor>=self.end: break
                else:
                    nxt=min(int(x[0]) for x in data)
                    if nxt>=cursor: raise ValueError('pagination did not progress')
                    cursor=nxt
                    if cursor<=self.start: break
            now=utc()
            values=[(source,'spot',symbol,asset,quote,'5m',*row,now,self.id) for row in fetched.values()]
            self.db.executemany('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,market,symbol,interval,open_time) DO UPDATE SET open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,base_volume=excluded.base_volume,observed_at=excluded.observed_at,run_id=excluded.run_id',values)
            self.db.commit()
            expected=(self.end-self.start)//STEP
            entry.update(status='complete' if len(fetched)==expected else 'partial',rows=len(fetched),expected=expected,missing=expected-len(fetched),pages=pages)
        except Exception as e: entry.update(status='error',error=str(e),rows_received=len(fetched),pages=pages)
        self.coverage.append(entry)
        print(json.dumps(entry),flush=True)
    def derivatives(self, source, asset, symbol):
        for kind in ('funding','oi'):
            entry={'source':source,'asset':asset,'symbol':symbol,'dataset':kind,'identity':'catalog_base_quote_only'}
            try:
                now=utc(); records=[]
                if source=='binance_perpetual':
                    if kind=='funding':
                        rows=self.get(source,'https://fapi.binance.com/fapi/v1/fundingRate',{'symbol':symbol,'startTime':self.start,'endTime':self.end-1,'limit':1000})
                        records=[(source,symbol,int(x['fundingTime']),x['fundingRate'],now,self.id) for x in rows]
                        entry['possibly_truncated']=len(rows)==1000
                    else:
                        x=self.get(source,'https://fapi.binance.com/fapi/v1/openInterest',{'symbol':symbol})
                        records=[(source,symbol,int(x['time']),x['openInterest'],'base_asset',now,self.id)]
                else:
                    if kind=='funding':
                        rows=self.get(source,'https://api.bybit.com/v5/market/funding/history',{'category':'linear','symbol':symbol,'startTime':self.start,'endTime':self.end-1,'limit':200})['result']['list']
                        records=[(source,symbol,int(x['fundingRateTimestamp']),x['fundingRate'],now,self.id) for x in rows]
                        entry['possibly_truncated']=len(rows)==200
                    else:
                        rows=self.get(source,'https://api.bybit.com/v5/market/open-interest',{'category':'linear','symbol':symbol,'intervalTime':'5min','limit':1})['result']['list']
                        records=[(source,symbol,int(x['timestamp']),x['openInterest'],'base_asset',now,self.id) for x in rows]
                for row in records:
                    if not Decimal(row[3]).is_finite(): raise ValueError('nonfinite metric')
                placeholders=','.join('?' for _ in range(6 if kind=='funding' else 7))
                self.db.executemany('INSERT OR IGNORE INTO '+kind+' VALUES ('+placeholders+')',records);self.db.commit()
                entry.update(status='available' if records else 'empty',rows=len(records),scope='requested_window' if kind=='funding' else 'latest_snapshot_only')
            except Exception as e: entry.update(status='error',error=str(e))
            self.coverage.append(entry)
    def run(self):
        catalogs={}
        endpoints={
          'binance_spot':'https://data-api.binance.vision/api/v3/exchangeInfo',
          'okx_spot':'https://www.okx.com/api/v5/public/instruments?instType=SPOT',
          'binance_perpetual':'https://fapi.binance.com/fapi/v1/exchangeInfo',
          'bybit_perpetual':'https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000'}
        for source,url in endpoints.items():
            try:
                d=self.get(source,url)
                if source.startswith('binance'):
                    catalogs[source]=[(x['baseAsset'],x['quoteAsset'],x['symbol']) for x in d['symbols'] if x['status']=='TRADING' and (source.endswith('spot') or (x.get('contractType')=='PERPETUAL' and x.get('marginAsset')=='USDT'))]
                elif source=='okx_spot': catalogs[source]=[(x['baseCcy'],x['quoteCcy'],x['instId']) for x in d['data'] if x['state']=='live']
                else:
                    if d['result'].get('nextPageCursor'): raise ValueError('catalog pagination required; no partial mapping allowed')
                    catalogs[source]=[(x['baseCoin'],x['quoteCoin'],x['symbol']) for x in d['result']['list'] if x['status']=='Trading' and x.get('contractType')=='LinearPerpetual' and x.get('settleCoin')=='USDT']
            except Exception as e:
                self.coverage.append({'source':source,'dataset':'catalog','status':'error','error':str(e)})
        for source,catalog in catalogs.items():
            for asset in self.cfg['assets']:
                priority=['USDT','USDC','USD'] if source.endswith('spot') else ['USDT']
                choices=[x for q in priority for x in catalog if x[0].upper()==asset.upper() and x[1]==q and x[0].upper()!=q]
                if not choices:
                    self.coverage.append({'source':source,'asset':asset,'dataset':'market_selection','status':'no_eligible_pair'});continue
                base,quote,symbol=choices[0]
                if source.endswith('spot'): self.run_candles(source,asset,symbol,quote)
                else: self.derivatives(source,asset,symbol)
        for name,url in [('defillama_protocols','https://api.llama.fi/protocols'),('defillama_chains','https://api.llama.fi/v2/chains'),('defillama_stablecoins','https://stablecoins.llama.fi/stablecoins?includePrices=true')]:
            try:
                data=self.get(name,url)
                self.coverage.append({'source':name,'dataset':'raw_snapshot','status':'available','normalized':False})
            except Exception as e:self.coverage.append({'source':name,'dataset':'raw_snapshot','status':'error','error':str(e)})
        failures=sum(x.get('status')=='error' for x in self.coverage)
        self.db.execute('UPDATE runs SET status=? WHERE run_id=?',('partial' if failures else 'completed',self.id));self.db.commit()
        counts={t:self.db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('candles','funding','oi')}
        integrity=self.db.execute('PRAGMA integrity_check').fetchone()[0]; self.db.close()
        report={'run_id':self.id,'window_start_ms':self.start,'window_end_ms':self.end,'seconds':round(time.monotonic()-self.started,2),'request_attempts':sum(x['attempts'] for x in self.calls),'compressed_raw_bytes':sum(x.get('compressed_bytes',0) for x in self.calls),'database_bytes':(R/'normalized/market.sqlite3').stat().st_size,'database_rows':counts,'sqlite_integrity':integrity,'errors':failures,'coverage':self.coverage,'requests':self.calls,'limitations':['Ticker-based candidates only; contract identity pending','OI latest snapshot only, no backfill','DefiLlama raw snapshots, not mapped protocol history','No trading, scheduler or automated backup','7-day reload pilot, not production incremental collector']}
        (R/'reports'/(self.id+'-pilot.json')).write_text(json.dumps(report,indent=2),encoding='utf8')
        print(json.dumps({k:v for k,v in report.items() if k not in ('coverage','requests')},indent=2),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--days',type=int,default=7);args=parser.parse_args()
    if not 1<=args.days<=7:parser.error('pilot days must be 1..7')
    lock=R/'state/collector.lock';lock.parent.mkdir(parents=True,exist_ok=True)
    try: fd=lock.open('x')
    except FileExistsError:raise SystemExit('collector.lock exists; inspect previous run before removing')
    try:
        fd.write(utc());fd.close();Collector(args.days).run()
    finally:lock.unlink(missing_ok=True)
