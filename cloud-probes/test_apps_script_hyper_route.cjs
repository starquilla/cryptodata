const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const code=fs.readFileSync(__dirname+'/apps_script_hyper_route.gs','utf8');
function setup(status=200){
 const props=new Map(),calls=[];let released=false;
 class Clock extends Date{static now(){return Date.parse('2026-10-15T14:30:00+07:00');}}
 const symbols=['BTC','ETH','ATOM','SOL','AVAX','BNB','OP','LTC','ARB','DOGE','SUI','CRV','LDO','LINK','XRP','BCH','APT','AAVE','TRX','UNI','DOT','ADA','PENDLE','NEAR','ENA','HYPE','MORPHO'];
 const ctx={Date:Clock,console:{log(){}},PropertiesService:{getScriptProperties:()=>({getProperty:k=>props.get(k)||null,setProperty:(k,v)=>props.set(k,v)})},LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock:()=>released=true})},Utilities:{sleep(){}},UrlFetchApp:{fetch:(url,opts)=>{calls.push(url);assert.equal(url,'https://api.hyperliquid.xyz/info');assert.equal(opts.followRedirects,false);return{getResponseCode:()=>status,getAllHeaders:()=>({'Retry-After':'1'}),getContentText:()=>JSON.stringify([{universe:symbols.map(name=>({name}))},symbols.map(()=>({markPx:'1',openInterest:'0',funding:'-0.001'}))])};}}};
 vm.createContext(ctx);vm.runInContext(code,ctx);return{ctx,props,calls,released:()=>released};
}
{
 const s=setup();assert.equal(s.ctx.cryptodataHyperliquidThree().status,'FAIL');assert.equal(s.calls.length,0);
 assert.equal(s.ctx.cryptodataHyperliquidOnly().status,'PASS');assert.equal(s.calls.length,1);
 assert.equal(s.ctx.cryptodataHyperliquidOnly().status,'FAIL');assert.equal(s.calls.length,1);
 assert.equal(s.ctx.cryptodataHyperliquidThree().status,'PASS');assert.equal(s.calls.length,4);assert.ok(s.released());
}
{
 const s=setup(429);assert.equal(s.ctx.cryptodataHyperliquidOnly().status,'FAIL');assert.equal(s.calls.length,1);
 assert.ok(Number(s.props.get('hyper_cooldown_until'))>=s.ctx.Date.now()+900000);
 s.props.set('hyper_sample_pass','20261015-hyper-sample-v1');assert.equal(s.ctx.cryptodataHyperliquidThree().status,'FAIL');assert.equal(s.calls.length,1);
}
{
 const s=setup();s.ctx.LockService.getScriptLock=()=>({tryLock:()=>false});assert.equal(s.ctx.cryptodataHyperliquidOnly().status,'FAIL');assert.equal(s.calls.length,0);
}
console.log(JSON.stringify({status:'PASS',cases:7,network:'mocked Hyperliquid only',binance_calls:0}));
