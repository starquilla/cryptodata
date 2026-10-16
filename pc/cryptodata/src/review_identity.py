import pathlib,gzip,json
r=pathlib.Path(r'E:\CryptoData tai nguyen')
f=sorted(r.glob('raw/pilot-*/*defillama_protocols.json.gz'))[-1]
data=json.loads(gzip.decompress(f.read_bytes()))
assets=json.loads((r/'config/settings.json').read_text())['assets']
result=[]
for asset in assets:
    matches=[{'name':x.get('name'),'slug':x.get('slug'),'symbol':x.get('symbol'),'address':x.get('address'),'chain':x.get('chain'),'url':x.get('url'),'gecko_id':x.get('gecko_id')} for x in data if str(x.get('symbol','')).upper()==asset.upper()]
    result.append({'asset':asset,'candidates':matches,'status':'needs_manual_identity_review' if matches else 'no_protocol_symbol_match','evidence_file':str(f),'not_exchange_identity_verification':True})
(r/'reports/protocol-identity-candidates.json').write_text(json.dumps(result,indent=2),encoding='utf8')
print(json.dumps([{'asset':x['asset'],'candidate_count':len(x['candidates'])} for x in result]))
