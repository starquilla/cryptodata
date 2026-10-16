const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
for(const status of [200,403]){
 const logs=[],calls=[],token='synthetic-oauth-secret';
 const ctx={ScriptApp:{getOAuthToken:()=>token},console:{log:x=>logs.push(x)},UrlFetchApp:{fetch:(url,options)=>{
  calls.push({url,options});
  return {getResponseCode:()=>status,getContentText:()=>JSON.stringify(status===200?{files:[]}:{error:{status:'PERMISSION_DENIED',message:'synthetic permission failure',errors:[{reason:'insufficientPermissions'}]}})};
 }}};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync(__dirname+'/apps_script_hyper_drive.gs','utf8'),ctx);
 ctx.cryptodataDrivePermissionCheck();
 assert.equal(calls.length,1);assert.equal(calls[0].options.method,undefined);
 assert.equal(calls[0].url,'https://www.googleapis.com/drive/v3/files?pageSize=1&fields=files(id)');
 assert.equal(calls[0].options.followRedirects,false);
 assert.equal(calls[0].options.headers.Authorization,'Bearer '+token);
 assert.equal(logs.length,1);assert.ok(!logs.join('').includes(token));assert.ok(!logs.join('').includes('Authorization'));
 const record=JSON.parse(logs[0].split('CRYPTODATA_DRIVE_DIAGNOSTIC=')[1]);
 assert.equal(record.http_status,status);
 assert.equal(record.status,status===200?null:'PERMISSION_DENIED');
 if(status===403)assert.deepEqual(record.reasons,['insufficientPermissions']);
}
console.log(JSON.stringify({status:'PASS',scenarios:2,network:'mocked only',model_calls:0}));
