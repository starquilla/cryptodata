"""Durable, bounded file-integrity handoff using existing pinned HTTPS read workers.

No remote shell, model calls or production writes. PC2 hashes compressed evidence
blocks; PC1 verifies every receipt before accepting the return handoff.
"""
import argparse
import base64
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import socket
import time
import uuid

from coordinator import Store, canonical, write_json

ROOT = Path(__file__).resolve().parent
HOME = Path('E:/tai nguyen codex/AI-LAN-Coordinator/auto-handoff')
PC1 = 'DESKTOP-7VPAM29'
PC2 = 'LAPTOP-GNLJITR9'
CHUNK = 15000
MAX_INPUT = 16_000_000


def sha(data):
    return hashlib.sha256(data).hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def pack(paths):
    files, blocks, total = [], [], 0
    for p in paths:
        p = Path(p)
        if not p.is_file() or p.is_symlink() or p.stat().st_file_attributes & 0x400:
            raise ValueError('only regular non-reparse files allowed')
        if 'private' in [x.casefold() for x in p.parts] or p.name.casefold().endswith('.identity.json'):
            raise ValueError('private material cannot be submitted')
        total += p.stat().st_size
        if total > MAX_INPUT:
            raise ValueError('input exceeds 16 MB bounded session')
        data = p.read_bytes()
        encoded = base64.b64encode(gzip.compress(data, mtime=0)).decode('ascii')
        parts = [encoded[i:i+CHUNK] for i in range(0, len(encoded), CHUNK)]
        if gzip.decompress(base64.b64decode(''.join(parts))) != data:
            raise ValueError('packing roundtrip failed')
        indices = []
        for text in parts:
            indices.append(len(blocks))
            blocks.append({'text': text, 'expected': {'sha256': sha(text.encode()), 'bytes': len(text)}})
        files.append({'name': p.name, 'source': str(p.resolve()), 'bytes': len(data),
                      'sha256': sha(data), 'block_indices': indices})
    if not files or len({f['name'] for f in files}) != len(files):
        raise ValueError('nonempty uniquely named inputs required')
    return files, blocks


