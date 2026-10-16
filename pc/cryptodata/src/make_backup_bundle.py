"""Create and verify a portable research-data backup, without credentials."""
import datetime as dt,hashlib,json,pathlib,sqlite3,zipfile
from backup_integrity import digest_file,verify_bundle
from collect_pilot import R
out=R/'backups';out.mkdir(exist_ok=True)
stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
tmp=out/('bundle-'+stamp+'.sqlite3')
source=sqlite3.connect(R/'normalized/market.sqlite3');dest=sqlite3.connect(tmp)
try:
    source.backup(dest)
    assert dest.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    counts={t:dest.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ['candles','funding','oi','oi_history','daily_candles','defi_metrics','processed_defi','venue_snapshots','perpetual_context','runs'] if dest.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone()}
finally:dest.close();source.close()
config=json.loads((R/'config/settings.json').read_text(encoding='utf-8-sig'))
allowed=['schema_version','assets','max_assets','collection_interval_hours','allowed_lateness_minutes','local_storage_budget_bytes','monthly_service_budget','trading_enabled','research_only','research_collection_enabled','extra_venues_enabled','defi_normalization_policy','local_zip_backup_versions']
clean={k:config[k] for k in allowed if k in config};clean.update(automatic_schedule_enabled=False,automatic_drive_backup_enabled=False,local_monitor_enabled=False,trading_enabled=False,exchange_api_key='',exchange_api_secret='')
manifest={'created_at_utc':stamp,'rows':counts,'scope':'Normalized DB, source code, sanitized configuration; excludes raw API history and credentials','files':{}}
target=out/('cryptodata-recovery-'+stamp+'.zip')
try:
    with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED) as z:
        def add(name,data):
            z.writestr(name,data);manifest['files'][name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        z.write(tmp,'normalized/market.sqlite3');manifest['files']['normalized/market.sqlite3']={'bytes':tmp.stat().st_size,'sha256':digest_file(tmp)}
        add('config/settings.json',json.dumps(clean,indent=2).encode())
        for name in ['asset-registry.json','instrument-map.json','data-semantics.json','protocol-mapping-policy.json','official-identity-evidence.json','extra-instrument-map.json','defi-protocol-scope.json']:
            if not (R/'config'/name).is_file():continue
            add('config/'+name,(R/'config'/name).read_bytes())
        for p in pathlib.Path(r'E:\CryptoData\src').glob('*.py'):add('src/'+p.name,p.read_bytes())
        for p in pathlib.Path(r'E:\CryptoData\src').glob('*.ps1'):add('src/'+p.name,p.read_bytes())
        for p in pathlib.Path(r'E:\CryptoData\src').glob('*.html'):add('src/'+p.name,p.read_bytes())
        for extension in ('*.gs','*.cjs','*.yml'):
            for p in pathlib.Path(r'E:\CryptoData\src').glob(extension):add('src/'+p.name,p.read_bytes())
        add('OPERATIONS.md',(R/'OPERATIONS.md').read_bytes())
        z.writestr('manifest.json',json.dumps(manifest,indent=2))
    verify_bundle(target,out/('verify-'+stamp+'.sqlite3'))
finally:tmp.unlink(missing_ok=True)
receipt={'path':str(target),'bytes':target.stat().st_size,'sha256':digest_file(target),'rows':counts,'verified':True,'cloud_upload_verified':False,'streaming_verification':True}
(R/'reports/backup-bundle.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
print(json.dumps(receipt,indent=2))
