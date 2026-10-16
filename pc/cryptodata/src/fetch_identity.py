import json,gzip,pathlib,urllib.request,datetime as dt
r=pathlib.Path(r'E:\CryptoData tai nguyen');out=r/'raw/identity';out.mkdir(exist_ok=True)
urls={'coinbase':'https://api.coinbase.com/v2/currencies/crypto','kraken':'https://api.kraken.com/0/public/Assets'}
for name,u in urls.items():
 try:
  with urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'CryptoDataResearch/0.3'}),timeout=20) as f:d=json.load(f)
  (out/(name+'.json')).write_text(json.dumps({'url':u,'retrieved_at':dt.datetime.now(dt.timezone.utc).isoformat(),'data':d}),encoding='utf8')
  print(name,str(d)[:700])
 except Exception as e:print(name,str(e))
