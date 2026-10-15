"""Single explicit probe with retained cooldown, exclusive lease and no automatic retry.

Durability is limited to the supplied filesystem. Use a persistent VM volume;
Render Free's ephemeral filesystem does not become durable through this module.
"""
import argparse
import contextlib
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import uuid
from cloud_rate_guard import RateGuard


def atomic_json(path, value):
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(value, indent=2), encoding='utf8')
    os.replace(temp, path)


@contextlib.contextmanager
def lease(root, run_id):
    root = pathlib.Path(root)
    if not root.is_absolute() or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',run_id):
        raise ValueError('absolute state directory and safe run ID required')
    root.mkdir(parents=True, exist_ok=True)
    lock = root/'probe.lock'
    owner = uuid.uuid4().hex
    # A leftover lock requires operator investigation, never automatic takeover.
    with lock.open('x', encoding='utf8') as f:
        json.dump({'owner':owner,'pid':os.getpid(),'run_id':run_id,'started':time.time()},f)
    receipt = root/'runs'/run_id/'receipt.json'
    try:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        with receipt.open('x',encoding='utf8') as f:
            json.dump({'run_id':run_id,'status':'RUNNING','started':time.time()},f)
        yield receipt
    finally:
        if json.loads(lock.read_text('utf8'))['owner'] == owner:
            lock.unlink()


def execute_probe(script, state_root, run_id, *, timeout=1500, environ=None):
    script = pathlib.Path(script).resolve()
    if script.name not in ('probe_cloud_sources.py','probe_asset_coverage.py'):
        raise ValueError('only reviewed probe entrypoints are supported')
    env = dict(os.environ if environ is None else environ)
    root = pathlib.Path(state_root)
    with lease(root,run_id) as receipt:
        work = receipt.parent
        child = None
        try:
            state_path = root/'rate-cooldowns.json'
            seed = json.loads(env.get('PROBE_COOLDOWNS_JSON','{}'))
            guard = RateGuard(state_path, seed)
            atomic_json(state_path, guard.state)
            env['PROBE_STATE_PATH'] = str(state_path)
            log_path = work/'probe.log'
            with log_path.open('w',encoding='utf8') as log:
                child = subprocess.Popen([sys.executable,str(script)],cwd=work,
                                         env=env,stdout=log,stderr=subprocess.STDOUT)
                try:
                    code = child.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=30)
                    raise TimeoutError('probe deadline exceeded; child stopped')
            if log_path.stat().st_size > 16_000_000:
                raise ValueError('probe log exceeded16MB')
            reports = [line.split('=',1)[1] for line in log_path.read_text('utf8').splitlines()
                       if line.startswith('CRYPTODATA_PROBE_REPORT=')]
            if len(reports) != 1:
                raise ValueError('one final report required')
            report = json.loads(reports[0])
            if code != 0 and report.get('status') == 'PASS':
                raise ValueError('PASS report conflicts with process exit code')
            report.update(exit_code=code,run_id=run_id,
                          cooldown_persistence='supplied filesystem only; provider durability must be verified')
            atomic_json(work/'report.json',report)
            atomic_json(receipt,{'run_id':run_id,'status':'FINISHED','finished':time.time(),
                                 'exit_code':code,'probe_status':report.get('status','UNKNOWN')})
            return report
        except Exception as exc:
            if child is not None and child.poll() is None:
                child.kill()
                child.wait(timeout=30)
            atomic_json(receipt,{'run_id':run_id,'status':'FAILED','finished':time.time(),
                                 'error_type':type(exc).__name__})
            raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',required=True)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--sources',nargs='+',required=True)
    parser.add_argument('--mode',choices=('sample','coverage'),default='sample')
    parser.add_argument('--rounds',choices=(1,3),type=int,default=1)
    parser.add_argument('--not-before',required=True,help='Timezone-aware ISO8601 timestamp')
    parser.add_argument('--deadline',required=True,help='Latest allowed completion timestamp')
    args=parser.parse_args(argv)
    begin=dt.datetime.fromisoformat(args.not_before)
    end=dt.datetime.fromisoformat(args.deadline)
    if begin.tzinfo is None or end.tzinfo is None:
        raise ValueError('timezone-aware bounds required')
    now=dt.datetime.now(dt.timezone.utc)
    budget=(end-now).total_seconds()
    if now<begin or budget<30:
        raise ValueError('outside explicit execution window')
    from probe_cloud_sources import configured_sources
    env=dict(os.environ)
    env['PROBE_SOURCES_JSON']=json.dumps(args.sources)
    configured_sources(env)
    env['PROBE_ROUNDS']=str(args.rounds)
    name='probe_cloud_sources.py' if args.mode=='sample' else 'probe_asset_coverage.py'
    report=execute_probe(pathlib.Path(__file__).with_name(name),args.state_dir,args.run_id,
                         timeout=min(1500,budget-5),environ=env)
    print(json.dumps({'run_id':args.run_id,'status':report.get('status'),
                      'exit_code':report['exit_code']}))
    return 0 if report.get('status')=='PASS' else 2


if __name__=='__main__':
    raise SystemExit(main())
