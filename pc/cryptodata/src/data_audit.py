"""Offline coverage and health audit; keep source identity and metric semantics."""
import argparse,csv,datetime as dt,json,pathlib,shutil,sqlite3,sys,uuid
ROOT=pathlib.Path(r'E:\CryptoData tai nguyen')
def load(path,default=None):
    try:return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return default
def save(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.'+uuid.uuid4().hex+'.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf8');tmp.replace(path)
def epoch(value):
    try:return dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()*1000
    except (ValueError,AttributeError,TypeError):return None
def audit(root=ROOT,now=None):
    now=now or dt.datetime.now(dt.timezone.utc);nowms=int(now.timestamp()*1000)
    cfg=load(root/'config/settings.json',{});mapping=load(root/'config/instrument-map.json',{})
    latest=load(root/'state/latest.json',{});addon=load(root/'reports/derivatives-history.json',{})
    main=load(root/'reports'/pathlib.Path(latest.get('report','missing.json')).name,{})
    matrix=[];issues=[];limit=cfg.get('collection_interval_hours',2)*60+cfg.get('allowed_lateness_minutes',30)
    try:addonstamp=dt.datetime.strptime(addon['run_id'],'%Y%m%dT%H%M%S%fZ').replace(tzinfo=dt.timezone.utc).timestamp()*1000
    except (KeyError,ValueError):addonstamp=None
    db=sqlite3.connect('file:'+str(root/'normalized/market.sqlite3').replace('\\','/')+'?mode=ro',uri=True,timeout=30);db.row_factory=sqlite3.Row
    tables={x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for key,symbol in mapping.items():
        source,asset=key.split('|')
        for dataset in (['candles_5m'] if source.endswith('_spot') else ['funding','oi','oi_history']):
            table='candles' if dataset=='candles_5m' else dataset;tc='open_time' if table=='candles' else 'time'
            row={'asset':asset,'source':source,'symbol':symbol,'dataset':dataset,'rows':0,'status':'missing','missing_slots_7d':None,'internal_missing_slots_7d':None,'unit':'','identity':'venue_instrument_frozen; cross_venue_contract_pending'}
            if table not in tables:matrix.append(row);continue
            s=db.execute(f'SELECT COUNT(*) n,MIN({tc}) first,MAX({tc}) last,MAX(observed_at) observed FROM {table} WHERE source=? AND symbol=?',(source,symbol)).fetchone()
            row.update(rows=s['n'],earliest_ms=s['first'],latest_ms=s['last'],observed_at=s['observed'])
            observed=epoch(s['observed']);event=s['last'];row['status']='available' if s['n'] else 'missing'
            row['age_minutes']=round((nowms-event)/60000,1) if event else None
            row['observed_age_minutes']=round((nowms-observed)/60000,1) if observed else None
            if dataset=='candles_5m':
                q=db.execute('SELECT quote FROM candles WHERE source=? AND symbol=? LIMIT 1',(source,symbol)).fetchone()
                row.update(unit='price_in_'+q[0]+'; volume_base_asset' if q else 'native_quote',backfill='same_source; up to 7 days per run')
            elif dataset=='funding':row.update(unit='raw_settlement_rate',backfill='same_source; rolling 7 days',gap_assessment='UNKNOWN: per-instrument settlement schedule needed; do not assume 8h')
            else:
                row.update(unit='base_asset; provider counting convention retained',backfill='no exact replay of missed snapshots' if dataset=='oi' else 'same_source; rolling 7 days')
                if dataset=='oi_history':row['provider_semantics']='; '.join(x[0]+':'+x[1] for x in db.execute('SELECT DISTINCT provider_field,counting_convention FROM oi_history WHERE source=? AND symbol=?',(source,symbol)))
            fresh=event
            if dataset=='funding':
                # INSERT OR IGNORE preserves original observed_at between settlements.
                # Use successful poll evidence; a payment can legitimately be >150m old.
                stamps=[]
                for rep,stamp in ((main,epoch(latest.get('finished_at'))),(addon,addonstamp)):
                    if stamp and any(x.get('source')==source and x.get('symbol')==symbol and x.get('dataset')=='funding' and x.get('status')=='available' for x in rep.get('coverage',[])):stamps.append(stamp)
                fresh=max(stamps) if stamps else observed
                row['last_successful_poll_ms']=fresh
            budget=limit+(5 if dataset=='candles_5m' else 60 if dataset=='oi_history' else 0)
            if fresh and (nowms-fresh)/60000>budget:row['status']='stale'
            if dataset in ('candles_5m','oi_history') and s['n']:
                step=300000 if dataset=='candles_5m' else 3600000
                start=((int(max(nowms-7*86400000,s['first']))+step-1)//step)*step;end=nowms//step*step
                ts=[x[0] for x in db.execute(f'SELECT {tc} FROM {table} WHERE source=? AND symbol=? AND {tc}>=? AND {tc}<? ORDER BY {tc}',(source,symbol,start,end))]
                row.update(gap_window_start_ms=start,gap_window_end_ms=end,missing_slots_7d=len(set(range(start,end,step))-set(ts)))
                internal=max(0,(ts[-1]-ts[0])//step+1-len(ts)) if ts else 0
                row['internal_missing_slots_7d']=internal
                row['max_internal_gap_minutes_7d']=round(max([max(0,b-a-step) for a,b in zip(ts,ts[1:])]+[0])/60000,1)
                if internal and row['status']=='available':row['status']='partial'
            matrix.append(row)
    for source in ('binance_spot','okx_spot','binance_perpetual','bybit_perpetual'):
        for asset in cfg.get('assets',[]):
            if source+'|'+asset not in mapping:
                for dataset in (['candles_5m'] if source.endswith('_spot') else ['funding','oi','oi_history']):matrix.append({'asset':asset,'source':source,'dataset':dataset,'rows':0,'status':'not_configured','reason':'no eligible instrument in frozen mapping; recheck listing'})
    extra_map=load(root/'config/extra-instrument-map.json',{})
    extra_report=load(root/'reports/extra-venues.json',{})
    extra_stamp=epoch(extra_report.get('finished_at'))
    if cfg.get('extra_venues_enabled'):
        if not extra_stamp or (nowms-extra_stamp)/60000>limit:issues.append('extra_venues:report_missing_or_stale')
        if extra_report.get('issues'):issues.append('extra_venues:reported_degraded')
    for source in ('coinbase_spot','kraken_spot','hyperliquid_perpetual','bitflyer_spot','bitbank_spot','upbit_spot','bithumb_spot'):
        for asset in cfg.get('assets',[]):
            product=extra_map.get(source+'|'+asset)
            if not product:
                matrix.append({'asset':asset,'source':source,'dataset':'market_data','rows':0,'status':'not_configured' if extra_map else 'not_implemented','reason':'no frozen product; catalog absence is not proof of no listing'});continue
            table='perpetual_context' if source=='hyperliquid_perpetual' else 'venue_snapshots'
            row={'asset':asset,'source':source,'symbol':product['symbol'],'quote':product['quote'],'dataset':'perpetual_context' if source=='hyperliquid_perpetual' else 'ticker_snapshot','rows':0,'status':'missing','unit':'native_quote; native provider metric','backfill':'missed snapshots not exactly recoverable','identity':product['identity']}
            if table in tables:
                s=db.execute('SELECT COUNT(*),MIN(observed_at),MAX(observed_at) FROM '+table+' WHERE source=? AND symbol=?',(source,product['symbol'])).fetchone()
                row.update(rows=s[0],earliest_observed_at=s[1],observed_at=s[2],age_minutes=round((nowms-epoch(s[2]))/60000,1) if epoch(s[2]) else None)
                row['status']='available' if s[0] else 'missing'
                if row['age_minutes'] is not None and row['age_minutes']>limit:row['status']='stale'
            matrix.append(row)
    for asset in cfg.get('assets',[]):matrix.append({'asset':asset,'source':'defillama','dataset':'asset_fundamentals','status':'mapping_pending','reason':'shared protocol/chain/stablecoin metrics collected; no verified token entitlement join'})
    for source in ('defillama_protocols','defillama_chains','defillama_stablecoins'):
        reqs=[x for x in main.get('requests',[]) if x.get('source')==source and x.get('status')=='ok']
        observed=reqs[-1].get('observed_at') if reqs else None;stamp=epoch(observed)
        path=root/'raw'/pathlib.Path(reqs[-1]['file']).parent.name/pathlib.Path(reqs[-1]['file']).name if reqs and reqs[-1].get('file') else None
        age=(nowms-stamp)/60000 if stamp else None
        status='available' if path and path.is_file() and age is not None and age<=limit else 'stale' if path and path.is_file() else 'missing'
        matrix.append({'asset':'ALL_SHARED','source':source,'dataset':'raw_snapshot','status':status,'observed_at':observed,'age_minutes':round(age,1) if age is not None else None,'unit':'provider_native','backfill':'past snapshot replay not guaranteed','asset_mapping':'pending','file':str(path) if path else None})
    if cfg.get('research_collection_enabled'):
        research=load(root/'reports/research-collection.json',{})
        researchstamp=epoch(research.get('finished_at'))
        if not researchstamp or (nowms-researchstamp)/60000>limit:issues.append('research:report_missing_or_stale')
        if research.get('issues'):issues.append('research:reported_degraded')
        for key,symbol in mapping.items():
            source,asset=key.split('|')
            if not source.endswith('_spot'):continue
            row={'source':source,'asset':asset,'symbol':symbol,'dataset':'candles_1d','status':'missing','rows':0,'unit':'native_quote; UTC daily close','backfill':'same-source 200-day initial window; new tails thereafter'}
            if 'daily_candles' in tables:
                s=db.execute('SELECT COUNT(*) n,MIN(open_time) first,MAX(open_time) last,MAX(observed_at) observed FROM daily_candles WHERE source=? AND symbol=?',(source,symbol)).fetchone()
                row.update(rows=s['n'],earliest_ms=s['first'],latest_ms=s['last'],observed_at=s['observed'],age_minutes=round((nowms-s['last'])/60000,1) if s['last'] else None)
                row['status']='available' if s['n'] else 'missing'
                if s['last'] and (nowms-s['last'])/60000>48*60:row['status']='stale'
                if s['n']:
                    row['internal_missing_days']=max(0,(s['last']-s['first'])//86400000+1-s['n'])
                    if row['internal_missing_days'] and row['status']=='available':row['status']='partial'
            matrix.append(row)
        if 'defi_metrics' not in tables:issues.append('defi_metrics:table_missing')
        else:
            for source in ('defillama_protocols','defillama_chains','defillama_stablecoins'):
                stats=db.execute('SELECT COUNT(*),MAX(observed_at) FROM defi_metrics WHERE source=?',(source,)).fetchone();stamp=epoch(stats[1])
                age=(nowms-stamp)/60000 if stamp else None
                matrix.append({'asset':'ALL_SHARED','source':source,'dataset':'normalized_metrics','rows':stats[0],'observed_at':stats[1],'age_minutes':round(age,1) if age is not None else None,'status':'available' if stats[0] and age is not None and age<=limit else 'stale' if stats[0] else 'missing','unit':'named source metric; no token entitlement join','scope':'500 fixed protocol records; all returned chains/stablecoins','backfill':'rebuild from retained raw snapshots'})
    counts={t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('candles','funding','oi','oi_history','daily_candles','defi_metrics','venue_snapshots','perpetual_context') if t in tables};db.close()
    mandatory=[r for r in matrix if r['status'] not in ('not_implemented','not_configured','mapping_pending')]
    issues.extend(r['source']+'|'+r['asset']+'|'+r['dataset']+':'+r['status'] for r in mandatory if r['status'] in ('missing','stale','partial'))
    for name,stamp,report in [('collector',epoch(latest.get('finished_at')),main),('derivatives',addonstamp,addon)]:
        if not stamp or (nowms-stamp)/60000>limit:issues.append(name+':report_missing_or_stale')
        if report.get('issues') or (name=='collector' and latest.get('health')!='ok'):issues.append(name+':reported_degraded')
    backup=load(root/'reports/daily-drive-backup.json',{});verified=epoch(backup.get('verified_at'));bage=(nowms-verified)/3600000 if verified else None
    if not cfg.get('automatic_drive_backup_enabled'):issues.append('backup:not_enabled')
    if not backup.get('cloud_upload_verified') or backup.get('cloud_restore_test')!='passed':issues.append('backup:not_verified')
    if bage is None or bage>26:issues.append('backup:older_than_26h_or_missing')
    err=root/'reports/daily-drive-backup-error.json'
    if err.is_file() and verified and err.stat().st_mtime*1000>verified:issues.append('backup:newer_error_report')
    size=0
    for p in root.rglob('*'):
        try:
            if p.is_file():size+=p.stat().st_size
        except FileNotFoundError:pass
    free=shutil.disk_usage(root).free
    if size>cfg.get('local_storage_budget_bytes',50e9)*.7:issues.append('storage:above_70_percent')
    if free<2e9:issues.append('storage:less_than_2GB_free')
    health={'schema_version':2,'checked_at':now.isoformat(),'status':'warning' if issues else 'ok','issues':issues,'unknown':['funding gaps need settlement schedule','cross-venue contract identity pending','off-PC heartbeat not deployed','separate-machine recovery not verified'],'scope':'configured feeds only; ok does not mean acceptance complete','database_rows':counts,'local_bytes':size,'budget_bytes':cfg.get('local_storage_budget_bytes'),'disk_free_bytes':free,'cloud_backup_automatic':cfg.get('automatic_drive_backup_enabled',False),'backup_age_hours':round(bage,2) if bage is not None else None,'backup_verified_at':backup.get('verified_at'),'identity_verification_complete':cfg.get('identity_verification_complete',False),'components':{d:{'series':len([r for r in mandatory if r['dataset']==d]),'issues':len([r for r in mandatory if r['dataset']==d and r['status']!='available'])} for d in ('candles_5m','candles_1d','funding','oi','oi_history','raw_snapshot')}}
    save(root/'reports/health.json',health);save(root/'reports/data-coverage.json',{'generated_at':now.isoformat(),'rows':matrix})
    csvpath=root/'reports/data-coverage.csv';csvtmp=csvpath.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    with csvtmp.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=sorted({k for r in matrix for k in r}));w.writeheader();w.writerows(matrix)
    csvtmp.replace(csvpath)
    return health,matrix
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=pathlib.Path,default=ROOT);a=p.parse_args();h,m=audit(a.root)
    print(json.dumps({'status':h['status'],'issues':h['issues'],'coverage_rows':len(m),'database_rows':h['database_rows']},ensure_ascii=False));sys.exit(2 if h['issues'] else 0)
