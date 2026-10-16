"""Validate small, authenticated operational status. Never execute its contents."""
import json


def validate_status(worker,value):
    fields={'hostname','role','at','boot_utc','pid','session_id','interactive_users','sid','phase','offline_count'}
    if not isinstance(value,dict) or set(value)!=fields or len(json.dumps(value))>4096:
        raise ValueError('invalid runtime status fields')
    if value['hostname'].casefold()!=worker.casefold() or value['role'] not in ('worker','coordinator','controller'):
        raise ValueError('runtime host or role mismatch')
    for k in ('at','boot_utc','sid','phase'):
        if not isinstance(value[k],str) or not 1<=len(value[k])<=100:
            raise ValueError('invalid runtime status string')
    for k in ('pid','session_id','interactive_users','offline_count'):
        if type(value[k]) is not int or not 0<=value[k]<2**32:
            raise ValueError('invalid runtime status number')
    if value['role']!='worker' and worker.casefold()!='desktop-7vpam29':
        raise ValueError('PC2 cannot report a PC1-only role')
    return value
