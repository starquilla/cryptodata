/** Hyperliquid-only manual acceptance. No Binance, Drive, triggers or trading. */
const HYPER_ROUTE_SYMBOLS = ['BTC','ETH','ATOM','SOL','AVAX','BNB','OP','LTC','ARB','DOGE','SUI','CRV','LDO','LINK','XRP','BCH','APT','AAVE','TRX','UNI','DOT','ADA','PENDLE','NEAR','ENA','HYPE','MORPHO'];
function cryptodataHyperliquidOnly() { return cryptodataHyperRoute_(1,'20261015-hyper-sample-v1'); }
function cryptodataHyperliquidThree() { return cryptodataHyperRoute_(3,'20261015-hyper-three-v1'); }
function cryptodataHyperRoute_(rounds,runId) {
  const now=Date.now(), notBefore=Date.parse('2026-10-15T14:00:00+07:00'), deadline=Date.parse('2026-10-15T14:40:00+07:00');
  const props=PropertiesService.getScriptProperties(), lock=LockService.getScriptLock();
  const report={run_id:runId,source:'hyperliquid_perpetual',environment:'Google Apps Script',requested_rounds:rounds,results:[],status:'FAIL',scope:'27 configured Hyperliquid contexts; not all30 assets, funding settlement history, transport or production acceptance'};
  const emit=()=>{console.log('CRYPTODATA_HYPER_ROUTE='+JSON.stringify(report));return report;};
  if(now<notBefore || now+60000>=deadline){report.error='outside permitted window before PC14:42';return emit();}
  if(!lock.tryLock(1000)){report.error='another script execution holds lock';return emit();}
  try {
    if(props.getProperty('hyper_run_'+runId)){report.error='run already claimed; no automatic replay';return emit();}
    if(rounds===3 && props.getProperty('hyper_sample_pass')!=='20261015-hyper-sample-v1'){report.error='sample gate not passed';return emit();}
    props.setProperty('hyper_run_'+runId,'RUNNING');
    for(let round=1;round<=rounds;round++) {
      const tick=Date.now(), until=Number(props.getProperty('hyper_cooldown_until')||1791979369025);
      if(!Number.isFinite(until) || until>tick){report.error='cooldown active or invalid';break;}
      if(tick+30000>=deadline){report.error='deadline too close';break;}
      const row={round,observed_at:new Date(tick).toISOString(),provider_timestamp:null,freshness:'observation only; provider timestamp absent'};
      try {
        const response=UrlFetchApp.fetch('https://api.hyperliquid.xyz/info',{method:'post',contentType:'application/json',payload:'{"type":"metaAndAssetCtxs"}',muteHttpExceptions:true,followRedirects:false});
        row.http_status=response.getResponseCode();
        if(row.http_status===418 || row.http_status===429) {
          const headers=response.getAllHeaders();let hint=0;
          for(const key of Object.keys(headers))if(key.toLowerCase()==='retry-after'){
            const text=String(headers[key]);hint=/^\d+(\.\d+)?$/.test(text)?tick+Number(text)*1000:Date.parse(text);
          }
          const fallback=tick+(row.http_status===418?259200000:900000);
          const stop=Math.max(fallback,Number.isFinite(hint)?hint+5000:0,until);
          props.setProperty('hyper_cooldown_until',String(stop));row.retry_at=new Date(stop).toISOString();
          throw new Error('host blocked; no retry');
        }
        if(row.http_status!==200)throw new Error('HTTP '+row.http_status);
        const data=JSON.parse(response.getContentText());
        if(!Array.isArray(data)||!Array.isArray(data[0].universe)||data[0].universe.length!==data[1].length)throw new Error('invalid context shape');
        const index=new Map(data[0].universe.map((x,i)=>[x.name,i]));
        const valid=(value,positive)=>value!=null&&value!==''&&Number.isFinite(Number(value))&&(positive?Number(value)>0:Number(value)>=0);
        row.assets=HYPER_ROUTE_SYMBOLS.map(symbol=>{
          const i=index.get(symbol), c=i===undefined?null:data[1][i];
          const ok=c&&valid(c.markPx,true)&&valid(c.openInterest,false)&&c.funding!=null&&c.funding!==''&&Number.isFinite(Number(c.funding));
          return {symbol,status:ok?'PASS':'FAIL'};
        });
        row.configured_assets=HYPER_ROUTE_SYMBOLS.length;row.passed_assets=row.assets.filter(x=>x.status==='PASS').length;
        row.status=row.passed_assets===row.configured_assets?'PASS':'FAIL';
      }catch(error){row.status='FAIL';row.error=String(error.message||error).slice(0,150);}
      report.results.push(row);
      if(row.status!=='PASS')break;
      if(round<rounds)Utilities.sleep(15000);
    }
    report.status=report.results.length===rounds&&report.results.every(x=>x.status==='PASS')?'PASS':'FAIL';
    if(rounds===1&&report.status==='PASS')props.setProperty('hyper_sample_pass',runId);
    props.setProperty('hyper_run_'+runId,report.status);
    return emit();
  } finally {lock.releaseLock();}
}
