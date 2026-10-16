"""Generate local research indicators. No predictions or order placement."""
import datetime as dt,json,math,sqlite3,statistics
from data_audit import ROOT,load,save
def oi_change(current,previous):
    if not previous:return None
    if any(current[k]!=previous[k] for k in ('provider_field','counting_convention','unit')):return None
    return (float(current['value'])/float(previous['value'])-1)*100 if float(previous['value']) else None
def metrics(rows):
    prices={r[0]:float(r[1]) for r in rows};times=sorted(prices);p=prices[times[-1]]
    out={'daily_bars':len(times),'last_daily_close':p,'last_daily_open_ms':times[-1]}
    for days in (7,30,90,180):
        old=prices.get(times[-1]-days*86400000);out['return_'+str(days)+'d_pct']=(p/old-1)*100 if old else None
    contiguous=len(times)>0 and all(b-a==86400000 for a,b in zip(times,times[1:]));out['contiguous_daily_history']=contiguous
    seq=[prices[t] for t in times]
    for n in (20,50,200):out['sma_'+str(n)]=statistics.mean(seq[-n:]) if contiguous and len(seq)>=n else None
    out['trend']='insufficient_history'
    if out['sma_20'] and out['sma_50']:out['trend']='above_20_50' if p>out['sma_20']>out['sma_50'] else 'below_20_50' if p<out['sma_20']<out['sma_50'] else 'mixed'
    peak=seq[-90:][0];dd=0
    for v in seq[-90:]:peak=max(peak,v);dd=min(dd,(v/peak-1)*100)
    out['max_drawdown_observed_90d_pct']=dd if contiguous and len(seq)>=90 else None
    out['realized_vol_30d_annualized_pct']=statistics.stdev([math.log(b/a) for a,b in zip(seq[-31:],seq[-30:])])*math.sqrt(365)*100 if contiguous and len(seq)>=31 else None
    return out
def main():
    db=sqlite3.connect('file:'+str(ROOT/'normalized/market.sqlite3').replace('\\','/')+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    tables={x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")};assets=[];derivatives=[];defi=[]
    if 'daily_candles' in tables:
        for s in db.execute('SELECT DISTINCT source,symbol,base,quote FROM daily_candles').fetchall():
            rows=db.execute('SELECT open_time,close FROM daily_candles WHERE source=? AND symbol=? ORDER BY open_time',(s['source'],s['symbol'])).fetchall()
            if rows:assets.append(dict(s)|metrics(rows))
    if 'oi_history' in tables:
        for s in db.execute('SELECT DISTINCT source,symbol FROM oi_history').fetchall():
            last=db.execute('SELECT * FROM oi_history WHERE source=? AND symbol=? ORDER BY time DESC LIMIT 1',tuple(s)).fetchone()
            old=db.execute('SELECT value,provider_field,counting_convention,unit FROM oi_history WHERE source=? AND symbol=? AND time=?',(*tuple(s),last['time']-86400000)).fetchone()
            f=db.execute('SELECT rate,time FROM funding WHERE source=? AND symbol=? ORDER BY time DESC LIMIT 1',tuple(s)).fetchone()
            derivatives.append(dict(s)|{'oi_last':last['value'],'oi_time':last['time'],'oi_24h_pct':oi_change(last,old),'semantics':last['provider_field']+':'+last['counting_convention'],'funding_last_pct':float(f[0])*100 if f else None,'funding_time':f[1] if f else None})
    if 'defi_metrics' in tables:
        defi=[dict(r) for r in db.execute("SELECT source,entity_id,name,metric,value,observed_at FROM defi_metrics WHERE metric='tvl_usd' AND source='defillama_chains' AND observed_at=(SELECT MAX(observed_at) FROM defi_metrics WHERE source='defillama_chains') ORDER BY CAST(value AS REAL) DESC LIMIT 12")]
    snapshots=[];contexts=[]
    if 'venue_snapshots' in tables:
        snapshots=[dict(r) for r in db.execute('SELECT v.* FROM venue_snapshots v JOIN (SELECT source,symbol,MAX(observed_at) stamp FROM venue_snapshots GROUP BY source,symbol) x ON v.source=x.source AND v.symbol=x.symbol AND v.observed_at=x.stamp')]
    if 'perpetual_context' in tables:
        contexts=[dict(r) for r in db.execute('SELECT v.* FROM perpetual_context v JOIN (SELECT source,symbol,MAX(observed_at) stamp FROM perpetual_context GROUP BY source,symbol) x ON v.source=x.source AND v.symbol=x.symbol AND v.observed_at=x.stamp')]
    db.close();payload={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'health':load(ROOT/'reports/health.json',{}),'assets':assets,'derivatives':derivatives,'defi_chains':defi,'venue_snapshots':snapshots,'perpetual_context':contexts,'capacity':load(ROOT/'reports/capacity-x3.json',{}),'coverage':load(ROOT/'reports/data-coverage.json',{}),'warnings':['Research indicators, not tested trading signals','No quote-currency or cross-venue OI aggregation','Price execution and intraday risk require faster live data','Fundamentals and token entitlement not yet validated']}
    save(ROOT/'reports/research-indicators.json',payload)
    folder=ROOT/'dashboard';folder.mkdir(exist_ok=True)
    template=(__import__('pathlib').Path(__file__).parent/'dashboard.html').read_text(encoding='utf8')
    data=json.dumps(payload,ensure_ascii=False).replace('<','\\u003c')
    (folder/'index.html').write_text(template.replace('__PAYLOAD__',data),encoding='utf8')
    print(json.dumps({'spot_series':len(assets),'derivative_series':len(derivatives),'dashboard':str(folder/'index.html')}))
if __name__=='__main__':main()
