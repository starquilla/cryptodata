"""Incremental research collector; no trading. Windows OS file lock releases on crash."""
import datetime as dt, gzip, json, pathlib, sqlite3, sys, time, traceback, msvcrt
from collect_pilot import Collector, R, STEP, utc

def candle_start(db,source,symbol,end,bootstrap):
    times=[x[0] for x in db.execute('SELECT open_time FROM candles WHERE source=? AND symbol=? AND interval=? ORDER BY open_time',(source,symbol,'5m'))]
    if not times:return bootstrap
    # Scan internal history gaps as well as the tail; never advance past an unresolved gap.
    for a,b in zip(times,times[1:]):
        if b-a>STEP:return a+STEP
    return max(times[0],times[-1]-STEP)

def snapshot_backup():
    folder=R/'backups';folder.mkdir(exist_ok=True)
    day=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d')
    target=folder/('market-'+day+'.sqlite3.gz')
    if target.exists():return
    tmp=folder/('market-'+day+'.tmp.sqlite3')
    src=sqlite3.connect(R/'normalized/market.sqlite3');dst=sqlite3.connect(tmp)
    try:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('backup integrity failed')
    finally:dst.close();src.close()
    with tmp.open('rb') as f,gzip.open(str(target)+'.tmp','wb') as out:
        import shutil;shutil.copyfileobj(f,out)
    pathlib.Path(str(target)+'.tmp').replace(target);tmp.unlink()
    # Only exact application-owned backups in the fixed backup directory.
    for old in sorted(folder.glob('market-????????.sqlite3.gz'))[:-2]:old.unlink()

class Incremental(Collector):
    def __init__(self):
        super().__init__(7)
        self.mapping=json.loads((R/'config/instrument-map.json').read_text())
    def allowed(self,source,asset,symbol):
        if self.mapping.get(source+'|'+asset)!=symbol:
            self.coverage.append({'source':source,'asset':asset,'symbol':symbol,'status':'error','dataset':'identity_guard','error':'New or changed instrument requires mapping review'})
            return False
        return True
    def run_candles(self,source,asset,symbol,quote):
        if not self.allowed(source,asset,symbol):return
        original=self.start
        self.start=candle_start(self.db,source,symbol,self.end,original)
        # Bound catch-up work; later runs continue from the earliest unresolved gap.
        original_end=self.end
        self.end=min(self.end,self.start+7*86400000)
        start=self.start;end=self.end
        try:
            super().run_candles(source,asset,symbol,quote)
            self.coverage[-1].update(window_start_ms=start,window_end_ms=end,identity='frozen_venue_instrument; onchain_identity_pending',catchup_remaining=end<original_end)
        finally:self.start=original;self.end=original_end
    def derivatives(self,source,asset,symbol):
        if not self.allowed(source,asset,symbol):return
        original=self.start
        latest=self.db.execute('SELECT MAX(time) FROM funding WHERE source=? AND symbol=?',(source,symbol)).fetchone()[0]
        if latest is not None:self.start=max(original,latest-86400000)
        start_index=len(self.coverage)
        try:
            super().derivatives(source,asset,symbol)
            for x in self.coverage[start_index:]:
                x['identity']='frozen_venue_instrument; onchain_identity_pending'
                if x.get('possibly_truncated'):x.update(status='partial',error='funding pagination required')
                if latest is not None and latest<original:x['older_funding_gap_possible']=True
        finally:self.start=original
    def run(self):
        super().run()
        path=R/'reports'/(self.id+'-pilot.json');report=json.loads(path.read_text())
        seen={x.get('source','')+'|'+x.get('asset','') for x in report['coverage'] if x.get('symbol') and x.get('dataset')!='identity_guard'}
        for key in self.mapping:
            if key not in seen:
                report['coverage'].append({'source_asset':key,'status':'error','dataset':'mapping_guard','error':'Previously tracked instrument missing from current usable catalog'})
        bad=[x for x in report['coverage'] if x.get('status') in ('error','partial') or x.get('catchup_remaining') or x.get('older_funding_gap_possible')]
        report['mode']='incremental'
        report['health']='degraded' if bad else 'ok'
        report['limitations']=['Venue mappings frozen; onchain identities pending, research only','OI latest snapshots only; old outages cannot be reconstructed by this collector','Funding catches up within 7 days; older gaps explicitly flagged','External backup handled by separate verified daily Drive job; trading disabled']
        out=R/'reports'/(self.id+'-incremental.json');out.write_text(json.dumps(report,indent=2),encoding='utf8');path.unlink()
        status={'run_id':self.id,'finished_at':utc(),'health':report['health'],'next_expected_within_hours':2.5,'report':str(out),'requests':report['request_attempts'],'seconds':report['seconds'],'rows':report['database_rows']}
        tmp=R/'state/latest.json.tmp';tmp.write_text(json.dumps(status,indent=2),encoding='utf8');tmp.replace(R/'state/latest.json')
        snapshot_backup()
        print(json.dumps(status),flush=True)
        return 2 if bad else 0

def main():
    lock=(R/'state/incremental.lock').open('a+b')
    lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:lock.close();return 3
    try:return Incremental().run()
    except Exception:
        (R/'reports'/('failure-'+str(time.time_ns())+'.txt')).write_text(traceback.format_exc(),encoding='utf8');raise
    finally:
        lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1);lock.close()
if __name__=='__main__':sys.exit(main())

