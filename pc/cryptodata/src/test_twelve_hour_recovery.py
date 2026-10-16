"""Offline 12-hour outage replay using real stored candles; never writes live DB."""
import contextlib, io, json, sqlite3, datetime as dt
from collect_pilot import R, STEP
from collect_incremental import Incremental, candle_start

def main():
    live=sqlite3.connect((R/'normalized/market.sqlite3').as_uri()+'?mode=ro',uri=True)
    db=sqlite3.connect(':memory:');live.backup(db);live.close()
    results=[]
    series=db.execute('SELECT DISTINCT source,base,symbol,quote FROM candles').fetchall()
    for source,asset,symbol,quote in series:
        rows=db.execute('SELECT * FROM candles WHERE source=? AND symbol=? ORDER BY open_time DESC LIMIT 146',(source,symbol)).fetchall()[::-1]
        if len(rows)!=146 or any(b[6]-a[6]!=STEP for a,b in zip(rows,rows[1:])):
            results.append(dict(source=source,symbol=symbol,status='SKIP',reason='not enough contiguous fixture'));continue
        removed=rows[-144:];start=removed[0][6];end=removed[-1][6]+STEP
        before=db.execute('SELECT COUNT(*) FROM candles WHERE source=? AND symbol=?',(source,symbol)).fetchone()[0]
        db.execute('DELETE FROM candles WHERE source=? AND symbol=? AND open_time>=?',(source,symbol,start));db.commit()
        c=object.__new__(Incremental);c.db=db;c.start=end-7*86400000;c.end=end;c.id='offline-12h-recovery';c.coverage=[];c.mapping={source+'|'+asset:symbol}
        def fixture(src,url,params):
            if src=='binance_spot':
                return [[r[6],*r[7:12],r[6]+STEP-1] for r in rows if params['startTime']<=r[6]<=params['endTime']][:params['limit']]
            return {'data':[[r[6],*r[7:12],'0','0','1'] for r in rows[::-1] if r[6]<params['after']][:params['limit']]}
        c.get=fixture
        assert candle_start(db,source,symbol,end,c.start)<=start
        with contextlib.redirect_stdout(io.StringIO()): c.run_candles(source,asset,symbol,quote)
        assert c.coverage[-1]['status']=='complete',c.coverage[-1]
        restored=db.execute('SELECT * FROM candles WHERE source=? AND symbol=? AND open_time>=? ORDER BY open_time',(source,symbol,start)).fetchall()
        assert [r[:12] for r in restored]==[r[:12] for r in removed]
        with contextlib.redirect_stdout(io.StringIO()): c.run_candles(source,asset,symbol,quote)
        after=db.execute('SELECT COUNT(*) FROM candles WHERE source=? AND symbol=?',(source,symbol)).fetchone()[0]
        assert before==after
        results.append(dict(source=source,symbol=symbol,status='PASS',restored_candles=144,duplicate_replay=True))
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok';db.close()
    report=dict(checked_at=dt.datetime.now(dt.timezone.utc).isoformat(),status='PASS' if results and all(r['status']=='PASS' for r in results) else 'PARTIAL',scope='Offline real-data fixture, not a live API or physical PC shutdown test',production_modified=False,results=results)
    (R/'reports/twelve-hour-recovery.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k!='results'}));print('series',len(results),'passed',sum(x['status']=='PASS' for x in results))
if __name__=='__main__':main()
