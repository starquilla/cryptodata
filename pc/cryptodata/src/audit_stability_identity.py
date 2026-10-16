import csv,datetime as dt,gzip,json,pathlib,sqlite3
R=pathlib.Path(r'E:\CryptoData tai nguyen')
config=json.loads((R/'config/settings.json').read_text());assets=config['assets']
cb=json.loads((R/'raw/identity/coinbase.json').read_text());kr=json.loads((R/'raw/identity/kraken.json').read_text())
# Stablecoin registry is a separate dataset, not protocol ticker matching.
f=sorted(R.glob('raw/pilot-*/*defillama_stablecoins.json.gz'))[-1]
stable=json.loads(gzip.decompress(f.read_bytes())).get('peggedAssets',[])
registry=[]
for asset in assets:
 matches=[x for x in cb['data']['data'] if x['code'].upper()==asset.upper()]
 evidence=[];name=None
 if len(matches)==1:
  name=matches[0]['name'];evidence.append({'provider':'Coinbase','url':cb['url'],'asset_id':matches[0]['asset_id'],'name':name})
 if asset=='TRX':
  entries=[x for x in kr['data']['result'].values() if x.get('altname')=='TRX']
  if entries:evidence.append({'provider':'Kraken','url':kr['url'],'code':'TRX','status':'ticker_presence_only'})
 if asset in ['USDT','USDC','USDe']:
  entries=[x for x in stable if str(x.get('symbol','')).upper()==asset.upper()]
  for x in entries:evidence.append({'provider':'DefiLlama stablecoins','file':str(f),'id':x.get('id'),'name':x.get('name'),'symbol':x.get('symbol')})
  if not name and len(entries)==1:name=entries[0].get('name')
 official_path=R/'config/official-identity-evidence.json'
 if official_path.exists():
  official=json.loads(official_path.read_text()).get(asset)
  if official:
   name=official['name'];evidence.append({'provider':'official issuer/network',**official})
 registry.append({'asset':asset,'name':name,'identity_status':'named_external_registry_observed' if name else 'pending_name_confirmation','evidence':evidence,'onchain_contract_verified':False,'cross_venue_identity_verified':False,'eligible_for_automated_trading':False})
(R/'config/asset-registry.json').write_text(json.dumps(registry,indent=2),encoding='utf8')
protocols=json.loads((R/'reports/protocol-identity-candidates.json').read_text())
policy={'version':1,'auto_join_on_symbol':False,'automatic_protocol_aggregation':False,'excluded_symbol_collisions':{'BTC':['alpha','blackrockfund'],'APT':['apricot-finance'],'HYPE':['hyperblast'],'SOL':['solana-farm']},'reason':'Coin ticker alone is not evidence of protocol identity or entitlement to revenue. Native chain assets and stablecoins require separate metric models.'}
(R/'config/protocol-mapping-policy.json').write_text(json.dumps(policy,indent=2),encoding='utf8')
with (R/'reports/asset-identity-review.csv').open('w',newline='',encoding='utf-8-sig') as f:
 w=csv.DictWriter(f,fieldnames=['asset','name','identity_status','onchain_contract_verified','cross_venue_identity_verified']);w.writeheader();w.writerows({k:x[k] for k in w.fieldnames} for x in registry)
db=sqlite3.connect('file:'+str(R/'normalized/market.sqlite3').replace('\\','/')+'?mode=ro',uri=True)
runs=db.execute('SELECT run_id,started_at,status FROM runs ORDER BY started_at').fetchall();db.close()
now=dt.datetime.now(dt.timezone.utc);elapsed=(now-dt.datetime.fromisoformat(runs[0][1])).total_seconds()/3600
result={'checked_at':now.isoformat(),'observed_hours':round(elapsed,2),'minimum_24h_evidence':elapsed>=24,'minimum_48h_evidence':elapsed>=48,'run_count':len(runs),'runs':runs,'named_assets':[x['asset'] for x in registry if x['name']],'pending_names':[x['asset'] for x in registry if not x['name']],'all_cross_venue_and_contract_verification_pending':True,'note':'Manual and scheduler-triggered runs are mixed; run count is not proof of periodic reliability.'}
(R/'reports/stability-and-identity.json').write_text(json.dumps(result,indent=2),encoding='utf8')
print(json.dumps({k:v for k,v in result.items() if k!='runs'},indent=2))