def prepare(home, paths, seconds=3600, session=None):
    if not 60 <= seconds <= 86400:
        raise ValueError('deadline must be 60..86400 seconds')
    files, blocks = pack(paths)
    session = session or ('oi-1642-handoff-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    if not session.replace('-', '').isalnum():
        raise ValueError('invalid session')
    folder = Path(home) / session
    folder.mkdir(parents=True, exist_ok=False)
    plan = {'version': 1, 'session': session, 'created': now(), 'deadline': time.time()+seconds,
            'pc1': PC1, 'pc2': PC2, 'files': files, 'blocks': blocks, 'status': 'PREPARED'}
    write_json(folder/'plan.json', plan)
    return folder


def verify_job(job, expected, owner):
    if job['state'] in ('failed', 'needs_review'):
        raise ValueError('terminal job failure: ' + job['id'])
    if job['state'] != 'succeeded':
        return False
    if job['owner'] != owner or json.loads(job['result']) != expected:
        raise ValueError('receipt owner/content mismatch: ' + job['id'])
    return True


def tick(store, folder):
    folder = Path(folder)
    plan = load(folder/'plan.json')
    state_path = folder/'state.json'
    state = load(state_path) if state_path.exists() else {'session': plan['session'], 'status': 'PREPARED'}
    if state['status'] in ('PASS', 'HOLD', 'TIMED_OUT'):
        return state
    if time.time() > plan['deadline']:
        with store.db() as db:
            for job in db.execute("SELECT id FROM jobs WHERE idem LIKE ? AND state='queued'", (plan['session']+':%',)).fetchall():
                db.execute("UPDATE jobs SET state='failed',reason='handoff_deadline_elapsed' WHERE id=?", (job['id'],))
                store.event(db, job['id'], 'handoff_deadline_elapsed', {'session':plan['session']})
            # A running pure read may finish, but lease loss must not start a
            # fresh attempt after this session has stopped accepting results.
            for job in db.execute("SELECT id FROM jobs WHERE idem LIKE ? AND state='running'", (plan['session']+':%',)).fetchall():
                db.execute('UPDATE jobs SET max_attempts=attempt WHERE id=?', (job['id'],))
                store.event(db, job['id'], 'handoff_no_further_attempts', {'session':plan['session']})
        state.update(status='TIMED_OUT', reason='acceptance deadline elapsed', updated=now())
        write_json(state_path, state)
        return state
    ids = []
    prefix = plan['session']
    try:
        # Deterministic idempotency keys recover safely even if the process died
        # after enqueue committed but before state.json was written.
        for i, block in enumerate(plan['blocks']):
            ids.append(store.enqueue(prefix+f':pc2:{i}', 'hash_text', {'text': block['text']},
                                     target=plan['pc2'], resource=prefix+':pc2',
                                     max_attempts=3, not_before=0))
        jobs = {j['id']: j for j in store.snapshot()['jobs']}
        done = sum(verify_job(jobs[jid], block['expected'], plan['pc2'])
                   for jid, block in zip(ids, plan['blocks']))
        state.update(status='WAITING_PC2', pc2_total=len(ids), pc2_verified=done,
                     job_ids=ids, updated=now())
        if done == len(ids):
            receipt = {'session': prefix, 'worker': plan['pc2'], 'verified_blocks': done,
                       'files': plan['files'], 'original_bytes': sum(f['bytes'] for f in plan['files']),
                       'scope': 'PC2 SHA256 of gzip-base64 blocks; no independent OI semantic review'}
            text = canonical(receipt)
            if len(text.encode()) > 16000:
                raise ValueError('return manifest exceeds worker input limit')
            jid = store.enqueue(prefix+':pc1:return', 'hash_text', {'text': text},
                                target=plan['pc1'], resource=prefix+':pc1', max_attempts=3)
            state.update(status='WAITING_PC1_RETURN', return_job=jid)
            jobs = {j['id']: j for j in store.snapshot()['jobs']}
            if verify_job(jobs[jid], {'sha256': sha(text.encode()), 'bytes': len(text.encode())}, plan['pc1']):
                state.update(status='PASS', completed=now(), receipt=receipt)
                write_json(folder/'receipt.json', receipt)
        # Stop late acceptance, including an offline worker returning after expiry.
        if time.time() > plan['deadline']:
            state.update(status='TIMED_OUT', reason='acceptance deadline elapsed')
    except (ValueError, KeyError) as exc:
        state.update(status='HOLD', reason=str(exc), updated=now(), job_ids=ids)
    write_json(state_path, state)
    snap = store.snapshot()
    selected = set(state.get('job_ids', [])) | {state.get('return_job')}
    write_json(folder/'queue-evidence.json', {
        'checked_at': now(), 'jobs': [j for j in snap['jobs'] if j['id'] in selected],
        'events': [e for e in snap['events'] if e['job'] in selected],
        'workers': snap['workers']})
    return state


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--home', type=Path, default=HOME)
    ap.add_argument('--queue', type=Path, default=ROOT/'private/coordinator/queue.sqlite')
    sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('submit')
    p.add_argument('files', nargs='+', type=Path)
    p.add_argument('--seconds', type=int, default=3600)
    sub.add_parser('watch')
    sub.add_parser('once')
    args = ap.parse_args()
    if socket.gethostname().casefold() != PC1.casefold():
        raise SystemExit('controller runs on the existing PC1 coordinator only')
    args.home.mkdir(parents=True, exist_ok=True)
    if args.command == 'submit':
        folder = prepare(args.home, args.files, args.seconds)
        print(folder, flush=True)
        return
    from runtime_singleton import acquire_once
    acquire_once(ROOT, 'controller')
    store = Store(args.queue)
    # A persistent OS lock is released automatically after a crash/reboot.
    lock = (args.home/'controller.lock').open('a+b')
    lock.seek(0)
    if not lock.read(1):
        lock.write(b'0')
        lock.flush()
    lock.seek(0)
    import msvcrt
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise SystemExit('Another handoff controller already owns this inbox')
    last = {}
    while True:
        for path in sorted(args.home.glob('*/plan.json')):
            try:
                state = tick(store, path.parent)
                summary = {k: state[k] for k in ('session','status','pc2_verified','pc2_total','reason') if k in state}
                if last.get(state['session']) != summary:
                    print(canonical(summary), flush=True)
                    last[state['session']] = summary
            except Exception as exc:
                write_json(path.parent/'controller-error.json', {'at':now(),'error':str(exc)})
                print('Controller error; evidence retained: '+str(exc), flush=True)
        if args.command == 'once':
            break
        time.sleep(2)


if __name__ == '__main__':
    main()
