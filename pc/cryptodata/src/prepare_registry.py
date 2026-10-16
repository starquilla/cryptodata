import json, pathlib, urllib.request
r=pathlib.Path(r'E:\CryptoData tai nguyen')
u='https://api.exchange.coinbase.com/currencies'
try:
    with urllib.request.urlopen(u,timeout=25) as f:d=json.load(f)
except Exception as e:
    print('Identity endpoint unavailable: '+str(e)); d=[]
a=json.loads((r/'config/settings.json').read_text(encoding='utf-8-sig'))['assets']
registry=[]
for asset in a:
    matches=[x for x in d if x['id'].upper()==asset.upper()]
    registry.append({'asset':asset,'name':matches[0].get('name') if matches else None,'identity_status':'exchange_currency_name_observed' if matches else 'pending_external_identity','evidence_url':u if matches else None,'onchain_contract_verified':False})
(r/'config/asset-registry.json').write_text(json.dumps(registry,indent=2),encoding='utf8')
p=json.loads(sorted((r/'reports').glob('*-pilot.json'))[-1].read_text())
mapping={}
for x in p['coverage']:
    if x.get('symbol') and x.get('asset'):mapping[x['source']+'|'+x['asset']]=x['symbol']
(r/'config/instrument-map.json').write_text(json.dumps(mapping,indent=2),encoding='utf8')
print(json.dumps({'observed_names':sum(x['name'] is not None for x in registry),'pending':[x['asset'] for x in registry if not x['name']],'frozen_instruments':len(mapping)}))
