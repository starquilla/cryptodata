"""Read-only public API preflight. No credentials, trading, or scheduler."""
import concurrent.futures, csv, datetime, gzip, hashlib, json, pathlib, time, urllib.request
ROOT = pathlib.Path(r"E:\CryptoData tai nguyen")
SOURCES = [
 ('coinbase_spot','https://api.exchange.coinbase.com/products',None),
 ('kraken_spot','https://api.kraken.com/0/public/AssetPairs',None),
 ('binance_spot','https://data-api.binance.vision/api/v3/exchangeInfo',None),
 ('binance_perpetual','https://fapi.binance.com/fapi/v1/exchangeInfo',None),
 ('okx_spot','https://www.okx.com/api/v5/public/instruments?instType=SPOT',None),
 ('okx_perpetual','https://www.okx.com/api/v5/public/instruments?instType=SWAP',None),
 ('bybit_spot','https://api.bybit.com/v5/market/instruments-info?category=spot',None),
 ('bybit_perpetual','https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000',None),
 ('hyperliquid_perpetual','https://api.hyperliquid.xyz/info',{'type':'meta'}),
 ('upbit_spot','https://api.upbit.com/v1/market/all?isDetails=false',None),
 ('bithumb_spot','https://api.bithumb.com/v1/market/all?isDetails=false',None),
 ('bitflyer_spot','https://api.bitflyer.com/v1/getmarkets',None),
 ('bitbank_spot','https://public.bitbank.cc/tickers',None),
 ('defillama_protocols','https://api.llama.fi/protocols',None),
]
def products(name, d):
    rows=[]
    def add(base, quote, symbol, status='unknown'):
        base={'XBT':'BTC'}.get(base,base)
        rows.append((str(base).upper(),quote,symbol,status))
    if name=='coinbase_spot':
        for r in d: add(r['base_currency'],r['quote_currency'],r['id'],r.get('status','unknown'))
    elif name=='kraken_spot':
        if d.get('error'): raise ValueError(str(d['error']))
        for k,r in d['result'].items():
            pair=r.get('wsname','').split('/')
            if len(pair)==2: add(*pair,k,r.get('status','unknown'))
    elif name.startswith('binance'):
        for r in d['symbols']:
            if name.endswith('perpetual') and r.get('contractType')!='PERPETUAL': continue
            add(r['baseAsset'],r['quoteAsset'],r['symbol'],r.get('status','unknown'))
    elif name.startswith('okx'):
        if d.get('code')!='0': raise ValueError(str(d.get('msg')))
        for r in d['data']:
            base,quote=(r['baseCcy'],r['quoteCcy']) if name.endswith('spot') else r['uly'].split('-')[:2]
            add(base,quote,r['instId'],r.get('state','unknown'))
    elif name.startswith('bybit'):
        if d.get('retCode')!=0: raise ValueError(str(d.get('retMsg')))
        for r in d['result']['list']:
            if name.endswith('perpetual') and 'Perpetual' not in r.get('contractType',''): continue
            add(r['baseCoin'],r['quoteCoin'],r['symbol'],r.get('status','unknown'))
    elif name=='hyperliquid_perpetual':
        for r in d['universe']: add(r['name'],'USDC',r['name'],'delisted' if r.get('isDelisted') else 'listed')
    elif name in ('upbit_spot','bithumb_spot'):
        for r in d:
            quote,base=r['market'].split('-',1); add(base,quote,r['market'])
    elif name=='bitflyer_spot':
        for r in d:
            symbol=r['product_code']; parts=symbol.split('_')
            if len(parts)==2: add(parts[0],parts[1],symbol)
    elif name=='bitbank_spot':
        if d.get('success')!=1: raise ValueError('API failure')
        for r in d['data']:
            base,quote=r['pair'].split('_'); add(base,quote,r['pair'])
    return rows

def main():
    settings=json.loads((ROOT/'config/settings.json').read_text(encoding='utf-8-sig'))
    assert len(settings['assets'])<=settings['max_assets'] and not settings['trading_enabled']
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    dest=ROOT/'raw'/stamp; dest.mkdir(parents=True)
    started=time.monotonic()
    def fetch(item):
        name,url,body=item; start=time.monotonic()
        result={'source':name,'url':url,'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
        try:
            req=urllib.request.Request(url,data=json.dumps(body).encode() if body else None,headers={'User-Agent':'CryptoDataResearch/0.1','Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=25) as response:
                raw=response.read(64000001)
                if len(raw)>64000000: raise ValueError('response size safety limit')
                result['http_status']=response.status
            data=json.loads(raw)
            path=dest/(name+'.json.gz'); path.write_bytes(gzip.compress(raw))
            rows=products(name,data)
            result.update(status='ok',raw_bytes=len(raw),compressed_bytes=path.stat().st_size,sha256=hashlib.sha256(raw).hexdigest(),products=rows)
            if name=='defillama_protocols': result['protocol_count']=len(data)
            if name.startswith('bybit'): result['pagination_pending']=bool(data['result'].get('nextPageCursor'))
        except Exception as e: result.update(status='error',error=str(e))
        result['seconds']=round(time.monotonic()-start,3)
        return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool: results=list(pool.map(fetch,SOURCES))
    coverage=[]
    for r in results:
        if r['source']=='defillama_protocols': continue
        rows=r.pop('products',[])
        for asset in settings['assets']:
            matches=[{'symbol':x[2],'quote':x[1],'status':x[3]} for x in rows if x[0]==asset.upper()]
            state='candidate_symbol_match' if matches else ('unverified_source_error' if r['status']!='ok' else ('unverified_pagination' if r.get('pagination_pending') else 'not_in_returned_catalog'))
            coverage.append({'asset':asset,'source':r['source'],'coverage':state,'matches':json.dumps(matches,ensure_ascii=False)})
    report={'run_id':stamp,'seconds':round(time.monotonic()-started,3),'scope':'Catalog only; matches need identity and market-data validation. No source replacement.','sources':results}
    report_path=ROOT/'reports'/(stamp+'-preflight.json'); report_path.write_text(json.dumps(report,indent=2),encoding='utf8')
    with (ROOT/'reports'/(stamp+'-coverage.csv')).open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=['asset','source','coverage','matches']); writer.writeheader(); writer.writerows(coverage)
    print(json.dumps({'run_id':stamp,'seconds':report['seconds'],'results':[{'source':r['source'],'status':r['status'],'error':r.get('error'),'pagination_pending':r.get('pagination_pending')} for r in results],'compressed_bytes':sum(r.get('compressed_bytes',0) for r in results)},indent=2))
if __name__=='__main__': main()
