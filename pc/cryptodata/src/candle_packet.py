"""Offline candle transfer boundary. No network, credentials or automatic live import.

SHA256 detects corruption, not authenticity. Only accept packets from a trusted
transport. Mapping must be a separately verified list of venue instruments.
"""
import hashlib, json, datetime as dt
from decimal import Decimal

MAX_BYTES=16_000_000
MAX_ROWS=10000
def encode(payload):
    raw=json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    return json.dumps({'sha256':hashlib.sha256(raw).hexdigest(),'payload':payload},sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def decode(raw,mapping,now_ms):
    if len(raw)>MAX_BYTES:raise ValueError('packet too large')
    packet=json.loads(raw)
    payload=packet['payload']
    canonical=json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    if hashlib.sha256(canonical).hexdigest()!=packet['sha256']:raise ValueError('checksum mismatch')
    if set(payload)!= {'schema','dataset','rows'} or payload['schema']!=1 or payload['dataset']!='candles_5m':raise ValueError('unsupported schema')
    rows=payload['rows']
    if not isinstance(rows,list) or not 0<len(rows)<=MAX_ROWS:raise ValueError('invalid row count')
    seen=set()
    for r in rows:
        if not isinstance(r,list) or len(r)!=14:raise ValueError('invalid row shape')
        source,market,symbol,base,quote,interval,t,*_=r
        if (source,market,symbol,base,quote,interval) not in mapping:raise ValueError('unverified instrument')
        if type(t)!=int or t%300000 or t+300000>now_ms:raise ValueError('not a closed aligned candle')
        key=(source,market,symbol,interval,t)
        if key in seen:raise ValueError('duplicate key in packet')
        seen.add(key)
        nums=[Decimal(str(v)) for v in r[7:12]]
        if any(not n.is_finite() for n in nums):raise ValueError('nonfinite metric')
        o,h,l,c,v=nums
        if min(o,h,l,c)<=0 or v<0 or l>min(o,c) or h<max(o,c) or l>h:raise ValueError('invalid OHLCV')
        stamp=dt.datetime.fromisoformat(r[12])
        if stamp.tzinfo is None or not isinstance(r[13],str) or not r[13]:raise ValueError('missing provenance')
    return rows

def import_packet(db,raw,mapping,now_ms):
    rows=decode(raw,mapping,now_ms)
    inserted=duplicates=0
    # One transaction: any conflict rolls back every new row in this packet.
    db.execute('SAVEPOINT candle_packet')
    try:
        for r in rows:
            old=db.execute('SELECT * FROM candles WHERE source=? AND market=? AND symbol=? AND interval=? AND open_time=?',(r[0],r[1],r[2],r[5],r[6])).fetchone()
            if old is not None:
                if tuple(old[:12])!=tuple(r[:12]):raise ValueError('conflicting candle; manual review required')
                duplicates+=1
            else:
                db.execute('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',r);inserted+=1
        db.execute('RELEASE candle_packet')
    except Exception:
        db.execute('ROLLBACK TO candle_packet');db.execute('RELEASE candle_packet');raise
    return {'inserted':inserted,'duplicates':duplicates}
