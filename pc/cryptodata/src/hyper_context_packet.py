"""Experimental raw-context importer for an isolated database; no network or production integration."""
import datetime as dt
import hashlib
import json
import math
import re

SYMBOLS={'BTC','ETH','ATOM','SOL','AVAX','BNB','OP','LTC','ARB','DOGE','SUI','CRV','LDO','LINK','XRP','BCH','APT','AAVE','TRX','UNI','DOT','ADA','PENDLE','NEAR','ENA','HYPE','MORPHO'}


def reject_constant(value):
    raise ValueError('nonfinite JSON')


def strict_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate JSON key')
        result[key]=value
    return result


def decode(raw,now):
    if len(raw)>100000:raise ValueError('packet too large')
    kwargs={'parse_constant':reject_constant,'object_pairs_hook':strict_object}
    e=json.loads(raw,**kwargs)
    if set(e)!={'schema','payload_text','sha256'} or type(e['schema']) is not int or e['schema']!=1:raise ValueError('envelope schema')
    if not isinstance(e['payload_text'],str) or not isinstance(e['sha256'],str):raise ValueError('envelope type')
    if hashlib.sha256(e['payload_text'].encode('utf8')).hexdigest()!=e['sha256']:raise ValueError('checksum mismatch')
    p=json.loads(e['payload_text'],**kwargs)
    expected={'schema','dataset','packet_id','source','observed_at','provider_timestamp','unit_status','rows'}
    if set(p)!=expected or type(p['schema']) is not int or p['schema']!=1 or p['dataset']!='hyperliquid_context_raw' or p['source']!='hyperliquid_perpetual':raise ValueError('payload schema/source')
    if not isinstance(p['packet_id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',p['packet_id']):raise ValueError('packet ID')
    stamp=dt.datetime.fromisoformat(p['observed_at'].replace('Z','+00:00'))
    if stamp.tzinfo is None or not 0<stamp.timestamp()<=now.timestamp()+5:raise ValueError('observation timestamp')
    if p['provider_timestamp'] is not None or p['unit_status']!='vendor fields retained; semantic acceptance pending':raise ValueError('unsupported timestamp/unit claim')
    if not isinstance(p['rows'],list) or len(p['rows'])!=len(SYMBOLS):raise ValueError('row count')
    seen=set()
    for row in p['rows']:
        if set(row)!={'symbol','context'} or row['symbol'] not in SYMBOLS or row['symbol'] in seen:raise ValueError('mapping/duplicate symbol')
        seen.add(row['symbol']);c=row['context']
        if not isinstance(c,dict):raise ValueError('context shape')
        for key in ('markPx','openInterest','funding'):
            v=c.get(key)
            if v is None or isinstance(v,bool) or v=='' or not math.isfinite(float(v)):raise ValueError('numeric field')
        if float(c['markPx'])<=0 or float(c['openInterest'])<0:raise ValueError('numeric range')
    return p,e['sha256']


def initialize_sandbox(db):
    db.execute('CREATE TABLE IF NOT EXISTS pilot_packets(packet_id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,observed_at TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS pilot_hyper_context(packet_id TEXT NOT NULL,symbol TEXT NOT NULL,context_json TEXT NOT NULL,PRIMARY KEY(packet_id,symbol))')


def import_packet(db,raw,now):
    p,sha=decode(raw,now)
    # Transport authentication is an external requirement. A checksum alone is not authentication.
    old=db.execute('SELECT sha256 FROM pilot_packets WHERE packet_id=?',(p['packet_id'],)).fetchone()
    if old:
        if old[0]!=sha:raise ValueError('packet ID conflict')
        return {'inserted':0,'duplicates':len(p['rows'])}
    db.execute('SAVEPOINT hyper_packet')
    try:
        db.execute('INSERT INTO pilot_packets VALUES(?,?,?)',(p['packet_id'],sha,p['observed_at']))
        for row in p['rows']:
            db.execute('INSERT INTO pilot_hyper_context VALUES(?,?,?)',(p['packet_id'],row['symbol'],json.dumps(row['context'],sort_keys=True,separators=(',',':'),allow_nan=False)))
        db.execute('RELEASE hyper_packet')
    except Exception:
        db.execute('ROLLBACK TO hyper_packet');db.execute('RELEASE hyper_packet');raise
    return {'inserted':len(p['rows']),'duplicates':0}
