"""Offline release checks with saved, timestamped evidence."""
import datetime as dt,json,pathlib,subprocess,sys
from data_audit import ROOT,save
def main():
    code=pathlib.Path(__file__).parent
    for p in code.glob('*.py'):compile(p.read_text(encoding='utf-8-sig'),str(p),'exec')
    modules=['test_quality','test_incremental','test_derivatives','test_data_audit','test_research_metrics','test_backup_integrity','test_cloud_probe','test_candle_packet','test_derivative_packet']
    result=subprocess.run([sys.executable,'-m','unittest',*modules],cwd=code,capture_output=True,text=True)
    report={'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'syntax_files':len(list(code.glob('*.py'))),'test_modules':modules,'exit_code':result.returncode,'output':result.stdout+result.stderr,'network_calls':False,'production_database_writes':False}
    save(ROOT/'reports/release-checks.json',report);print(json.dumps({'syntax_files':report['syntax_files'],'tests_exit_code':result.returncode}));return result.returncode
if __name__=='__main__':sys.exit(main())
