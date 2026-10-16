"""Daily Drive backup using current-user DPAPI. Never log credentials."""
import datetime as dt,hashlib,json,pathlib,sqlite3,subprocess,urllib.request,urllib.parse,uuid,zipfile,time
import urllib.error,shutil
from backup_integrity import digest_file,verify_bundle
R=pathlib.Path(r'E:\CryptoData tai nguyen')
S=pathlib.Path(r'E:\CryptoData\src')
API='https://www.googleapis.com/drive/v3/files'
def main():
 cfg=json.loads((R/'config/google-integration.json').read_text(encoding='utf-8-sig'))
 settings=json.loads((R/'config/settings.json').read_text(encoding='utf-8-sig'))
 folder=cfg['drive_folder_id']
 if folder!=settings['drive_folder_id'] or cfg['target_project_id']!='cryptodata-caothanhtran14':raise RuntimeError('configuration mismatch')
 ps=r'''Add-Type -AssemblyName System.Security
$p='E:\CryptoData tai nguyen\credentials\google-oauth-token.dpapi'
$b=[IO.File]::ReadAllBytes($p)
try {$v=[Security.Cryptography.ProtectedData]::Unprotect($b,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)} catch {$b=[Convert]::FromBase64String([IO.File]::ReadAllText($p));$v=[Security.Cryptography.ProtectedData]::Unprotect($b,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)}
[Console]::Write([Text.Encoding]::UTF8.GetString($v))'''
 proc=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',ps],capture_output=True)
 if proc.returncode:raise RuntimeError('DPAPI unavailable for this Windows user')
 auth=json.loads(proc.stdout.decode('utf-8-sig'))
 body=urllib.parse.urlencode({'client_id':auth['client_id'],'client_secret':auth['client_secret'],'refresh_token':auth['refresh_token'],'grant_type':'refresh_token'}).encode()
 with urllib.request.urlopen(urllib.request.Request('https://oauth2.googleapis.com/token',data=body),timeout=40) as f:token=json.load(f)['access_token']
 def request(url,method='GET',data=None,content=None):
  headers={'Authorization':'Bearer '+token}
  if content:headers['Content-Type']=content
  with urllib.request.urlopen(urllib.request.Request(url,data=data,headers=headers,method=method),timeout=120) as f:return f.read()
 def js(url,**kw):return json.loads(request(url,**kw))
 meta=js(API+'/'+folder+'?fields=id,mimeType,capabilities')
 if meta['mimeType']!='application/vnd.google-apps.folder' or not meta.get('capabilities',{}).get('canAddChildren'):raise RuntimeError('folder not writable')
 subprocess.run([__import__('sys').executable,str(S/'make_backup_bundle.py')],check=True,capture_output=True)
 receipt=json.loads((R/'reports/backup-bundle.json').read_text());path=pathlib.Path(receipt['path']);size=path.stat().st_size
 files=[];page=None
 while True:
  params={'q':"'"+folder+"' in parents and trashed=false",'fields':'nextPageToken,files(id,name,size,createdTime,appProperties)','pageSize':1000}
  if page:params['pageToken']=page
  result=js(API+'?'+urllib.parse.urlencode(params));files+=result.get('files',[]);page=result.get('nextPageToken')
  if not page:break
 cap=settings.get('cloud_storage_budget_bytes',10_000_000_000)
 used=sum(int(x.get('size',0)) for x in files)
 if used+size>cap:raise RuntimeError('cloud budget reached; old backups preserved')
 quota=js('https://www.googleapis.com/drive/v3/about?fields=storageQuota')['storageQuota']
 if quota.get('limit') and int(quota['limit'])-int(quota.get('usage',0))<size+100_000_000:raise RuntimeError('insufficient account storage; old backups preserved')
 if shutil.disk_usage(R).free<size*8+2_000_000_000:raise RuntimeError('insufficient restore working space')
 metadata={'name':path.name,'parents':[folder],'appProperties':{'application':'cryptodata-daily-backup','verified':'false'}}
 init=urllib.request.Request('https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id,size',method='POST',data=json.dumps(metadata).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json; charset=UTF-8','X-Upload-Content-Type':'application/zip','X-Upload-Content-Length':str(size)})
 with urllib.request.urlopen(init,timeout=120) as f:session=f.headers['Location']
 # Session URL is sensitive and stays in memory. Chunks are multiples of 256 KiB.
 if urllib.parse.urlparse(session).scheme!='https' or urllib.parse.urlparse(session).hostname!='www.googleapis.com':raise RuntimeError('unexpected upload host')
 uploaded=None;offset=0
 with path.open('rb') as source:
  while offset<size:
   source.seek(offset);chunk=source.read(8*1024*1024)
   req=urllib.request.Request(session,method='PUT',data=chunk,headers={'Authorization':'Bearer '+token,'Content-Type':'application/zip','Content-Length':str(len(chunk)),'Content-Range':f'bytes {offset}-{offset+len(chunk)-1}/{size}'})
   try:
    with urllib.request.urlopen(req,timeout=120) as f:uploaded=json.load(f);offset=size
   except urllib.error.HTTPError as e:
    if e.code!=308:raise
    accepted=e.headers.get('Range','')
    if not accepted.startswith('bytes=0-'):raise RuntimeError('upload progress missing')
    nxt=int(accepted.split('-')[-1])+1
    if nxt<=offset or nxt>offset+len(chunk):raise RuntimeError('invalid upload progress')
    offset=nxt
 if not uploaded:raise RuntimeError('upload did not complete')
 fid=uploaded['id'];download=R/'state'/('download-'+uuid.uuid4().hex+'.zip');restore=R/'state'/('restore-'+uuid.uuid4().hex+'.sqlite3')
 try:
  with urllib.request.urlopen(urllib.request.Request(API+'/'+fid+'?alt=media',headers={'Authorization':'Bearer '+token}),timeout=120) as src,download.open('wb') as dest:shutil.copyfileobj(src,dest,1024*1024)
  if digest_file(download)!=receipt['sha256']:raise RuntimeError('cloud hash mismatch; old backups preserved')
  manifest=verify_bundle(download,restore)
 finally:restore.unlink(missing_ok=True);download.unlink(missing_ok=True)
 js(API+'/'+fid,method='PATCH',data=json.dumps({'appProperties':{'application':'cryptodata-daily-backup','verified':'true'}}).encode(),content='application/json')
 # Delete only older verified bundles owned by this app, AFTER new restore passes.
 old=sorted([x for x in files if x.get('appProperties',{}).get('application')=='cryptodata-daily-backup' and x.get('appProperties',{}).get('verified')=='true'],key=lambda x:x.get('createdTime',''),reverse=True)
 deleted=[]
 for x in old[2:]:request(API+'/'+x['id'],method='DELETE');deleted.append(x['id'])
 receipt.update(cloud_upload_verified=True,cloud_restore_test='passed',drive_file_id=fid,drive_folder_id=folder,verified_at=dt.datetime.now(dt.timezone.utc).isoformat(),deleted_old_verified=deleted,retention_versions=3,cloud_budget_bytes=cap,upload_method='resumable_8MiB_chunks',quota_checked_at=dt.datetime.now(dt.timezone.utc).isoformat(),quota_usage_bytes=int(quota.get('usage',0)),quota_limit_bytes=int(quota['limit']) if quota.get('limit') else None)
 (R/'reports/daily-drive-backup.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
 # Local ZIP retention only tracks app-verified packages from this version onward.
 indexpath=R/'state/verified-local-backups.json'
 previous=json.loads(indexpath.read_text()) if indexpath.is_file() else []
 previous=[x for x in previous if x.get('path')!=str(path)]
 previous.append({'path':str(path),'sha256':receipt['sha256'],'verified_at':receipt['verified_at']})
 keep=int(settings.get('local_zip_backup_versions',3))
 if keep<2:raise RuntimeError('local retention must preserve at least 2 verified packages')
 for entry in previous[:-keep]:
  oldpath=pathlib.Path(entry['path']).resolve()
  if oldpath.parent==(R/'backups').resolve() and oldpath.name.startswith('cryptodata-recovery-') and oldpath.suffix=='.zip':oldpath.unlink(missing_ok=True)
 indexpath.write_text(json.dumps(previous[-keep:],indent=2),encoding='utf8')
 print(json.dumps({'status':'verified','file_id':fid,'bytes':size,'rows':manifest['rows'],'older_deleted':len(deleted)}))
if __name__=='__main__':
 try:main()
 except Exception as e:
  # Never print response bodies or credential-bearing request objects.
  (R/'reports/daily-drive-backup-error.json').write_text(json.dumps({'at':dt.datetime.now(dt.timezone.utc).isoformat(),'error_type':type(e).__name__,'status':'failed','old_backups_preserved_until_verification':True}),encoding='utf8')
  print('Backup failed: '+type(e).__name__+'; see sanitized error report.');raise SystemExit(1)
