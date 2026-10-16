import json,pathlib,sqlite3,zipfile,hashlib,shutil
from backup_integrity import digest_file
root=pathlib.Path(r'E:\CryptoData tai nguyen')
baseline=sorted((root/'releases').glob('baseline-*'))[0]
with zipfile.ZipFile(root/'backups/cryptodata-recovery-20261014T060310Z.zip') as z:
    with z.open('normalized/market.sqlite3') as src,(baseline/'market.sqlite3').open('wb') as dest:shutil.copyfileobj(src,dest,1024*1024)
settings=json.loads((baseline/'config/settings.json').read_text(encoding='utf-8-sig'))
settings.pop('exchange_api_key',None);settings.pop('exchange_api_secret',None)
(baseline/'config/settings.json').write_text(json.dumps(settings,indent=2),encoding='utf8')
files={str(p.relative_to(baseline)):digest_file(p) for folder in ('src','config') for p in (baseline/folder).iterdir() if p.is_file() and p.suffix in ('.py','.ps1','.json')}
files['market.sqlite3']=digest_file(baseline/'market.sqlite3')
db=sqlite3.connect(baseline/'market.sqlite3');schema=db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'").fetchall();db.close()
(baseline/'manifest.json').write_text(json.dumps({'files':files,'schema':schema,'scope':'pre-change source/config snapshot; DB recovered from verified 13:03 backup'},indent=2),encoding='utf8')
print(str(baseline))
