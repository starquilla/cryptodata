"""Explicit, expiring local test batches; never grant or renew from a model call."""
import json
import secrets
import time
from ai_runtime import AIGateError

def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS pilot_budget_batches(
        id TEXT PRIMARY KEY,provider TEXT NOT NULL,issued REAL NOT NULL,expires REAL NOT NULL,
        max_new_calls INTEGER NOT NULL,baseline_ids TEXT NOT NULL,authority TEXT NOT NULL)''')

def create_batch(db,now=None,max_new_calls=3,duration_seconds=7200):
    now=time.time() if now is None else now
    if (isinstance(max_new_calls,bool) or not isinstance(max_new_calls,int) or not 1<=max_new_calls<=3
            or isinstance(duration_seconds,bool) or not 0<duration_seconds<=7200):
        raise AIGateError('Manual test batch must be at most 3 calls and 2 hours')
    ensure_schema(db)
    db.execute('BEGIN IMMEDIATE')
    try:
        if db.execute("SELECT 1 FROM pilot_budget_batches WHERE provider='antigravity' AND expires>?",(now,)).fetchone():
            raise AIGateError('An unexpired batch already exists; do not renew or reset its allowance')
        if db.execute("SELECT 1 FROM runs WHERE provider='antigravity' AND state IN ('running','needs_review')").fetchone():
            raise AIGateError('Earlier Antigravity work is unresolved; review it before granting budget')
        baseline=[row[0] for row in db.execute("SELECT id FROM runs WHERE provider='antigravity' ORDER BY id")]
        batch={'id':'manual-'+secrets.token_hex(8),'provider':'antigravity','issued':now,'expires':now+duration_seconds,
               'max_new_calls':max_new_calls,'baseline_ids':baseline,'authority':'user-authorized-manual-test'}
        db.execute('INSERT INTO pilot_budget_batches VALUES(?,?,?,?,?,?,?)',
                   (batch['id'],batch['provider'],now,batch['expires'],max_new_calls,json.dumps(baseline),batch['authority']))
        db.commit()
        return batch
    except BaseException:
        db.rollback(); raise

def check_batch(db,provider,now=None):
    now=time.time() if now is None else now
    rows=db.execute('SELECT id,issued,expires,max_new_calls,baseline_ids,authority FROM pilot_budget_batches WHERE provider=? AND issued<=? AND expires>?',
                    (provider,now,now)).fetchall()
    if len(rows)!=1: raise AIGateError('A single unexpired manual test batch is required; no automatic renewal')
    batch_id,issued,expires,maximum,raw_baseline,authority=rows[0]
    if authority!='user-authorized-manual-test' or not 0<expires-issued<=7200 or not 1<=maximum<=3:
        raise AIGateError('Invalid manual batch limits; operator review required')
    baseline=json.loads(raw_baseline)
    if not isinstance(baseline,list) or any(not isinstance(x,str) for x in baseline) or len(set(baseline))!=len(baseline):
        raise AIGateError('Invalid batch baseline')
    calls={row[0]:row[1] for row in db.execute('SELECT id,created FROM runs WHERE provider=?',(provider,))}
    if not set(baseline)<=set(calls) or any(calls[x]>issued for x in baseline):
        raise AIGateError('Batch baseline no longer matches prior receipts')
    used=len(set(calls)-set(baseline))
    if used>=maximum: raise AIGateError('Manual batch exhausted; no retry, new batch or budget increase automatically')
    return {'batch_id':batch_id,'new_calls_used':used,'new_calls_limit':maximum,'expires':expires}
