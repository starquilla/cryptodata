/** Upload an existing packet only. Requires explicit drive.file consent; no provider fetch. */
function cryptodataHyperPacketToDrive() {
  const id='20261015-hyper-packet-v1',props=PropertiesService.getScriptProperties(),lock=LockService.getScriptLock();
  if(!lock.tryLock(1000))throw new Error('another script execution');
  try {
    const manifest=JSON.parse(props.getProperty('packet_manifest_'+id)||'null');
    if(!manifest||manifest.packet_id!==id)throw new Error('packet manifest missing');
    let encoded='';for(let i=0;i<manifest.chunks;i++){const p=props.getProperty('packet_chunk_'+id+'_'+i);if(p==null)throw new Error('packet chunk missing');encoded+=p;}
    const bytes=Utilities.base64Decode(encoded),text=Utilities.newBlob(bytes).getDataAsString('UTF-8'),envelope=JSON.parse(text);
    const hex=arr=>arr.map(b=>(b&255).toString(16).padStart(2,'0')).join('');
    const sha=hex(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,Utilities.newBlob(envelope.payload_text).getBytes()));
    if(sha!==manifest.sha256||sha!==envelope.sha256)throw new Error('local packet checksum mismatch');
    const md5=hex(Utilities.computeDigest(Utilities.DigestAlgorithm.MD5,bytes));
    const request=(url,options)=>{
      options=options||{};options.headers={Authorization:'Bearer '+ScriptApp.getOAuthToken()};options.muteHttpExceptions=true;options.followRedirects=false;
      const r=UrlFetchApp.fetch(url,options),code=r.getResponseCode();
      if(code<200||code>=300)throw new Error('Drive HTTP '+code+'; inspect before retry');
      return JSON.parse(r.getContentText());
    };
    const q="trashed=false and appProperties has { key='packet_id' and value='"+id+"' } and appProperties has { key='producer' and value='CryptoData-Hyper-Pilot' }";
    const found=request('https://www.googleapis.com/drive/v3/files?q='+encodeURIComponent(q)+'&fields=files(id,size,md5Checksum,appProperties),nextPageToken&pageSize=10');
    if(found.nextPageToken||found.files.length>1)throw new Error('duplicate Drive packet requires review');
    let file=found.files[0];
    if(!file){
      const boundary='cryptodata_packet_20261015';
      const meta={name:'cryptodata-'+id+'.json',mimeType:'application/json',appProperties:{producer:'CryptoData-Hyper-Pilot',packet_id:id,packet_sha256:sha}};
      const body='--'+boundary+'\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'+JSON.stringify(meta)+'\r\n--'+boundary+'\r\nContent-Type: application/json\r\n\r\n'+text+'\r\n--'+boundary+'--';
      file=request('https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,size,md5Checksum,appProperties',{method:'post',contentType:'multipart/related; boundary='+boundary,payload:body});
    }
    if(Number(file.size)!==bytes.length||file.md5Checksum!==md5||file.appProperties.packet_sha256!==sha)throw new Error('Drive readback mismatch');
    props.setProperty('packet_drive_'+id,JSON.stringify({file_id:file.id,sha256:sha,status:'UPLOADED_PENDING_PC_DOWNLOAD_VERIFY'}));
    console.log('CRYPTODATA_DRIVE_UPLOAD='+JSON.stringify({file_id:file.id,packet_id:id,bytes:bytes.length,sha256:sha,status:'UPLOADED_PENDING_PC_DOWNLOAD_VERIFY'}));
  }finally{lock.releaseLock();}
}
