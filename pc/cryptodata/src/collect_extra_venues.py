"""Native-quote snapshots, separate from candles/history. No FX or symbol-only DeFi joins."""
import json,math,sys,time
from collect_pilot import Collector,R,utc
from preflight import products
from data_audit import load,save
def main():
    import msvcrt
    lock=(R/'state/incremental.lock').open('a+b');lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:lock.close();return 3
    try:
        c=Collector(1);c.db.executescript('''CREATE TABLE IF NOT EXISTS venue_snapshots(source TEXT,symbol TEXT,base TEXT,quote TEXT,observed_at TEXT,last TEXT,bid TEXT,ask TEXT,volume_24h TEXT,volume_semantics TEXT,PRIMARY KEY(source,symbol,observed_at));
        CREATE TABLE IF NOT EXISTS perpetual_context(source TEXT,symbol TEXT,base TEXT,quote TEXT,observed_at TEXT,mark_price TEXT,open_interest TEXT,funding_current TEXT,semantics TEXT,PRIMARY KEY(source,symbol,observed_at));''')
        mapping=load(R/'config/extra-instrument-map.json',{});coverage=[];catalogs={}
        endpoints={'coinbase_spot':'https://api.exchange.coinbase.com/products','kraken_spot':'https://api.kraken.com/0/public/AssetPairs','upbit_spot':'https://api.upbit.com/v1/market/all?isDetails=false','bithumb_spot':'https://api.bithumb.com/v1/market/all?isDetails=false','bitflyer_spot':'https://api.bitflyer.com/v1/getmarkets','bitbank_spot':'https://public.bitbank.cc/tickers'}
        for source,url in endpoints.items():
            try:
                data=c.get(source,url);catalogs[source]=(products(source,data),data)
            except Exception as e:coverage.append({'source':source,'dataset':'catalog','status':'error','error':str(e)})
        def put(source,asset,symbol,quote,last,bid=None,ask=None,volume=None,semantics='base_24h'):
            for v in (last,bid,ask,volume):
                if v is not None and (not math.isfinite(float(v)) or float(v)<0):raise ValueError('invalid snapshot value')
            if last is None or float(last)<=0:raise ValueError('missing positive last price')
            stamp=utc();c.db.execute('INSERT INTO venue_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)',(source,symbol,asset,quote,stamp,str(last),str(bid) if bid is not None else None,str(ask) if ask is not None else None,str(volume) if volume is not None else None,semantics));c.db.commit()
            coverage.append({'source':source,'asset':asset,'symbol':symbol,'quote':quote,'dataset':'ticker_snapshot','status':'available','observed_at':stamp,'spread_crossed':bid is not None and ask is not None and float(bid)>=float(ask)})
        for source,(cat,data) in catalogs.items():
            priority=['USD','USDT','USDC'] if source in ('coinbase_spot','kraken_spot') else ['KRW'] if source in ('upbit_spot','bithumb_spot') else ['JPY']
            selected=[]
            initial=not any(k.startswith(source+'|') for k in mapping)
            for asset in c.cfg['assets']:
                choices=[x for q in priority for x in cat if x[0]==asset.upper() and str(x[1]).upper()==q and asset.upper()!=q and x[3] in ('online','unknown')]
                if not choices:coverage.append({'source':source,'asset':asset,'dataset':'ticker_snapshot','status':'not_in_catalog'});continue
                base,quote,symbol,status=choices[0];quote=quote.upper();key=source+'|'+asset
                if initial:mapping[key]={'symbol':symbol,'quote':quote,'identity':'catalog_product_only; cross_venue_pending'}
                if mapping.get(key,{}).get('symbol')!=symbol:coverage.append({'source':source,'asset':asset,'dataset':'identity_guard','status':'error','error':'new or changed product needs mapping review'});continue
                selected.append((asset,symbol,quote))
            # Save frozen products before requests; a transient failure must not remap.
            save(R/'config/extra-instrument-map.json',mapping)
            batch=None
            try:
                if source in ('upbit_spot','bithumb_spot') and selected:batch={x['market']:x for x in c.get(source,'https://api.'+('upbit' if source=='upbit_spot' else 'bithumb')+'.com/v1/ticker',{'markets':','.join(x[1] for x in selected)})}
                elif source=='kraken_spot' and selected:
                    result=c.get(source,'https://api.kraken.com/0/public/Ticker',{'pair':','.join(x[1] for x in selected)})
                    if result.get('error'):raise ValueError(str(result['error']))
                    batch=result['result']
                elif source=='bitbank_spot':batch={x['pair']:x for x in data['data']}
            except Exception as e:
                for asset,symbol,quote in selected:coverage.append({'source':source,'asset':asset,'symbol':symbol,'dataset':'ticker_snapshot','status':'error','error':str(e)})
                continue
            for asset,symbol,quote in selected:
                try:
                    if source=='coinbase_spot':
                        x=c.get(source,'https://api.exchange.coinbase.com/products/'+symbol+'/ticker');put(source,asset,symbol,quote,x['price'],x.get('bid'),x.get('ask'),x.get('volume'))
                    elif source=='kraken_spot':x=batch[symbol];put(source,asset,symbol,quote,x['c'][0],x['b'][0],x['a'][0],x['v'][1])
                    elif source in ('upbit_spot','bithumb_spot'):x=batch[symbol];put(source,asset,symbol,quote,x['trade_price'],volume=x.get('acc_trade_volume_24h'))
                    elif source=='bitbank_spot':x=batch[symbol];put(source,asset,symbol,quote,x['last'],x.get('buy'),x.get('sell'),x.get('vol'))
                    else:
                        x=c.get(source,'https://api.bitflyer.com/v1/getticker',{'product_code':symbol});put(source,asset,symbol,quote,x['ltp'],x.get('best_bid'),x.get('best_ask'),x.get('volume_by_product'),'provider_volume_by_product; duration pending verification')
                except Exception as e:coverage.append({'source':source,'asset':asset,'symbol':symbol,'dataset':'ticker_snapshot','status':'error','error':str(e)})
        source='hyperliquid_perpetual'
        try:
            meta,contexts=c.get(source,'https://api.hyperliquid.xyz/info',body={'type':'metaAndAssetCtxs'})
            if len(meta['universe'])!=len(contexts):raise ValueError('metadata context count mismatch')
            initial=not any(k.startswith(source+'|') for k in mapping)
            for coin,ctx in zip(meta['universe'],contexts):
                symbol=coin['name'];asset=next((a for a in c.cfg['assets'] if a.upper()==symbol.upper()),None)
                if not asset or coin.get('isDelisted'):continue
                key=source+'|'+asset
                if initial:mapping[key]={'symbol':symbol,'quote':'USDC','identity':'provider_perpetual_name; cross_venue_pending'}
                if mapping.get(key,{}).get('symbol')!=symbol:continue
                vals=[ctx.get('markPx'),ctx.get('openInterest'),ctx.get('funding')]
                if any(v is None or not math.isfinite(float(v)) for v in vals) or float(vals[0])<=0 or float(vals[1])<0:raise ValueError('invalid perpetual context')
                stamp=utc();c.db.execute('INSERT INTO perpetual_context VALUES (?,?,?,?,?,?,?,?,?)',(source,symbol,asset,'USDC',stamp,*vals,'openInterest provider_native; funding_current not settlement history'))
                coverage.append({'source':source,'asset':asset,'symbol':symbol,'dataset':'perpetual_context','status':'available','observed_at':stamp})
            c.db.commit();save(R/'config/extra-instrument-map.json',mapping)
        except Exception as e:c.db.rollback();coverage.append({'source':source,'dataset':'perpetual_context','status':'error','error':str(e)})
        bad=[x for x in coverage if x['status']=='error'];c.db.execute('UPDATE runs SET status=? WHERE run_id=?',('partial' if bad else 'completed',c.id));c.db.commit();c.db.close()
        report={'run_id':c.id,'finished_at':utc(),'coverage':coverage,'issues':bad,'requests':c.calls,'seconds':round(time.monotonic()-c.started,2),'limitations':['snapshots only; not full historical feeds','native quotes retained; no KRW/JPY premium without FX','catalog product identity only; cross-venue verification pending']};save(R/'reports/extra-venues.json',report)
        print(json.dumps({'issues':bad,'available_series':sum(x['status']=='available' for x in coverage)}));return 2 if bad else 0
    finally:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1);lock.close()
if __name__=='__main__':sys.exit(main())
