"""Summarize cloud coverage evidence; never convert cooldowns into available observations."""
import csv,datetime as dt,json,pathlib
R=pathlib.Path(r'E:\CryptoData tai nguyen')
def main():
 report=json.loads((R/'reports/cloud-30-assets-x3-probe.json').read_text('utf-8-sig'));groups={}
 for row in report['results']:
  if row['source']=='hyperliquid_perpetual':
   extra=json.loads((R/'config/extra-instrument-map.json').read_text('utf-8-sig'))
   for key,item in extra.items():
    if key.startswith('hyperliquid_perpetual|'):
     copy=dict(row,asset=key.split('|')[1],symbol=item['symbol'],status=row.get('assets',{}).get(key.split('|')[1],row['status']))
     key=(copy['source'],copy['asset'],copy['symbol'],copy['dataset']);groups.setdefault(key,[]).append(copy)
   continue
  key=(row['source'],row['asset'],row['symbol'],row['dataset']);groups.setdefault(key,[]).append(row)
 assets=json.loads((R/'config/settings.json').read_text('utf-8-sig'))['assets']
 datasets={'binance_spot':['candles','daily_candles'],'okx_spot':['candles','daily_candles'],'binance_perpetual':['funding','oi','oi_history'],'bybit_perpetual':['funding','oi','oi_history'],'hyperliquid_perpetual':['context']}
 for source in ['coinbase_spot','kraken_spot','bitflyer_spot','bitbank_spot','upbit_spot','bithumb_spot']:datasets[source]=['ticker']
 present={(s,a,d) for s,a,_,d in groups}
 for source,types in datasets.items():
  for asset in assets:
   for dataset in types:
    if (source,asset,dataset) not in present:
     groups[(source,asset,'',dataset)]=[dict(status='NOT_CONFIGURED',checked_at='') for _ in range(3)]
 units={'candles':'price in native quote; base volume','daily_candles':'price in native quote; base volume','funding':'raw settled funding rate','oi':'provider native base asset, or 5min sample for Bybit','oi_history':'provider native base asset','ticker':'price in native quote','context':'provider native OI; current funding, not settled','snapshot':'shared protocol/chain/stablecoin data; not token entitlement'}
 rows=[]
 for (source,asset,symbol,dataset),samples in groups.items():
  rows.append(dict(source=source,asset=asset,symbol=symbol,dataset=dataset,round_1=samples[0]['status'],round_2=samples[1]['status'],round_3=samples[2]['status'],pass_count=sum(x['status']=='PASS' for x in samples),last_observed_at=samples[-1]['checked_at'],unit=units[dataset],provider_timestamp_freshness='not checked by this probe',backfill='possible only where historical endpoint exists; instant snapshots not recoverable',error_codes=','.join(sorted({str(x['http_status']) for x in samples if x.get('http_status') and x['status']!='PASS'}))))
 with (R/'reports/cloud-30-assets-coverage.csv').open('w',newline='',encoding='utf-8-sig') as f:
  writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
 summary=dict(status=report['status'],rounds=report['rounds'],seconds=report['seconds'],checks=len(report['results']),passed=sum(x['status']=='PASS' for x in report['results']),cooldown=sum(x['status']=='COOLDOWN' for x in report['results']),failed=sum(x['status']=='FAIL' for x in report['results']),successful_response_bytes=sum(x.get('response_bytes',0) for x in report['results']),issues=[x for x in report['results'] if x['status']=='FAIL'],cooldowns=report['cooldowns'],limitation=report['scope'])
 (R/'reports/cloud-30-assets-x3-summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8');print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
