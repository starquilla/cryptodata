/** Bounded manual snapshot spool. No Drive scope, Binance, triggers or deletes. */
function cryptodataHyperPacketCapture() {
  const id='20261015-hyper-packet-v1', now=Date.now();
  const props=PropertiesService.getScriptProperties(), lock=LockService.getScriptLock();
  if(now<Date.parse('2026-10-15T14:57:00+07:00')||now+60000>=Date.parse('2026-10-15T15:25:00+07:00'))throw new Error('outside manual window');
  if(!lock.tryLock(1000))throw new Error('another script execution');
  try {
    if(props.getProperty('packet_claim_'+id))throw new Error('already claimed; inspect receipt, no retry');
    if(props.getProperty('hyper_sample_pass')!=='20261015-hyper-sample-v1'||props.getProperty('hyper_run_20261015-hyper-three-v1')!=='PASS')throw new Error('source gates missing');
    const until=Number(props.getProperty('hyper_cooldown_until')||1791979369025);
    if(!Number.isFinite(until)||until>now)throw new Error('cooldown active or invalid');
    const all=props.getProperties();
    if(Object.keys(all).filter(k=>k.startsWith('packet_manifest_')).length>=5)throw new Error('spool full; no deletion');
    if(Utilities.newBlob(JSON.stringify(all)).getBytes().length>180000)throw new Error('property budget exhausted');
    props.setProperty('packet_claim_'+id,'RUNNING');
    const response=UrlFetchApp.fetch('https://api.hyperliquid.xyz/info',{method:'post',contentType:'application/json',payload:'{"type":"metaAndAssetCtxs"}',muteHttpExceptions:true,followRedirects:false});
    const status=response.getResponseCode();
    if(status===418||status===429){
      let stop=now+(status===418?259200000:900000);
      const headers=response.getAllHeaders();
      for(const key of Object.keys(headers))if(key.toLowerCase()==='retry-after'){
        const value=String(headers[key]);const hint=/^\d+(\.\d+)?$/.test(value)?now+Number(value)*1000:Date.parse(value);
        if(Number.isFinite(hint))stop=Math.max(stop,hint+5000);
      }
      props.setProperty('hyper_cooldown_until',String(Math.max(stop,until)));
      throw new Error('host blocked; cooldown persisted');
    }
    if(status!==200)throw new Error('HTTP '+status);
    const data=JSON.parse(response.getContentText());
    if(!Array.isArray(data)||!data[0]||!Array.isArray(data[0].universe)||!Array.isArray(data[1])||data[0].universe.length!==data[1].length)throw new Error('invalid shape');
    const names=data[0].universe.map(x=>x.name);
    if(new Set(names).size!==names.length)throw new Error('duplicate provider symbol');
    const rows=HYPER_ROUTE_SYMBOLS.map(symbol=>{
      const index=names.indexOf(symbol);if(index<0)throw new Error('missing mapped symbol');
      const c=data[1][index];
      for(const key of ['markPx','openInterest','funding'])if(c[key]==null||c[key]===''||!Number.isFinite(Number(c[key])))throw new Error('invalid numeric field');
      if(Number(c.markPx)<=0||Number(c.openInterest)<0)throw new Error('invalid numeric range');
      return {symbol,context:c};
    });
    const payload={schema:1,dataset:'hyperliquid_context_raw',packet_id:id,source:'hyperliquid_perpetual',observed_at:new Date().toISOString(),provider_timestamp:null,unit_status:'vendor fields retained; semantic acceptance pending',rows};
    const text=JSON.stringify(payload), bytes=Utilities.newBlob(text).getBytes();
    if(bytes.length>60000)throw new Error('packet exceeds pilot limit');
    const sha=Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,bytes).map(b=>(b&255).toString(16).padStart(2,'0')).join('');
    const envelope=JSON.stringify({schema:1,payload_text:text,sha256:sha});
    // ASCII encoding keeps every stored chunk below the9KB per-value limit.
    const encoded=Utilities.base64Encode(Utilities.newBlob(envelope).getBytes());
    if(encoded.length>100000)throw new Error('encoded packet exceeds pilot limit');
    const chunks=[];for(let i=0;i<encoded.length;i+=6000)chunks.push(encoded.slice(i,i+6000));
    chunks.forEach((chunk,i)=>props.setProperty('packet_chunk_'+id+'_'+i,chunk));
    props.setProperty('packet_manifest_'+id,JSON.stringify({packet_id:id,sha256:sha,chunks:chunks.length,bytes:bytes.length,status:'PENDING_LOCAL_VERIFICATION'}));
    props.setProperty('packet_claim_'+id,'STORED');
    console.log('CRYPTODATA_PACKET_STORED='+JSON.stringify({packet_id:id,sha256:sha,rows:rows.length,chunks:chunks.length,bytes:bytes.length,status:'STORED',storage:'Script Properties bounded pilot; not Drive'}));
  }catch(error){if(props.getProperty('packet_claim_'+id)==='RUNNING')props.setProperty('packet_claim_'+id,'FAILED');throw error;}
  finally{lock.releaseLock();}
}

function cryptodataHyperPacketExport() {
  const id='20261015-hyper-packet-v1', props=PropertiesService.getScriptProperties();
  const text=props.getProperty('packet_manifest_'+id);if(!text)throw new Error('manifest missing');
  const manifest=JSON.parse(text);
  const parts=[];
  for(let i=0;i<manifest.chunks;i++){const part=props.getProperty('packet_chunk_'+id+'_'+i);if(part==null)throw new Error('chunk missing');parts.push(part);}
  // Read-only export; repeated export never calls the provider or creates a new packet.
  console.log('CRYPTODATA_PACKET_MANIFEST='+text);
  parts.forEach((part,i)=>console.log('CRYPTODATA_PACKET_PART_'+i+'='+part));
}
