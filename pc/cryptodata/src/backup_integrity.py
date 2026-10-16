"""Bounded-memory ZIP verification and isolated SQLite restore."""
import hashlib,json,pathlib,re,shutil,sqlite3,zipfile
def digest_file(path):
    h=hashlib.sha256()
    with pathlib.Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
def verify_bundle(path,restore):
    restore=pathlib.Path(restore)
    try:
        with zipfile.ZipFile(path) as z:
            if z.testzip():raise ValueError('ZIP CRC failed')
            manifest=json.loads(z.read('manifest.json'))
            for name,item in manifest['files'].items():
                h=hashlib.sha256();size=0
                with z.open(name) as f:
                    for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk);size+=len(chunk)
                if h.hexdigest()!=item['sha256'] or size!=item['bytes']:raise ValueError('manifest mismatch')
            with z.open('normalized/market.sqlite3') as src,restore.open('wb') as dest:shutil.copyfileobj(src,dest,1024*1024)
        db=sqlite3.connect(restore)
        try:
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('restore integrity failed')
            for table,n in manifest['rows'].items():
                if not re.fullmatch('[a-z_]+',table):raise ValueError('invalid table identifier')
                if db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]!=n:raise ValueError('restore count mismatch')
        finally:db.close()
        return manifest
    finally:restore.unlink(missing_ok=True)
