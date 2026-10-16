"""Full frozen instrument coverage, three sequential passes; no trading or cloud credentials."""
import datetime as dt,json,math,os,pathlib,time,urllib.request,urllib.error,urllib.parse
from probe_cloud_sources import PROBES,validate,configured_sources
from cloud_rate_guard import RateGuard
ROOT=pathlib.Path(__file__).resolve().parent

def jobs():
 config=pathlib.Path(os.getenv('PROBE_CONFIG_ROOT',str(ROOT)))
 mapping=json.loads((config/'instrument-map.json').read_text('utf-8-sig'))
 extra=json.loads((config/'extra-instrument-map.json').read_text('utf-8-sig'))
 tasks=[]
 for key,symbol in mapping.items():
  source,asset=key.split('|');templates=[x for x in PROBES if x[0]==source]
  for _,dataset,url,kind,body in templates:
   url=url.replace('BTCUSDT',urllib.parse.quote(symbol)).replace('BTC-USDT',urllib.parse.quote(symbol))
   tasks.append((source,asset,symbol,dataset,url,kind,body))
  if source.endswith('_spot'):
   template=templates[0];url=template[2].replace('BTCUSDT',urllib.parse.quote(symbol)).replace('BTC-USDT',urllib.parse.quote(symbol)).replace('interval=5m','interval=1d').replace('bar=5m','bar=1D')
   tasks.append((source,asset,symbol,'daily_candles',url,template[3],None))
 for key,item in extra.items():
  source,asset=key.split('|');symbol=item['symbol']
  if source=='hyperliquid_perpetual':continue
  template=next(x for x in PROBES if x[0]==source)
  url=template[2]
  replace={'coinbase_spot':'BTC-USD','kraken_spot':'XBTUSD','bitflyer_spot':'BTC_JPY','bitbank_spot':'btc_jpy','upbit_spot':'KRW-BTC','bithumb_spot':'KRW-BTC'}[source]
  tasks.append((source,asset,symbol,'ticker',url.replace(replace,urllib.parse.quote(symbol)),template[3],None))
 for source,dataset,url,kind,body in PROBES[14:]:tasks.append((source,'SHARED','SHARED',dataset,url,kind,body))
 sources=configured_sources()
 return [t for t in tasks if t[0] in sources],extra

def run_settings(environ):
 rounds=int(environ.get('PROBE_ROUNDS','3'))
 pause=float(environ.get('BINANCE_FUTURES_PAUSE_SECONDS','2'))
 if rounds not in (1,3):raise ValueError('PROBE_ROUNDS must be 1 or 3')
 if not math.isfinite(pause) or pause<0:raise ValueError('invalid Binance pacing')
 return rounds,max(2.0,pause)

def main():
 rounds,binance_pause=run_settings(os.environ)
 tasks,extra=jobs()
 if not tasks:raise ValueError('empty coverage job set')
 start=time.monotonic();results=[]
 guard=RateGuard(os.getenv('PROBE_STATE_PATH','rate-cooldowns.json'),json.loads(os.getenv('PROBE_COOLDOWNS_JSON','{}')))
 for repeat in range(rounds):
  for source,asset,symbol,dataset,url,kind,body in tasks:
   row=dict(round=repeat+1,source=source,asset=asset,symbol=symbol,dataset=dataset,checked_at=dt.datetime.now(dt.timezone.utc).isoformat())
   host=urllib.parse.urlparse(url).netloc;tick=time.monotonic()
   if guard.remaining(host):row.update(status='COOLDOWN',retry_at=dt.datetime.fromtimestamp(guard.state[host]['until'],dt.timezone.utc).isoformat())
   elif tick-start>1500:row.update(status='UNTESTED',error='execution safety budget')
   else:
    try:
     req=urllib.request.Request(url,data=json.dumps(body).encode() if body else None,headers={'User-Agent':'CryptoDataCoverage/1.0','Content-Type':'application/json'})
     with urllib.request.urlopen(req,timeout=20) as response:
      raw=response.read(64_000_001);row['http_status']=response.status
      row['provider_used_weight']={k:v for k,v in response.headers.items() if k.lower().startswith('x-mbx-used-weight') and str(v).isdigit()}
     if len(raw)>64_000_000:raise ValueError('response cap')
     data=json.loads(raw);row.update(status='PASS',sample_rows=validate(kind,data),response_bytes=len(raw))
     if source=='hyperliquid_perpetual':
      universe={coin['name']:ctx for coin,ctx in zip(data[0]['universe'],data[1])}
      row['assets']={key.split('|')[1]:('PASS' if item['symbol'] in universe and float(universe[item['symbol']]['markPx'])>0 and float(universe[item['symbol']]['openInterest'])>=0 else 'FAIL') for key,item in extra.items() if key.startswith(source+'|')}
      if any(x!='PASS' for x in row['assets'].values()):row['status']='FAIL'
    except urllib.error.HTTPError as error:
     row.update(status='FAIL',http_status=error.code,error='HTTP '+str(error.code))
     if error.code in (418,429):row['cooldown']=guard.block(host,error.code,error.headers,error.read(65536).decode('utf8','replace'))
    except Exception as error:row.update(status='FAIL',error_type=type(error).__name__)
    time.sleep(binance_pause if host=='fapi.binance.com' else .4)
   row['seconds']=round(time.monotonic()-tick,3);results.append(row)
   if len(results)%20==0:print('CRYPTODATA_PROGRESS='+json.dumps({'checked':len(results),'total':len(tasks)*rounds,'failed':sum(x['status']!='PASS' for x in results),'seconds':round(time.monotonic()-start,2)}),flush=True)
 report=dict(checked_at=dt.datetime.now(dt.timezone.utc).isoformat(),status='PASS' if results and all(x['status']=='PASS' for x in results) else 'FAIL',rounds=rounds,selected_sources=sorted(configured_sources()),seconds=round(time.monotonic()-start,2),task_count_per_round=len(tasks),results=results,cooldowns=guard.state,scope='Selected-source configured-asset API coverage; NOT production collector throughput or six-month stability',missing_instruments='Frozen mappings absent entries are not configured; never replaced by another venue')
 pathlib.Path('cloud-coverage.json').write_text(json.dumps(report,indent=2),encoding='utf8')
 print('CRYPTODATA_PROBE_REPORT='+json.dumps(report),flush=True)
 return 0 if report['status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())

