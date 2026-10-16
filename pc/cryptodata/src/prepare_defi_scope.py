import gzip,json,pathlib
from data_audit import ROOT,load,save
last=load(ROOT/'state/latest.json');report=load(ROOT/'reports'/pathlib.Path(last['report']).name)
req=next(x for x in report['requests'] if x.get('source')=='defillama_protocols' and x.get('status')=='ok')
entries=json.loads(gzip.decompress(pathlib.Path(req['file']).read_bytes()))
selected=sorted([x for x in entries if isinstance(x.get('tvl'),(int,float)) and x['tvl']>=0],key=lambda x:x['tvl'],reverse=True)[:500]
save(ROOT/'config/defi-protocol-scope.json',{'version':1,'selection':'fixed 500 highest TVL protocol records at initialization; source identities, not token joins','source_observed_at':req['observed_at'],'protocol_ids':[str(x.get('slug') or x.get('id') or x.get('name')) for x in selected],'raw_policy':'all protocols still captured every 2h; other metrics recoverable from retained raw','chain_and_stablecoin_scope':'all returned entities'})
print(json.dumps({'protocol_scope':len(selected)}))
