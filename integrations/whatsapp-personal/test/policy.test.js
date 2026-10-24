'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {chatAllowed,needsReview,safeReply,shortHash,validConfig}=require('../policy');
const {generateReply,LOCAL_OLLAMA}=require('../ollama');

const base={schema:1,autoSend:false,approvedChats:[],ollamaModel:'qwen3.5:4b',
  debounceMs:10000,maxPerContactDay:6};
test('explicit per-chat consent required, group and unknown rejected',()=>{
  const ids=new Set(['447700900123@c.us','123456789012345@lid']);
  assert.equal(chatAllowed('447700900123@c.us',ids),true);
  assert.equal(chatAllowed('123456789012345@lid',ids),true);
  assert.equal(chatAllowed('222222222222@g.us',ids),false);
  assert.equal(chatAllowed('445555555555@c.us',ids),false);
  assert.equal(chatAllowed('status@broadcast',ids),false);
});
test('automatic mode must have approved contacts and explicit bool',()=>{
  assert.equal(validConfig({...base}).autoSend,false);
  assert.throws(()=>validConfig({...base,autoSend:true}),/approved contacts/);
  assert.throws(()=>validConfig({...base,autoSend:'true'}),/boolean/);
  assert.throws(()=>validConfig({...base,approvedChats:['123456789@g.us']}),/exact approved/);
  assert.throws(()=>validConfig({...base,debounceMs:0}),/Debounce/);
  assert.equal(validConfig({...base,autoSend:true,approvedChats:['447700900123@c.us']}).autoSend,true);
});
test('financial legal health danger and promises require human review',()=>{
  for(const message of ['Can you transfer £500?', 'Please send the password',
    'The doctor called about medication', 'It is an emergency', 'Will you sign the contract?',
    'I love you', 'Can you book it for me?', 'Bank transfer details']){
    assert.equal(needsReview(message),true,message);
  }
  assert.equal(needsReview('Hey, how was your weekend?'),false);
  assert.equal(safeReply('Hi, hope you had a good weekend!'),true);
  assert.equal(safeReply('I guarantee to pay you £500'),false);
});
test('hashes are deterministic and do not expose chat ID',()=>{
  const result=shortHash('447700900123@c.us');
  assert.equal(result.length,24);
  assert.equal(shortHash('447700900123@c.us'),result);
  assert.equal(result.includes('447700900123'),false);
});
test('model only reachable via loopback and receives bounded human style reference',async()=>{
  let called;
  const generate=async(url,options)=>{
    called={url,options};return {ok:true,json:async()=>({message:{
      content:'{"reply":"Hi, hope you are well!","needs_review":false}'
    }})};
  };
  const result=await generateReply({model:'qwen3.5:4b',sender:'447700900123@c.us',
    examples:'Hiya, thanks for getting back to me. I really appreciate that. Hope all is well on your side and speak soon.',
    conversation:[{body:'How was the weekend?',fromMe:false}],fetcher:generate});
  assert.equal(result.reply,'Hi, hope you are well!');
  assert.equal(result.needsReview,false);
  assert.equal(called.url,LOCAL_OLLAMA);
  const payload=JSON.parse(called.options.body);
  assert.equal(payload.stream,false);
  assert.equal(payload.think,false);
  assert.equal(payload.messages.at(-1).content,'How was the weekend?');
});
test('model cannot override sensitive message rule or unsafe outgoing reply',async()=>{
  const fn=async()=>({ok:true,json:async()=>({message:{
    content:'{"reply":"No worries, will do","needs_review":false}'}})});
  const result=await generateReply({model:'qwen3.5:4b',sender:'447700900123@c.us',
    examples:'I hope your week is going okay and I wanted to send my thanks for everything. Speak to you soon, cheers.',
    conversation:[{body:'Please send your sort code and bank details',fromMe:false}],fetcher:fn});
  assert.equal(result.needsReview,true);
});
test('refuses to generate without authentic approved examples',async()=>{
  await assert.rejects(generateReply({
    model:'qwen3.5:4b',sender:'1@c.us',examples:'hello',
    conversation:[{body:'Hi',fromMe:false}],fetcher:()=>{throw Error('Should not connect');}
  }),/examples required/);
});
