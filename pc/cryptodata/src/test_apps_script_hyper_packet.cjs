const fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const code=fs.readFileSync(__dirname+'/apps_script_hyper_route.gs','utf8')+'\n'+fs.readFileSync(__dirname+'/apps_script_hyper_packet.gs','utf8');
function setup(status=200){
 const props=new Map([['hyper_sample_pass','20261015-hyper-sample-v1'],['hyper_run_20261015-hyper-three-v1','PASS']]),calls=[],logs=[];
 class Clock extends Date{static now(){return Date.parse('2026-10-15T15:01:00+07:00');}}
 const symbols=['BTC','ETH','ATOM','SOL','AVAX','BNB','OP','LTC','ARB','DOGE','SUI','CRV','LDO','LINK','XRP','BCH','APT','AAVE','TRX','UNI','DOT','ADA','PENDLE','NEAR','ENA','HYPE','MORPHO'];
 const context={Date:Clock,console:{log:x=>logs.push(x)},PropertiesService:{getScriptProperties:()=>({getProperty:k=>props.get(k)||null,getProperties:()=>Object.fromEntries(props),setProperty:(k,v)=>{assert.ok(Buffer.byteLength(v)<9000);props.set(k,v);}})},LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock(){}})},Utilities:{newBlob:v=>({getBytes:()=>Array.from(Buffer.from(v))}),base64Encode:v=>Buffer.from(v).toString('base64'),DigestAlgorithm:{SHA_256:'sha256'},computeDigest:(algorithm,b)=>Array.from(crypto.createHash(algorithm).update(Buffer.from(b)).digest())},UrlFetchApp:{fetch:(url)=>{assert.equal(url,'https://api.hyperliquid.xyz/info');calls.push(url);return {getResponseCode:()=>status,getAllHeaders:()=>({'Retry-After':'1200'}),getContentText:()=>JSON.stringify([{universe:symbols.map(name=>({name}))},symbols.map(()=>({markPx:'2',openInterest:'0',funding:'-0.001'}))])};}}};
 vm.createContext(context);vm.runInContext(code,context);return{context,props,calls,logs};
}
{
 const s=setup();s.context.cryptodataHyperPacketCapture();assert.equal(s.calls.length,1);assert.equal(s.props.get('packet_claim_20261015-hyper-packet-v1'),'STORED');
 s.context.cryptodataHyperPacketExport();const encoded=s.logs.filter(x=>x.startsWith('CRYPTODATA_PACKET_PART_')).map(x=>{assert.ok(x.length<6500);return x.slice(x.indexOf('=')+1);}).join('');const raw=Buffer.from(encoded,'base64').toString();const env=JSON.parse(raw);assert.equal(crypto.createHash('sha256').update(env.payload_text).digest('hex'),env.sha256);assert.equal(JSON.parse(env.payload_text).rows.length,27);
 s.context.cryptodataHyperPacketExport();assert.equal(s.calls.length,1);
 assert.throws(()=>s.context.cryptodataHyperPacketCapture(),/already claimed/);assert.equal(s.calls.length,1);
}
{
 const s=setup(429);assert.throws(()=>s.context.cryptodataHyperPacketCapture(),/host blocked/);assert.ok(Number(s.props.get('hyper_cooldown_until'))>=s.context.Date.now()+1205000);assert.equal(s.props.get('packet_claim_20261015-hyper-packet-v1'),'FAILED');
}
{
 const s=setup();s.props.set('hyper_cooldown_until',String(s.context.Date.now()+1));assert.throws(()=>s.context.cryptodataHyperPacketCapture(),/cooldown/);assert.equal(s.calls.length,0);
}
{
 const s=setup();for(let i=0;i<5;i++)s.props.set('packet_manifest_'+i,'{}');assert.throws(()=>s.context.cryptodataHyperPacketCapture(),/spool full/);assert.equal(s.calls.length,0);
}
{
 const s=setup();s.context.LockService.getScriptLock=()=>({tryLock:()=>false});assert.throws(()=>s.context.cryptodataHyperPacketCapture(),/another script/);assert.equal(s.calls.length,0);
}
console.log(JSON.stringify({status:'PASS',scenarios:7,network:'mocked',binance_calls:0}));
