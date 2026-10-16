"""Read-only source availability probe; no secrets, trading, schedules or artifact upload."""
import datetime as dt,json,math,os,pathlib,time,urllib.request,urllib.error
import urllib.parse
from cloud_rate_guard import RateGuard
PROBES=[
('binance_spot','candles','https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=5m&limit=3','binance_candles',None),
('okx_spot','candles','https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT&bar=5m&limit=3','okx_candles',None),
('binance_perpetual','funding','https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1','binance_funding',None),
('binance_perpetual','oi','https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT','binance_oi',None),
('binance_perpetual','oi_history','https://fapi.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period=1h&limit=2','binance_history',None),
('bybit_perpetual','funding','https://api.bybit.com/v5/market/funding/history?category=linear&symbol=BTCUSDT&limit=1','bybit_funding',None),
('bybit_perpetual','oi_history','https://api.bybit.com/v5/market/open-interest?category=linear&symbol=BTCUSDT&intervalTime=1h&limit=2','bybit_oi',None),
('bybit_perpetual','oi','https://api.bybit.com/v5/market/open-interest?category=linear&symbol=BTCUSDT&intervalTime=5min&limit=1','bybit_oi',None),
('coinbase_spot','ticker','https://api.exchange.coinbase.com/products/BTC-USD/ticker','coinbase',None),
('kraken_spot','ticker','https://api.kraken.com/0/public/Ticker?pair=XBTUSD','kraken',None),
('bitflyer_spot','ticker','https://api.bitflyer.com/v1/getticker?product_code=BTC_JPY','bitflyer',None),
('bitbank_spot','ticker','https://public.bitbank.cc/btc_jpy/ticker','bitbank',None),
('upbit_spot','ticker','https://api.upbit.com/v1/ticker?markets=KRW-BTC','korean',None),
('bithumb_spot','ticker','https://api.bithumb.com/v1/ticker?markets=KRW-BTC','korean',None),
('hyperliquid_perpetual','context','https://api.hyperliquid.xyz/info','hyperliquid',{'type':'metaAndAssetCtxs'}),
('defillama_protocols','snapshot','https://api.llama.fi/protocols','protocols',None),
('defillama_chains','snapshot','https://api.llama.fi/v2/chains','chains',None),
('defillama_stablecoins','snapshot','https://stablecoins.llama.fi/stablecoins?includePrices=true','stablecoins',None)]
def numeric(v,positive=False):
    n=float(v)
    if not math.isfinite(n) or positive and n<=0:raise ValueError('invalid numeric sample')
    return n
def validate(kind,d):
    if isinstance(d,dict):
        if 'retCode' in d and d['retCode']!=0:raise ValueError('provider retCode '+str(d['retCode']))
        if 'code' in d and str(d['code'])!='0':raise ValueError('provider code '+str(d['code']))
    if kind=='binance_candles':numeric(d[0][4],True);return len(d)
    if kind=='okx_candles':numeric(d['data'][0][4],True);return len(d['data'])
    if kind=='binance_funding':numeric(d[0]['fundingRate']);return len(d)
    if kind=='binance_oi':numeric(d['openInterest'],True);return 1
    if kind=='binance_history':numeric(d[0]['sumOpenInterest'],True);return len(d)
    if kind.startswith('bybit_'):
        rows=d['result']['list'];numeric(rows[0]['fundingRate'] if kind=='bybit_funding' else rows[0]['openInterest']);return len(rows)
    if kind=='coinbase':numeric(d['price'],True);return 1
    if kind=='kraken':
        if d.get('error'):raise ValueError('Kraken returned error')
        numeric(next(iter(d['result'].values()))['c'][0],True);return len(d['result'])
    if kind=='bitflyer':numeric(d['ltp'],True);return 1
    if kind=='bitbank':
        if d.get('success')!=1:raise ValueError('bitbank failure')
        numeric(d['data']['last'],True);return 1
    if kind=='korean':numeric(d[0]['trade_price'],True);return len(d)
    if kind=='hyperliquid':
        index=next(i for i,x in enumerate(d[0]['universe']) if x['name']=='BTC');numeric(d[1][index]['markPx'],True);numeric(d[1][index]['openInterest'],True);numeric(d[1][index]['funding']);return len(d[1])
    if kind in ('protocols','chains'):
        if not isinstance(d,list) or not d or not d[0].get('name'):raise ValueError('empty shared dataset')
        return len(d)
    if kind=='stablecoins':
        rows=d['peggedAssets']
        if not rows or not rows[0].get('name'):raise ValueError('empty stablecoins')
        return len(rows)
    raise ValueError('unknown validation kind')
