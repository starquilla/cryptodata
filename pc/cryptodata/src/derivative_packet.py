"""Strict offline funding/OI-history importer. Trusted transport still required."""
import datetime as dt, hashlib, json
from decimal import Decimal
from candle_packet import MAX_BYTES, MAX_ROWS

def import_packet(db,raw,mapping,semantics,now_ms):
    if len(raw)>MAX_BYTES:raise ValueError('packet too large')
    envelope=json.loads(raw);p=envelope['payload']
    canonical=json.dumps(p,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    if hashlib.sha256(canonical).hexdigest()!=envelope['sha256']:raise ValueError('checksum mismatch')
    if set(p)!={'schema','dataset','rows'} or p['schema']!=1 or p['dataset'] not in ('funding','oi_history'):raise ValueError('unsupported schema')
    table=p['dataset'];rows=p['rows'];oi=table=='oi_history';size=11 if oi else 6;seen=set()
    if not isinstance(rows,list) or not 0<len(rows)<=MAX_ROWS:raise ValueError('row count')
    for r in rows:
        if not isinstance(r,list) or len(r)!=size:raise ValueError('row shape')
        if tuple(r[:2]) not in mapping:raise ValueError('unknown source instrument')
        t=r[3] if oi else r[2]
        if type(t)!=int or t<0 or t>now_ms:raise ValueError('invalid timestamp')
        key=tuple(r[:4] if oi else r[:3])
        if key in seen:raise ValueError('duplicate packet key')
        seen.add(key)
        n=Decimal(str(r[4] if oi else r[3]))
        if not n.is_finite() or oi and n<0:raise ValueError('invalid metric')
        if oi:
            if r[2]!='1h' or t%3600000:raise ValueError('invalid interval')
            if tuple(r[5:8]) not in semantics.get(tuple(r[:2]),set()):raise ValueError('unverified OI semantics')
            if r[8] is not None:
                q=Decimal(str(r[8]))
                if not q.is_finite() or q<0:raise ValueError('invalid quote OI')
        stamp=dt.datetime.fromisoformat(r[-2])
        if stamp.tzinfo is None or not isinstance(r[-1],str) or not r[-1]:raise ValueError('missing provenance')
    inserted=duplicates=0;db.execute('SAVEPOINT derivative_packet')
    try:
        for r in rows:
            clause='source=? AND symbol=? AND interval=? AND time=?' if oi else 'source=? AND symbol=? AND time=?'
            key=r[:4] if oi else r[:3]
            old=db.execute('SELECT * FROM '+table+' WHERE '+clause,key).fetchone()
            if old:
                if tuple(old[:-2])!=tuple(r[:-2]):raise ValueError('conflict requires review')
                duplicates+=1
            else:
                db.execute('INSERT INTO '+table+' VALUES ('+','.join('?'*size)+')',r);inserted+=1
        db.execute('RELEASE derivative_packet')
    except Exception:
        db.execute('ROLLBACK TO derivative_packet');db.execute('RELEASE derivative_packet');raise
    return {'inserted':inserted,'duplicates':duplicates}
