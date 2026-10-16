/** Availability only. Requires external_request, no Drive/Gmail/trading access. */
const CRYPTODATA_PROBES = [["binance_spot","candles","https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=5m&limit=3","binance_candles",null],["okx_spot","candles","https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT&bar=5m&limit=3","okx_candles",null],["binance_perpetual","funding","https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1","binance_funding",null],["binance_perpetual","oi","https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT","binance_oi",null],["binance_perpetual","oi_history","https://fapi.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period=1h&limit=2","binance_history",null],["bybit_perpetual","funding","https://api.bybit.com/v5/market/funding/history?category=linear&symbol=BTCUSDT&limit=1","bybit_funding",null],["bybit_perpetual","oi_history","https://api.bybit.com/v5/market/open-interest?category=linear&symbol=BTCUSDT&intervalTime=1h&limit=2","bybit_oi",null],["bybit_perpetual","oi","https://api.bybit.com/v5/market/open-interest?category=linear&symbol=BTCUSDT&intervalTime=5min&limit=1","bybit_oi",null],["coinbase_spot","ticker","https://api.exchange.coinbase.com/products/BTC-USD/ticker","coinbase",null],["kraken_spot","ticker","https://api.kraken.com/0/public/Ticker?pair=XBTUSD","kraken",null],["bitflyer_spot","ticker","https://api.bitflyer.com/v1/getticker?product_code=BTC_JPY","bitflyer",null],["bitbank_spot","ticker","https://public.bitbank.cc/btc_jpy/ticker","bitbank",null],["upbit_spot","ticker","https://api.upbit.com/v1/ticker?markets=KRW-BTC","korean",null],["bithumb_spot","ticker","https://api.bithumb.com/v1/ticker?markets=KRW-BTC","korean",null],["hyperliquid_perpetual","context","https://api.hyperliquid.xyz/info","hyperliquid",{"type":"metaAndAssetCtxs"}],["defillama_protocols","snapshot","https://api.llama.fi/protocols","protocols",null],["defillama_chains","snapshot","https://api.llama.fi/v2/chains","chains",null],["defillama_stablecoins","snapshot","https://stablecoins.llama.fi/stablecoins?includePrices=true","stablecoins",null]];
function cryptodataProbeSources() {
  const start=Date.now(), results=[];
  for (const [source,dataset,url,kind,body] of CRYPTODATA_PROBES) {
    const row={source,dataset,url,checked_at:new Date().toISOString()};
    const tick=Date.now();
    if(tick-start>240000){row.status='UNTESTED';row.error='execution safety budget';results.push(row);continue;}
    try {
      const options={muteHttpExceptions:true,followRedirects:true};
      if(body){options.method='post';options.contentType='application/json';options.payload=JSON.stringify(body);}
      const response=UrlFetchApp.fetch(url,options); row.http_status=response.getResponseCode();
      if(row.http_status!==200)throw new Error('HTTP '+row.http_status);
      const data=JSON.parse(response.getContentText());row.sample_rows=cryptodataValidateProbe(kind,data);row.status='PASS';
    }catch(e){row.status='FAIL';row.error=String(e.message||e).slice(0,180);}
    row.seconds=(Date.now()-tick)/1000; results.push(row);Utilities.sleep(300);
  }
  const report={checked_at:new Date().toISOString(),environment:'Google Apps Script',scope:'BTC source availability only; no storage or recurring trigger',status:results.every(x=>x.status==='PASS')?'PASS':'FAIL',seconds:(Date.now()-start)/1000,results};
  console.log('CRYPTODATA_PROBE_REPORT='+JSON.stringify(report));return report;
}
function cryptodataValidateProbe(kind,d) {
  const number=(v,positive=false)=>{const n=Number(v);if(v==null||!Number.isFinite(n)||(positive&&n<=0))throw new Error('invalid sample');};
  if(d&&d.retCode!=null&&d.retCode!==0)throw new Error('provider retCode '+d.retCode);
  if(d&&d.code!=null&&String(d.code)!=='0')throw new Error('provider code '+d.code);
  switch(kind){
    case 'binance_candles':number(d[0][4],true);return d.length;
    case 'okx_candles':number(d.data[0][4],true);return d.data.length;
    case 'binance_funding':number(d[0].fundingRate);return d.length;
    case 'binance_oi':number(d.openInterest,true);return 1;
    case 'binance_history':number(d[0].sumOpenInterest,true);return d.length;
    case 'bybit_funding':number(d.result.list[0].fundingRate);return d.result.list.length;
    case 'bybit_oi':number(d.result.list[0].openInterest);return d.result.list.length;
    case 'coinbase':number(d.price,true);return 1;
    case 'kraken':if(d.error&&d.error.length)throw new Error('Kraken error');number(Object.values(d.result)[0].c[0],true);return Object.keys(d.result).length;
    case 'bitflyer':number(d.ltp,true);return 1;
    case 'bitbank':if(d.success!==1)throw new Error('bitbank failure');number(d.data.last,true);return 1;
    case 'korean':number(d[0].trade_price,true);return d.length;
    case 'hyperliquid':const i=d[0].universe.findIndex(x=>x.name==='BTC');if(i<0)throw new Error('BTC absent');number(d[1][i].markPx,true);number(d[1][i].openInterest,true);number(d[1][i].funding);return d[1].length;
    case 'protocols':case 'chains':if(!Array.isArray(d)||!d.length||!d[0].name)throw new Error('empty shared data');return d.length;
    case 'stablecoins':if(!d.peggedAssets||!d.peggedAssets.length)throw new Error('empty stablecoins');return d.peggedAssets.length;
    default:throw new Error('unknown probe');
  }
}
