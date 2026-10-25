'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {generateReply,privateApiKey,extractResponse,RESPONSES_URL}=require('../openai_reply');

function mockKey(){
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'oci-openai-unit-'));
  fs.chmodSync(root,0o700);
  const keyFile=path.join(root,'api-key');
  fs.writeFileSync(keyFile,'sk-'+('X'.repeat(50)),{mode:0o600});
  return {keyFile,cleanup:()=>fs.rmSync(root,{recursive:true,force:true})};
}
const examples='Hiya, thanks for getting back to me. I appreciate you letting me know. Hope you are having a good week. Speak soon and take care.';

test('private key is owner protected; insecure permissions refused',()=>{
  const k=mockKey();
  try{
    assert.equal(privateApiKey(k.keyFile),'sk-'+('X'.repeat(50)));
    fs.chmodSync(k.keyFile,0o644);
    assert.throws(()=>privateApiKey(k.keyFile),/0600/);
    fs.chmodSync(k.keyFile,0o600);
    fs.symlinkSync(k.keyFile,k.keyFile+'-link');
    assert.throws(()=>privateApiKey(k.keyFile+'-link'),/0600/);
  }finally{k.cleanup();}
});
test('cloud transfer must be explicitly consented to, even when a key exists',async()=>{
  const k=mockKey();
  try{await assert.rejects(()=>generateReply({keyFile:k.keyFile,conversation:[{body:'Hello',fromMe:false}],examples}),/permission/);}
  finally{k.cleanup();}
});
test('structured API response, no remote storage or chat identifiers',async()=>{
  const k=mockKey();
  let sent;
  const fetcher=async(url,args)=>{
    sent={url,args};
    return {ok:true,status:200,json:async()=>({status:'completed',
      output:[{content:[{type:'output_text',text:'{"reply":"Hiya, hope you are well!","needs_review":false}'}]}]
    })};
  };
  try{
    const reply=await generateReply({keyFile:k.keyFile,cloudPrivacyConsent:true,
      model:'gpt-5-mini',examples,conversation:[{body:'Hi Martin, how are you?',fromMe:false}],fetcher});
    assert.equal(reply.reply,'Hiya, hope you are well!');
    assert.equal(reply.needsReview,false);
    assert.equal(sent.url,RESPONSES_URL);
    assert.equal(sent.args.method,'POST');
    assert.equal(sent.args.headers.Authorization,'Bearer sk-'+('X'.repeat(50)));
    const payload=JSON.parse(sent.args.body);
    assert.equal(payload.store,false);
    assert.equal(payload.text.format.type,'json_schema');
    assert.equal(payload.text.format.strict,true);
    assert.deepEqual(Object.keys(payload.text.format.schema.properties),['reply','needs_review']);
    assert.equal(payload.input.at(-1).content,'Hi Martin, how are you?');
    assert.ok(!sent.args.body.includes('447700900123@c.us'));
    assert.ok(!JSON.stringify(reply).includes('sk-'));
  }finally{k.cleanup();}
});
test('sensitive messages never leave PC and are held',async()=>{
  let called=false;
  const reply=await generateReply({cloudPrivacyConsent:true,examples,
    conversation:[{body:'Please send me your bank details',fromMe:false}],
    fetcher:async()=>{called=true;throw Error('Should not call network');}});
  assert.equal(reply.needsReview,true);
  assert.equal(called,false);
});
test('API refusals and failures never generate sendable drafts',async()=>{
  const k=mockKey();
  try{
    const input={keyFile:k.keyFile,cloudPrivacyConsent:true,examples,
      conversation:[{body:'Hi, are you about?',fromMe:false}]};
    await assert.rejects(()=>generateReply({...input,fetcher:async()=>({
      ok:false,status:429})}),/429/);
    await assert.rejects(()=>generateReply({...input,fetcher:async()=>({
      ok:true,json:async()=>({status:'incomplete',output:[]})})}),/not completed/);
    await assert.rejects(()=>generateReply({...input,fetcher:async()=>({
      ok:true,json:async()=>({status:'completed',output:[{content:[{type:'refusal'}]}]})})}),/declined/);
    await assert.rejects(()=>generateReply({...input,fetcher:async()=>({
      ok:true,json:async()=>({status:'completed',output:[{content:[{type:'output_text',text:'not JSON'}]}]})})}),/invalid structured/);
  }finally{k.cleanup();}
});
test('output with money or commitments is held, despite model allow decision',()=>{
  const response=extractResponse({status:'completed',output:[{content:[{type:'output_text',text:'{"reply":"I will pay the money now","needs_review":false}'}]}]});
  assert.equal(response.needs_review,false);
});
