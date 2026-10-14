"""Portable, bounded public-data emergency collector. No secrets or trading."""
import datetime as dt,json,os,pathlib,sys,zipfile
from collect_pilot import Collector,R
class Frozen(Collector):
    def allowed(self,source,asset,symbol):
        mapping=json.loads((R/'config/instrument-map.json').read_text())
        if mapping.get(source+'|'+asset)==symbol:return True
        self.coverage.append({'source':source,'asset':asset,'symbol':symbol,'dataset':'identity_guard','status':'error','error':'instrument not in frozen mapping'});return False
    def run_candles(self,source,asset,symbol,quote):
        if self.allowed(source,asset,symbol):super().run_candles(source,asset,symbol,quote)
    def derivatives(self,source,asset,symbol):
        if self.allowed(source,asset,symbol):super().derivatives(source,asset,symbol)
def main():
    for p in ('raw','reports','normalized','state'):(R/p).mkdir(parents=True,exist_ok=True)
    c=Frozen(1);c.run();report=json.loads((R/'reports'/(c.id+'-pilot.json')).read_text())
    report['execution_environment']='GitHub Actions emergency, independent source observations'
    (R/'reports/cloud-report.json').write_text(json.dumps(report,indent=2))
    target=R/'emergency.zip'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
        for folder in ('raw','normalized','reports'):
            for p in (R/folder).rglob('*'):
                if p.is_file():z.write(p,str(p.relative_to(R)))
    if target.stat().st_size>20_000_000:raise RuntimeError('20MB emergency artifact cap exceeded')
    summary={'run_id':c.id,'errors':report['errors'],'database_rows':report['database_rows'],'package_bytes':target.stat().st_size,'limitations':['not full replacement for PC or DPAPI Drive backup','region/IP blocking remains an error; no venue substitution','1-day candle/funding window and OI snapshots; daily/research history excluded','package persists only if optional artifact upload enabled']}
    (R/'cloud-summary.json').write_text(json.dumps(summary,indent=2))
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('## CryptoData emergency run\n```json\n'+json.dumps(summary,indent=2)+'\n```\n')
    print(json.dumps(summary));return 2 if report['errors'] else 0
if __name__=='__main__':sys.exit(main())