def configured_sources(environ=None):
    environ = os.environ if environ is None else environ
    available = {p[0] for p in PROBES}
    raw = environ.get('PROBE_SOURCES_JSON')
    if raw is None:
        return available
    selected = json.loads(raw)
    if not isinstance(selected,list) or not selected or not all(isinstance(x,str) for x in selected):
        raise ValueError('PROBE_SOURCES_JSON must be a nonempty source list')
    if len(set(selected)) != len(selected) or set(selected) - available:
        raise ValueError('duplicate or unknown probe source')
    return set(selected)

def main():
    sources = configured_sources()
    results=[];started=time.monotonic()
    guard=RateGuard(os.getenv('PROBE_STATE_PATH','rate-cooldowns.json'), json.loads(os.getenv('PROBE_COOLDOWNS_JSON','{}')))
    for source,dataset,url,kind,body in PROBES:
        if source not in sources:continue
        t=time.monotonic();entry={'source':source,'dataset':dataset,'url':url,'checked_at':dt.datetime.now(dt.timezone.utc).isoformat()}
        host=urllib.parse.urlparse(url).netloc
        if guard.remaining(host):
            entry.update(status='COOLDOWN',retry_at=dt.datetime.fromtimestamp(guard.state[host]['until'],dt.timezone.utc).isoformat())
            results.append(entry);continue
        try:
            req=urllib.request.Request(url,data=json.dumps(body).encode() if body else None,headers={'User-Agent':'CryptoDataAvailability/1.0','Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=20) as f:
                entry['http_status']=f.status;raw=f.read(64_000_001)
                entry['provider_used_weight']={k:v for k,v in f.headers.items() if k.lower().startswith('x-mbx-used-weight') and str(v).isdigit()}
            if len(raw)>64_000_000:raise ValueError('size cap')
            data=json.loads(raw);entry.update(status='PASS',sample_rows=validate(kind,data),response_bytes=len(raw))
        except urllib.error.HTTPError as e:
            entry.update(status='FAIL',http_status=e.code,error='HTTP '+str(e.code))
            if e.code in (418,429):
                block=guard.block(host,e.code,e.headers,e.read(65536).decode('utf8','replace'))
                entry.update(cooldown=block,retry_at=dt.datetime.fromtimestamp(block['until'],dt.timezone.utc).isoformat())
        except Exception as e:entry.update(status='FAIL',error_type=type(e).__name__,error=str(e)[:160])
        entry['seconds']=round(time.monotonic()-t,3);results.append(entry);print(json.dumps(entry),flush=True);time.sleep(max(2.0,float(os.getenv('BINANCE_FUTURES_PAUSE_SECONDS','2'))) if host=='fapi.binance.com' else .4)
    report={'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'runner_os':os.getenv('RUNNER_OS','local'),'runner_arch':os.getenv('RUNNER_ARCH','local'),'workflow_run_id':os.getenv('GITHUB_RUN_ID'),'selected_sources':sorted(sources),'scope':'Selected-source BTC sample and shared DeFi; no full 30-asset test or storage/scheduling acceptance','status':'PASS' if results and all(x['status']=='PASS' for x in results) else 'FAIL','seconds':round(time.monotonic()-started,3),'results':results,'cooldowns':guard.state}
    pathlib.Path('cloud-availability.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print('CRYPTODATA_PROBE_REPORT='+json.dumps(report),flush=True)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            f.write('## Availability only\n|Source|Dataset|Status|HTTP|\n|---|---|---|---|\n')
            for x in results:f.write('|'+x['source']+'|'+x['dataset']+'|'+x['status']+'|'+str(x.get('http_status',''))+'|\n')
    return 0 if report['status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())
