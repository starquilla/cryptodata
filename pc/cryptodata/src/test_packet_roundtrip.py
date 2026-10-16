"""Local file transfer simulation, NOT cloud acceptance. Read-only live source."""
import datetime as dt, hashlib, json, sqlite3, tempfile, pathlib
from collect_pilot import R
from candle_packet import encode, import_packet as candles_import
from derivative_packet import import_packet as derivative_import

def main():
    live=sqlite3.connect((R/'normalized/market.sqlite3').as_uri()+'?mode=ro',uri=True)
    target=sqlite3.connect(':memory:');now=int(dt.datetime.now(dt.timezone.utc).timestamp()*1000);results=[]
    with tempfile.TemporaryDirectory(prefix='packet-roundtrip-',dir=R/'state') as work:
        for table,dataset in [('candles','candles_5m'),('funding','funding'),('oi_history','oi_history')]:
            target.execute(live.execute('SELECT sql FROM sqlite_master WHERE name=?',(table,)).fetchone()[0])
            # Include every currently represented source/symbol, up to 20 rows each.
            rows=[]
            for source,symbol in live.execute('SELECT DISTINCT source,symbol FROM '+table):
                order='open_time' if table=='candles' else 'time'
                rows.extend([list(r) for r in live.execute('SELECT * FROM '+table+' WHERE source=? AND symbol=? ORDER BY '+order+' DESC LIMIT 20',(source,symbol))])
            raw=encode({'schema':1,'dataset':dataset,'rows':rows})
            path=pathlib.Path(work)/(dataset+'.json');tmp=path.with_suffix('.tmp');tmp.write_bytes(raw);tmp.replace(path)
            downloaded=path.read_bytes();assert hashlib.sha256(raw).digest()==hashlib.sha256(downloaded).digest()
            if table=='candles':
                mapping={tuple(r[:6]) for r in rows};load=lambda b:candles_import(target,b,mapping,now)
            else:
                mapping={tuple(r[:2]) for r in rows};semantics={}
                if table=='oi_history':
                    for r in rows:semantics.setdefault(tuple(r[:2]),set()).add(tuple(r[5:8]))
                load=lambda b:derivative_import(target,b,mapping,semantics,now)
            first=load(downloaded);second=load(downloaded)
            assert first['inserted']==len(rows) and second=={'inserted':0,'duplicates':len(rows)}
            # A truncated file must not partially import.
            try:load(downloaded[:-8])
            except (ValueError,KeyError):pass
            else:raise AssertionError('truncated packet accepted')
            assert target.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==len(rows)
            results.append(dict(dataset=dataset,rows=len(rows),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),replay_no_duplicates=True,truncated_rejected=True))
        assert target.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    live.close();target.close()
    report=dict(checked_at=dt.datetime.now(dt.timezone.utc).isoformat(),status='PASS',scope='Local file roundtrip using real stored samples, no cloud upload or live database import',production_modified=False,results=results)
    (R/'reports/packet-roundtrip.json').write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
