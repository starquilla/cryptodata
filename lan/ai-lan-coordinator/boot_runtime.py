"""Noninteractive bounded-read runtime. No reboot, credential provisioning or AI calls."""
import argparse
import ctypes
from ctypes import wintypes
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parent


def utc(): return dt.datetime.now(dt.timezone.utc).isoformat()
def read(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def windows_context():
    # Fixed local observation only; no user supplied command or arguments.
    script="[pscustomobject]@{boot=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o');sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;session=(Get-Process -Id $PID).SessionId}|ConvertTo-Json -Compress"
    r=subprocess.run([os.environ.get('SystemRoot','C:/Windows')+'/System32/WindowsPowerShell/v1.0/powershell.exe',
        '-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,check=True)
    return json.loads(r.stdout)


def interactive_users():
    class Session(ctypes.Structure):
        _fields_=[('id',wintypes.DWORD),('station',wintypes.LPWSTR),('state',ctypes.c_int)]
    api=ctypes.WinDLL('Wtsapi32',use_last_error=True)
    api.WTSEnumerateSessionsW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.DWORD,
                                      ctypes.POINTER(ctypes.POINTER(Session)),ctypes.POINTER(wintypes.DWORD)]
    api.WTSQuerySessionInformationW.argtypes=[wintypes.HANDLE,wintypes.DWORD,ctypes.c_int,
                                            ctypes.POINTER(wintypes.LPWSTR),ctypes.POINTER(wintypes.DWORD)]
    api.WTSFreeMemory.argtypes=[ctypes.c_void_p]
    rows=ctypes.POINTER(Session)(); count=wintypes.DWORD()
    if not api.WTSEnumerateSessionsW(None,0,1,ctypes.byref(rows),ctypes.byref(count)):
        return 9999 # Unknown is never interpreted as "nobody logged on".
    users=0
    try:
        for i in range(count.value):
            if rows[i].id==0: continue
            value=wintypes.LPWSTR(); size=wintypes.DWORD()
            if not api.WTSQuerySessionInformationW(None,rows[i].id,5,ctypes.byref(value),ctypes.byref(size)):
                return 9999
            try:
                if value.value: users+=1
            finally: api.WTSFreeMemory(value)
        return users
    finally: api.WTSFreeMemory(rows)


def log(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and path.stat().st_size>4_000_000:
        os.replace(path,path.with_suffix('.previous.jsonl'))
    with path.open('a',encoding='utf-8') as f:
        f.write(json.dumps(value,ensure_ascii=False)+'\n'); f.flush()


def check_guard(config):
    if config['host'].casefold()!=socket.gethostname().casefold():
        raise RuntimeError('deployment belongs to another host')
    for relative,expected in config['protected'].items():
        if sha(ROOT/relative)!=expected:
            raise RuntimeError('protected file changed: '+relative)
    policy=read(ROOT/'ai-policy.json'); registry=read(ROOT/'registry.json')
    if registry['tools']['ai_text']['enabled'] or any(x['enabled'] for x in policy['providers'].values()):
        raise RuntimeError('unattended runtime requires existing AI gates to stay disabled')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--role',choices=['worker','coordinator','controller','probe'],required=True)
    args=p.parse_args()
    config=read(ROOT/'private/boot-v1/install.json')
    Path(config['evidence']).mkdir(parents=True,exist_ok=True)
    sys.stdout=(Path(config['evidence'])/(args.role+'.stdout.log')).open('a',encoding='utf-8',buffering=1)
    sys.stderr=(Path(config['evidence'])/(args.role+'.stderr.log')).open('a',encoding='utf-8',buffering=1)
    journal=Path(config['evidence'])/(args.role+'.jsonl')
    context=windows_context()
    from coordinator import Client,Rejected,write_json
    from network import read_config
    check_guard(config)
    c=read_config(ROOT/'config.worker.json')
    identity=read(ROOT/'private'/(socket.gethostname()+'.identity.json'))
    client=Client(c['coordinator_url'],c['certificate_sha256'],identity,c)
    if args.role=='probe':
        result={'at':utc(),'context':context,'interactive_users':interactive_users(),
                'python':sys.executable,'host':socket.gethostname(),'guard_ok':True}
        try:
            client.post('/heartbeat',{'id':'boot-probe-not-a-real-job','fence':0})
            raise RuntimeError('unexpected probe acceptance')
        except Rejected as exc:
            if not str(exc).startswith('stale lease or owner'):
                raise
            result['authenticated_https_ok']=True
        write_json(Path(config['evidence'])/'probe.json',result)
        return
    from runtime_singleton import acquire_once,AlreadyActive
    try: acquire_once(ROOT,args.role)
    except AlreadyActive as exc:
        log(journal,{'at':utc(),'phase':'duplicate_blocked','message':str(exc)})
        return
    status={'hostname':socket.gethostname(),'role':args.role,'at':utc(),'boot_utc':context['boot'],
            'pid':os.getpid(),'session_id':context['session'],'sid':context['sid'],
            'interactive_users':interactive_users(),'phase':'started','offline_count':0}
    log(journal,status)
    write_json(Path(config['evidence'])/(args.role+'-start.json'),status)
    def observer():
        while True:
            try: check_guard(config)
            except Exception as exc:
                log(journal,{'at':utc(),'phase':'protected_guard_failed','error':str(exc)})
                os._exit(3)
            try:
                status.update(at=utc(),interactive_users=interactive_users())
                client.post('/runtime-status',dict(status))
                status['phase']='heartbeat'
            except Exception as exc:
                status['offline_count']+=1
                status['phase']='unavailable'
                log(journal,dict(status,error=type(exc).__name__))
            else: log(journal,status)
            if args.role=='controller':
                try: boot_acceptance(config,context)
                except Exception as exc: log(journal,{'at':utc(),'phase':'acceptance_error','error':str(exc)})
            time.sleep(10)
    threading.Thread(target=observer,daemon=True).start()
    # No arbitrary executables. All role implementations run in this process.
    if args.role=='controller':
        import auto_handoff
        sys.argv=['auto_handoff.py','watch']
        auto_handoff.main()
    else:
        import coordinator
        if args.role=='coordinator':
            sys.argv=['coordinator.py','--state',str(ROOT/'private/coordinator'),
                      'serve','--config',str(ROOT/'config.server.json')]
        else:
            sys.argv=['coordinator.py','worker','--config',str(ROOT/'config.worker.json'),
                      '--identity',str(ROOT/'private'/(socket.gethostname()+'.identity.json')),
                      '--outbox',str(ROOT/'private'/(socket.gethostname()+'.outbox')),'--watch']
        coordinator.main()


def boot_acceptance(config,context):
    arm=ROOT/'private/boot-v1/armed.json'
    if not arm.exists(): return
    request=read(arm)
    if request['armed_boot']==context['boot']: return
    from auto_handoff import HOME,prepare,load
    from coordinator import Store,write_json
    folder=HOME/request['session']
    if not folder.exists():
        paths=[Path(x) for x in request['inputs']]
        prepare(HOME,paths,seconds=3600,session=request['session'])
        write_json(Path(config['evidence'])/'boot-job-created.json',{
            'at':utc(),'boot_utc':context['boot'],'interactive_users':interactive_users(),
            'session':request['session']})
    state=folder/'state.json'
    if not state.exists() or load(state)['status']!='PASS': return
    dest=Path(config['evidence'])/(request['session']+'-observation.json')
    if dest.exists(): return
    snapshot=Store(ROOT/'private/coordinator/queue.sqlite').snapshot()
    ids=set(load(state)['job_ids'])|{load(state)['return_job']}
    events=[e for e in snapshot['events'] if e['kind']=='runtime_status']
    write_json(dest,{'at':utc(),'session':request['session'],'state':load(state),
        'jobs':[j for j in snapshot['jobs'] if j['id'] in ids],
        'runtime_events':events[-200:],'interactive_users_at_completion_capture':interactive_users(),
        'verdict':'OBSERVED_REQUIRE_POST_REBOOT_AUDIT'})


if __name__=='__main__':
    try: main()
    except Exception as exc:
        # Never print identity, tokens, headers or the manifest contents.
        print(type(exc).__name__+': '+str(exc),file=sys.stderr,flush=True)
        raise SystemExit(1)
