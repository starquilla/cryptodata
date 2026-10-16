"""Offline x3 SQLite workload; no external calls and no production writes."""
import datetime as dt,hashlib,json,pathlib,sqlite3,time,zipfile
from data_audit import ROOT,load,save
def main():
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ');folder=ROOT/'sandbox'/('x3-'+stamp);folder.mkdir(parents=True)
    start=time.perf_counter();src=sqlite3.connect('file:'+str(ROOT/'normalized/market.sqlite3').replace('\\','/')+'?mode=ro',uri=True);db=sqlite3.connect(folder/'x3.sqlite3');src.backup(db);src.close()
    counts={}
    for table in ('candles','daily_candles','funding','oi','oi_history','defi_metrics','venue_snapshots','perpetual_context'):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():continue
        cols=[x[1] for x in db.execute('PRAGMA table_info('+table+')')];source=cols.index('source');rows=list(db.execute('SELECT * FROM '+table));counts[table]=len(rows)
        for replica in (1,2):
            copies=[]
            for row in rows:
                row=list(row);row[source]='SYNTHETIC_X3_'+str(replica)+'_'+row[source];copies.append(row)
            db.executemany('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in cols)+')',copies)
        db.commit()
    ingest=time.perf_counter()-start;qstart=time.perf_counter()
    db.execute('SELECT source,symbol,COUNT(*),MAX(open_time) FROM candles GROUP BY source,symbol').fetchall()
    query=time.perf_counter()-qstart;integrity=db.execute('PRAGMA integrity_check').fetchone()[0];db.close()
    zstart=time.perf_counter();zpath=folder/'x3-backup.zip'
    with zipfile.ZipFile(zpath,'w',zipfile.ZIP_DEFLATED) as z:z.write(folder/'x3.sqlite3','x3.sqlite3')
    compress=time.perf_counter()-zstart
    with zipfile.ZipFile(zpath) as z:
        assert z.testzip() is None;restored=folder/'restore.sqlite3';restored.write_bytes(z.read('x3.sqlite3'))
    rd=sqlite3.connect(restored);assert rd.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    for t,n in counts.items():assert rd.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]==n*3
    rd.close()
    mapping=load(ROOT/'config/instrument-map.json',{});spot=sum(k.startswith(('binance_spot|','okx_spot|')) for k in mapping);perp=len(mapping)-spot
    base=sqlite3.connect('file:'+str(ROOT/'normalized/market.sqlite3').replace('\\','/')+'?mode=ro',uri=True)
    defi_per_poll=base.execute('SELECT COUNT(*) FROM (SELECT DISTINCT source,entity_id,metric FROM defi_metrics WHERE source != ?)',('defillama_protocols',)).fetchone()[0] if counts.get('defi_metrics') else 0
    scope=load(ROOT/'config/defi-protocol-scope.json',{}).get('protocol_ids',[])
    defi_per_poll+=len(scope) if scope else base.execute('SELECT COUNT(*) FROM (SELECT DISTINCT entity_id,metric FROM defi_metrics WHERE source=?)',('defillama_protocols',)).fetchone()[0]
    extra_per_poll=len(load(ROOT/'config/extra-instrument-map.json',{}));base.close()
    rowbytes=(folder/'x3.sqlite3').stat().st_size/max(1,sum(counts.values())*3)
    daily_rows=spot*288+perp*(24+12)+counts.get('funding',0)/7+spot+12*(defi_per_poll+extra_per_poll)
    reports=[load(p,{}) for p in (ROOT/'reports').glob('*-incremental.json')]
    steady=[r for r in reports if r.get('compressed_raw_bytes') and r.get('mode')=='incremental']
    raw_per_run=max([r['compressed_raw_bytes'] for r in steady]+[0])
    addon=load(ROOT/'reports/derivatives-history.json',{});research=load(ROOT/'reports/research-collection.json',{})
    extra=load(ROOT/'reports/extra-venues.json',{})
    extras=sum(r.get('compressed_bytes',0) for r in addon.get('requests',[]))+sum(r.get('compressed_bytes',0) for r in research.get('requests',[]))+sum(r.get('compressed_bytes',0) for r in extra.get('requests',[]))
    raw_per_run+=extras
    scenarios=[]
    for factor in (1,3,5):
        db_est=daily_rows*180*factor*rowbytes+(ROOT/'normalized/market.sqlite3').stat().st_size
        raw_est=raw_per_run*12*180*factor
        ratio=zpath.stat().st_size/(folder/'x3.sqlite3').stat().st_size
        scenarios.append({'factor':factor,'database_180d_bytes_estimate':round(db_est),'raw_180d_bytes_no_retention':round(raw_est),'raw_30d_bytes_with_retention':round(raw_per_run*12*30*factor),'backup_cloud_3versions_bytes_estimate':round(db_est*ratio*3),'local_total_180d_with_20pct_margin_bytes_estimate':round((db_est+raw_est+db_est*ratio*8)*1.2),'backup_peak_working_bytes_estimate':round(db_est*2+db_est*ratio*2),'local_total_with_30d_raw_20pct_margin_bytes_estimate':round((db_est+raw_per_run*12*30*factor+db_est*ratio*8)*1.2),'legacy_250MB_upload_limit_would_be_exceeded':db_est*ratio>250e6})
    result={'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),'test_type':'offline synthetic x3 current database; not live API x3 or six-month soak','folder':str(folder),'baseline_rows':counts,'defi_rows_per_poll_estimate':defi_per_poll,'extra_rows_per_poll_estimate':extra_per_poll,'normalization_scope':load(ROOT/'config/defi-protocol-scope.json',{}).get('selection','all protocols'),'local_budget_bytes':load(ROOT/'config/settings.json',{}).get('local_storage_budget_bytes'),'x3_db_bytes':(folder/'x3.sqlite3').stat().st_size,'x3_zip_bytes':zpath.stat().st_size,'ingest_seconds':round(ingest,3),'aggregate_query_seconds':round(query,3),'zip_seconds':round(compress,3),'integrity':integrity,'restore_passed':True,'row_bytes_measured':round(rowbytes,2),'raw_bytes_per_run_upper_sample':raw_per_run,'scenarios':scenarios,'assumptions':['180 days, 12 collections/day; linear scaling; funding rows/day estimated from current window','DeFi scoped source/entity/metric values stored each poll; all raw retained','raw estimate uses maximum primary run and latest addons; may overestimate initial research reload','local planning includes 2 compressed SQLite backups, 3 local ZIPs and 3 compressed release DB copies; older releases/sandbox need separate housekeeping','no unattended raw deletion enabled; 30-day retention scenario is a proposal only']}
    save(ROOT/'reports/capacity-x3.json',result);print(json.dumps(result,indent=2))
if __name__=='__main__':main()
