"""Replay a 12-hour outage on an in-memory copy, using frozen stored history."""
import datetime as dt, json, sqlite3, types
from collect_pilot import R
from collect_derivatives_history import collect

def main():
    live=sqlite3.connect((R/'normalized/market.sqlite3').as_uri()+'?mode=ro',uri=True)
    db=sqlite3.connect(':memory:');live.backup(db);live.close()
    mapping=json.loads((R/'config/instrument-map.json').read_text());results=[]
    for key,symbol in mapping.items():
        source,asset=key.split('|')
        if source not in ('binance_perpetual','bybit_perpetual'):continue
        oi=db.execute('SELECT * FROM oi_history WHERE source=? AND symbol=? ORDER BY time',(source,symbol)).fetchall()
        funding=db.execute('SELECT * FROM funding WHERE source=? AND symbol=? ORDER BY time',(source,symbol)).fetchall()
        if not oi:results.append(dict(source=source,symbol=symbol,status='SKIP'));continue
        end=oi[-1][3]+3600000;start=end-12*3600000
        original={'oi_history':[r for r in oi if start<=r[3]<end], 'funding':[r for r in funding if start<=r[2]<end]}
        counts={table:db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in original}
        for table in original:db.execute('DELETE FROM '+table+' WHERE source=? AND symbol=? AND time>=? AND time<?',(source,symbol,start,end))
        db.commit()
        def fixture(src,url,p):
            if 'funding' in url:
                rows=[r for r in funding if p['startTime']<=r[2]<=p['endTime']]
                if src=='binance_perpetual':return [{'fundingTime':r[2],'fundingRate':r[3]} for r in rows[:p['limit']]]
                return {'result':{'list':[{'fundingRateTimestamp':r[2],'fundingRate':r[3]} for r in rows[::-1][:p['limit']]]}}
            rows=[r for r in oi if p['startTime']<=r[3]<=p['endTime']]
            if src=='binance_perpetual':return [{'timestamp':r[3],'sumOpenInterest':r[4],'sumOpenInterestValue':r[8]} for r in rows[:p['limit']]]
            rows=rows[::-1];offset=int(p.get('cursor','0'));page=rows[offset:offset+p['limit']]
            return {'result':{'list':[{'timestamp':r[3],r[6]:r[4]} for r in page], 'nextPageCursor':str(offset+p['limit']) if offset+p['limit']<len(rows) else ''}}
        c=types.SimpleNamespace(db=db,end=end,get=fixture,id='offline-derivatives-outage',coverage=[])
        collect(c,source,asset,symbol)
        assert all(r['status']!='error' for r in c.coverage),c.coverage
        for table,expected in original.items():
            got=db.execute('SELECT * FROM '+table+' WHERE source=? AND symbol=? AND time>=? AND time<? ORDER BY time',(source,symbol,start,end)).fetchall()
            n=4 if table=='funding' else 9
            assert [r[:n] for r in got]==[r[:n] for r in expected],(source,symbol,table)
        collect(c,source,asset,symbol)
        for table,n in counts.items():assert db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==n
        results.append(dict(source=source,symbol=symbol,status='PASS',restored={t:len(v) for t,v in original.items()},oi_hour_slots_expected=12,oi_hour_slots_available=len(original['oi_history']),duplicate_replay=True))
    db.close()
    report=dict(checked_at=dt.datetime.now(dt.timezone.utc).isoformat(),status='PASS' if results and all(x['status']=='PASS' for x in results) else 'PARTIAL',scope='Offline replay of existing history, not live API retention proof; original source gaps remain gaps; no instantaneous OI reconstruction',production_modified=False,results=results)
    (R/'reports/derivatives-twelve-hour-recovery.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({'status':report['status'],'series':len(results),'restored':{t:sum(x.get('restored',{}).get(t,0) for x in results) for t in ('funding','oi_history')},'incomplete_original_oi_windows':sum(x.get('oi_hour_slots_available',0)!=12 for x in results)}))
if __name__=='__main__':main()
