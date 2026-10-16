"""Checksum code/config snapshots and isolated rollback rehearsals."""
import argparse,datetime as dt,hashlib,json,pathlib,shutil,sqlite3,subprocess,sys,gzip
from backup_integrity import digest_file
from data_audit import ROOT,save,load
CODE=pathlib.Path(__file__).parent
ALLOW=['settings.json','asset-registry.json','instrument-map.json','data-semantics.json','protocol-mapping-policy.json','extra-instrument-map.json','defi-protocol-scope.json']
def capture():
    folder=ROOT/'releases'/('release-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ'));folder.mkdir(parents=True)
    files={}
    for p in CODE.iterdir():
        if p.suffix not in ('.py','.ps1','.html'):continue
        target=folder/'src'/p.name;target.parent.mkdir(exist_ok=True);target.write_bytes(p.read_bytes());files[str(target.relative_to(folder))]=hashlib.sha256(target.read_bytes()).hexdigest()
    for name in ALLOW:
        if not (ROOT/'config'/name).is_file():continue
        value=load(ROOT/'config'/name,{})
        if name=='settings.json':
            value.pop('exchange_api_key',None);value.pop('exchange_api_secret',None)
            value['trading_enabled']=False
        target=folder/'config'/name;save(target,value);files[str(target.relative_to(folder))]=hashlib.sha256(target.read_bytes()).hexdigest()
    # Recovery data are part of release evidence, not silently overwritten on rollback.
    db=sqlite3.connect('file:'+str(ROOT/'normalized/market.sqlite3').replace('\\','/')+'?mode=ro',uri=True);out=sqlite3.connect(folder/'market.sqlite3');db.backup(out);assert out.execute('PRAGMA integrity_check').fetchone()[0]=='ok';schema=out.execute("SELECT name,sql FROM sqlite_master WHERE type='table'").fetchall();out.close();db.close()
    with (folder/'market.sqlite3').open('rb') as src,gzip.open(folder/'market.sqlite3.gz','wb',compresslevel=6) as dest:shutil.copyfileobj(src,dest,1024*1024)
    (folder/'market.sqlite3').unlink();files['market.sqlite3.gz']=digest_file(folder/'market.sqlite3.gz')
    save(folder/'manifest.json',{'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),'files':files,'schema':schema,'scope':'code, allowlisted sanitized config, consistent DB; excludes credentials and raw'})
    save(ROOT/'reports/latest-release.json',{'path':str(folder),'files':len(files)});print(str(folder));return folder
def stage(folder):
    folder=folder.resolve();base=(ROOT/'releases').resolve()
    if not folder.is_relative_to(base):raise ValueError('release outside intended root')
    manifest=load(folder/'manifest.json')
    if not manifest:raise ValueError('release manifest missing')
    start=__import__('time').perf_counter()
    for name,sha in manifest['files'].items():
        p=(folder/name).resolve()
        if not p.is_relative_to(folder) or digest_file(p)!=sha:raise ValueError('release checksum mismatch')
    dest=ROOT/'sandbox'/('rollback-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ'));shutil.copytree(folder,dest)
    for p in (dest/'src').glob('*.py'):compile(p.read_text(encoding='utf-8-sig'),str(p),'exec')
    if (dest/'market.sqlite3.gz').is_file():
        with gzip.open(dest/'market.sqlite3.gz','rb') as src,(dest/'market.sqlite3').open('wb') as out:shutil.copyfileobj(src,out,1024*1024)
    db=sqlite3.connect(dest/'market.sqlite3');assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok';db.close()
    result={'release':str(folder),'staged':str(dest),'seconds':round(__import__('time').perf_counter()-start,3),'checksum_verified':True,'python_syntax_verified':True,'database_integrity':'ok','production_switched':False,'limitations':['staging/syntax/integrity only; full historical collector behavior not exercised','production rollback requires pausing scheduled tasks and reviewing schema/data compatibility']};save(ROOT/'reports/rollback-rehearsal.json',result);print(json.dumps(result))
    return dest
def restore_code(folder,code_root,resource_root):
    # This function never replaces data. Only additive-compatible schemas accepted.
    manifest=load(folder/'manifest.json');current=sqlite3.connect(resource_root/'normalized/market.sqlite3')
    try:
        schema=dict(current.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
        for name,sql in manifest['schema']:
            if schema.get(name)!=sql:raise ValueError('schema compatibility requires separate recovery plan')
    finally:current.close()
    for name,sha in manifest['files'].items():
        p=(folder/name).resolve()
        if not p.is_relative_to(folder.resolve()) or digest_file(p)!=sha:raise ValueError('checksum mismatch')
    existing=load(resource_root/'config/settings.json',{})
    for p in (folder/'src').iterdir():
        if p.suffix in ('.py','.ps1','.html'):shutil.copy2(p,code_root/p.name)
    for p in (folder/'config').glob('*.json'):
        value=load(p)
        if p.name=='settings.json':
            for key in ('exchange_api_key','exchange_api_secret','google_integration_config','drive_folder_id','drive_folder_name','drive_backup_task','drive_backup_time_local','automatic_drive_backup_enabled','cloud_storage_budget_bytes','cloud_backup_versions','local_storage_budget_bytes','monthly_service_budget','runner_policy'):
                if key in existing:value[key]=existing[key]
            value['trading_enabled']=False
        save(resource_root/'config'/p.name,value)
def rehearse_previous(folder):
    staged=stage(folder);code=staged/'simulation-code';res=staged/'simulation-resources';shutil.copytree(CODE,code);(res/'normalized').mkdir(parents=True);shutil.copytree(ROOT/'config',res/'config',ignore=shutil.ignore_patterns('google-integration.json'))
    live=sqlite3.connect('file:'+str(ROOT/'normalized/market.sqlite3').replace('\\','/')+'?mode=ro',uri=True);copy=sqlite3.connect(res/'normalized/market.sqlite3');live.backup(copy);copy.close();live.close()
    before=hashlib.sha256((res/'normalized/market.sqlite3').read_bytes()).hexdigest();start=__import__('time').perf_counter();restore_code(folder,code,res)
    assert hashlib.sha256((res/'normalized/market.sqlite3').read_bytes()).hexdigest()==before
    for p in (folder/'src').glob('*.py'):assert (code/p.name).read_bytes()==p.read_bytes()
    result={'release':str(folder),'simulation':str(staged),'code_restored_on_copy':True,'database_unchanged':True,'schema_compatible':True,'seconds':round(__import__('time').perf_counter()-start,3),'production_switched':False,'limitation':'full source API collection not run using previous release'};save(ROOT/'reports/rollback-rehearsal.json',result);save(ROOT/'reports/rollback-baseline-rehearsal.json',result);print(json.dumps(result))
def apply_previous(folder):
    # Stop only future starts; refuse if any collector/backup/monitor is currently running.
    tasks=['CryptoData-Collect-2h','CryptoData-DriveBackup-Daily','CryptoData-Monitor-15m']
    ps="$tasks=@('"+"','".join(tasks)+"'); $items=$tasks|ForEach-Object {Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue}; if($items|Where-Object State -eq 'Running'){exit 2}; $enabled=@($items|Where-Object {$_.Settings.Enabled}); $items|ForEach-Object {Disable-ScheduledTask -TaskName $_.TaskName | Out-Null}; if($tasks|ForEach-Object {Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue}|Where-Object State -eq 'Running'){$enabled|ForEach-Object {Enable-ScheduledTask -TaskName $_.TaskName|Out-Null};exit 3}"
    # Validate before pausing and retain a complete current snapshot.
    stage(folder);capture()
    proc=subprocess.run(['powershell.exe','-NoProfile','-Command',ps],capture_output=True)
    if proc.returncode:raise RuntimeError('tasks running or scheduler unavailable; no rollback applied')
    try:restore_code(folder,CODE,ROOT)
    except Exception:raise RuntimeError('rollback failed; tasks remain disabled; inspect retained release')
    cfg=load(ROOT/'config/settings.json',{})
    enabled=['CryptoData-Collect-2h','CryptoData-DriveBackup-Daily']
    if cfg.get('local_monitor_enabled'):enabled.append('CryptoData-Monitor-15m')
    for task in enabled:subprocess.run(['powershell.exe','-NoProfile','-Command',"Enable-ScheduledTask -TaskName '"+task+"' | Out-Null"],check=True)
    save(ROOT/'reports/rollback-applied.json',{'at':dt.datetime.now(dt.timezone.utc).isoformat(),'release':str(folder),'database_replaced':False,'enabled_tasks':enabled})
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['capture','stage','rehearse','apply-code']);p.add_argument('--release',type=pathlib.Path);a=p.parse_args()
    if a.action=='capture':capture()
    else:
        folder=a.release or pathlib.Path(load(ROOT/'reports/latest-release.json')['path'])
        {'stage':stage,'rehearse':rehearse_previous,'apply-code':apply_previous}[a.action](folder)
