"""Independent local monitor. Records transitions and requests local notification."""
import datetime as dt,json,pathlib,sys
from data_audit import ROOT,audit,load,save
def main():
    try:health,_=audit();signature='|'.join(sorted(health['issues']));status=health['status']
    except Exception as e:signature='audit_failed:'+type(e).__name__;status='warning';health={'issues':[signature]}
    old=load(ROOT/'state/monitor.json',{});changed=old.get('signature')!=signature
    event={'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'status':status,'signature':signature,'changed':changed,'issues':health.get('issues',[]),'notification_channel':'local Windows balloon; delivery not acknowledged; off-PC not covered'}
    save(ROOT/'state/monitor.json',event)
    if changed:
        log=ROOT/'reports/health-events.jsonl'
        if log.exists() and log.stat().st_size>1_000_000:log.replace(ROOT/'reports/health-events-previous.jsonl')
        with log.open('a',encoding='utf8') as f:f.write(json.dumps(event,ensure_ascii=False)+'\n')
    print(json.dumps({'status':status,'changed':changed,'issues_count':len(event['issues'])}))
    return 10 if changed and status!='ok' else 11 if changed and old and status=='ok' else 0
if __name__=='__main__':sys.exit(main())
