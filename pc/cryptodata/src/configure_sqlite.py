"""Enable local WAL concurrency and prove reader/writer overlap on a fixture."""
import datetime as dt,pathlib,sqlite3,tempfile
from data_audit import ROOT,save
def main():
    db=sqlite3.connect(ROOT/'normalized/market.sqlite3',timeout=30)
    try:
        mode=db.execute('PRAGMA journal_mode=WAL').fetchone()[0]
        if mode!='wal':raise RuntimeError('WAL unavailable')
    finally:db.close()
    with tempfile.TemporaryDirectory() as directory:
        path=pathlib.Path(directory)/'test.sqlite3';writer=sqlite3.connect(path);writer.execute('PRAGMA journal_mode=WAL');writer.execute('CREATE TABLE t(x)');writer.execute('INSERT INTO t VALUES (1)');writer.commit();reader=sqlite3.connect(path);reader.execute('BEGIN');assert reader.execute('SELECT COUNT(*) FROM t').fetchone()[0]==1
        writer.execute('INSERT INTO t VALUES (2)');writer.commit();assert reader.execute('SELECT COUNT(*) FROM t').fetchone()[0]==1;reader.commit();assert reader.execute('SELECT COUNT(*) FROM t').fetchone()[0]==2;reader.close();writer.close()
    save(ROOT/'reports/sqlite-concurrency.json',{'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'journal_mode':mode,'fixture_reader_snapshot_isolated':True,'fixture_writer_commit_while_reader_open':True,'collector_busy_timeout_seconds':30,'backup_method':'SQLite backup API; do not copy live DB alone in WAL mode'})
    print('WAL enabled; isolated concurrent read/write checks passed')
if __name__=='__main__':main()
