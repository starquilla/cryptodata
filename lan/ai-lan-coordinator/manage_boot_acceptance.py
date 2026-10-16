"""Arm a post-boot real OI job and audit observed results. NEVER reboots a PC."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import socket
import time

from coordinator import ROOT,Store,write_json
from boot_runtime import windows_context,utc

EVIDENCE=Path('E:/tai nguyen codex/AI-LAN-Coordinator/boot-v1')
AUTO=Path('E:/tai nguyen codex/AI-LAN-Coordinator/auto-handoff')
INPUT=Path('E:/tai nguyen codex/CryptoData-OI-v12/live-1642-20261016')
PC1='DESKTOP-7VPAM29';PC2='LAPTOP-GNLJITR9'


def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def instant(s):return dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()


def statuses(snapshot):
    values=[]
    for event in snapshot['events']:
        if event['kind']=='runtime_status':
            detail=json.loads(event['detail'])
            values.append(dict(detail['status'],server_at=event['at'],peer=detail['peer']))
    return values


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest='action',required=True)
    p=sub.add_parser('arm');p.add_argument('--case',choices=['normal','pc2-first'],required=True);p.add_argument('--retry-held',action='store_true')
    p=sub.add_parser('audit');p.add_argument('--session',required=True)
    sub.add_parser('status')
    args=ap.parse_args()
    assert socket.gethostname()==PC1
    store=Store(ROOT/'private/coordinator/queue.sqlite')
    snapshot=store.snapshot();observed=statuses(snapshot)
    context=windows_context()
    baseline=read(EVIDENCE/'baseline.json')
    protected=[dict(x,current=hashlib.sha256((ROOT/x['name']).read_bytes()).hexdigest()) for x in baseline['protected']]
    assert all(x['sha256']==x['current'] for x in protected),'identity/TLS/policy/config changed'
    if args.action=='status':
        latest={}
        for s in observed:latest[s['hostname']+':'+s['role']]=s
        print(json.dumps({'at':utc(),'latest':latest,'protected_unchanged':True},indent=2))
        return
    if args.action=='arm':
        assert not any(j['state'] in ('queued','running','needs_review') for j in snapshot['jobs']),'queue not drained'
        latest={}
        for s in observed:
            if time.time()-s['server_at']<60:latest[(s['hostname'],s['role'])]=s
        required=[(PC1,r) for r in ('coordinator','controller','worker')]+[(PC2,'worker')]
        assert all(k in latest and latest[k]['session_id']==0 for k in required),'both PCs must have fresh Session 0 telemetry before arming'
        arm=ROOT/'private/boot-v1/armed.json'
        if arm.exists():
            previous=read(arm)
            result=EVIDENCE/(previous['session']+'-audit.json')
            assert result.exists() and (read(result)['verdict']=='PASS' or (args.retry_held and read(result)['verdict']=='HOLD')),'previous armed round requires an explicit HOLD retry'
            write_json(EVIDENCE/(previous['session']+'-armed.json'),previous)
        session='boot-oi-'+args.case+'-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S')
        inputs=[INPUT/x for x in ('oi-db-window-rows.jsonl','LIVE-1642-AUDIT.json','source-manifest.json','health.json')]
        request={'session':session,'case':args.case,'at':utc(),'armed_boot':context['boot'],
                 'pc2_prior_boot':latest[(PC2,'worker')]['boot_utc'],
                 'inputs':[str(p) for p in inputs],
                 'input_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
                 'protected':protected,'no_reboot_requested':True}
        write_json(arm,request);write_json(EVIDENCE/(session+'-armed.json'),request)
        print(json.dumps(request,indent=2));return
    request=read(EVIDENCE/(args.session+'-armed.json'))
    folder=AUTO/args.session
    if not (folder/'state.json').exists():
        write_json(EVIDENCE/(args.session+'-audit.json'),{'verdict':'NOT_RUN','at':utc(),'session':args.session,'reason':'no post-boot task state yet'})
        print('NOT_RUN');return
    state=read(folder/'state.json')
    ids=set(state.get('job_ids',[]))|{state.get('return_job')}
    jobs=[j for j in snapshot['jobs'] if j['id'] in ids]
    checks={}
    checks['job_pass']=state['status']=='PASS' and len(jobs)==20 and all(j['state']=='succeeded' for j in jobs)
    checks['owner_correct']=all(j['owner']==j['target'] for j in jobs)
    plan=read(folder/'plan.json')
    checks['input_hashes_match_armed_real_oi_evidence']={f['name']:f['sha256'] for f in plan['files']}==request['input_hashes']
    claimed=[e['at'] for e in snapshot['events'] if e['job'] in ids and e['kind']=='claimed']
    completed=[e['at'] for e in snapshot['events'] if e['job'] in ids and e['kind']=='completed']
    t0=min(claimed) if claimed else time.time();t1=max(completed) if completed else time.time()
    # Judge the boot(s) that actually processed this job, not whichever boot
    # happens to be current when the operator returns to read the report.
    current=[s for s in observed if s['hostname']==PC1 and t0-15<=s['server_at']<=t1+15]
    work_boots={s['boot_utc'] for s in current}
    checks['pc1_rebooted']=bool(work_boots) and request['armed_boot'] not in work_boots
    starts=[]
    for p in EVIDENCE.glob('*.jsonl'):
        for line in p.read_text(encoding='utf-8').splitlines():
            try: s=json.loads(line)
            except ValueError: continue
            if s.get('phase')=='started' and s.get('hostname')==PC1 and s.get('boot_utc') in work_boots:starts.append(s)
    # Small test fixtures and older deployments may only have start snapshots.
    if not starts:
        starts=[read(EVIDENCE/(r+'-start.json')) for r in ('worker','controller','coordinator')]
    starts=[s for s in starts if s['boot_utc'] in work_boots]
    checks['pc1_before_login']=all(any(s['role']==r and s['boot_utc']==b and s['session_id']==0 and s['interactive_users']==0 for s in starts) for b in work_boots for r in ('worker','controller','coordinator')) and bool(work_boots)
    in_window=[s for s in current if t0<=s['server_at']<=t1]
    checks['pc1_zero_users_during_work']=bool(in_window) and all(s['interactive_users']==0 for s in in_window)
    # Keep old-boot telemetry visible. A missing new boot must not erase valid
    # evidence of zero logged-on users, singleton execution or offline recovery.
    remote=[s for s in observed if s['hostname']==PC2 and s['role']=='worker' and t0-30<=s['server_at']<=t1+15]
    checks['pc2_boot_changed']=bool(remote) and all(s['boot_utc']!=request['pc2_prior_boot'] for s in remote)
    checks['pc2_zero_users_during_work']=bool(remote) and all(s['session_id']==0 and s['interactive_users']==0 for s in remote)
    checks['pc2_rebooted_and_before_login']=checks['pc2_boot_changed'] and checks['pc2_zero_users_during_work']
    groups={(PC1,r):set() for r in ('worker','coordinator','controller')}
    groups[(PC2,'worker')]=set()
    for s in current+remote:
        if (s['hostname'],s['role']) in groups:groups[(s['hostname'],s['role'])].add(s['pid'])
    checks['one_observed_runtime_pid_per_role']=all(len(v)==1 for v in groups.values())
    checks['runtime_same_original_sid']=all(s['sid']==baseline_sid for s in current for baseline_sid in [read(ROOT/'private/boot-v1/install.json')['principal_sid']])
    checks['critical_hashes_unchanged']=True
    if request['case']=='pc2-first':
        checks['pc2_booted_first']=checks['pc2_boot_changed'] and all(instant(b)-instant(s['boot_utc'])>=20 for b in work_boots for s in remote)
        checks['pc2_waited_offline_then_recovered']=any(s['offline_count']>0 for s in remote)
    results={'verdict':'PASS' if all(checks.values()) else 'HOLD','at':utc(),'session':args.session,
             'case':request['case'],'checks':checks,'state':state,'jobs':jobs,
             'runtime_status':current+remote,'protected':protected,
             'limits':['PC2 boot/SID/session evidence is authenticated worker telemetry, not a hardware attestation',
                       'No AI model invocation is needed for this OI evidence integrity job']}
    write_json(EVIDENCE/(args.session+'-audit.json'),results)
    print(json.dumps({'verdict':results['verdict'],'checks':checks},indent=2))


if __name__=='__main__':main()
