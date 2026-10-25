'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {approvedExample,fromOwner,learnOwnerStyle}=require('../style_learning');

test('learning reads only owner-authored, ordinary one-to-one text',()=>{
  assert.equal(approvedExample({fromMe:false,type:'chat',body:'This is another person message.'}),null);
  assert.equal(approvedExample({fromMe:true,type:'image',body:'Here is a photo from me.'}),null);
  assert.equal(approvedExample({fromMe:true,type:'chat',body:'Please transfer £500 today'}),null);
  assert.equal(approvedExample({fromMe:true,type:'chat',body:'My new number is +447700900123'}),null);
  assert.equal(approvedExample({fromMe:true,type:'chat',body:'That works for me, hope you are doing okay!'}),'That works for me, hope you are doing okay!');
  assert.deepEqual(fromOwner([
    {fromMe:true,type:'chat',body:'That works for me, hope you are doing okay!'},
    {fromMe:true,type:'chat',body:'That works for me, hope you are doing okay!'},
    {fromMe:false,type:'chat',body:'Hello, how are you doing today?'}
  ]),['That works for me, hope you are doing okay!']);
});
test('approved-chat learning yields enough real sent examples without logging third-party text',async()=>{
  let accesses=0;
  const own=[
    'Thank you for getting back to me, I do appreciate it!',
    'That sounds lovely, hope you have been keeping well.',
    'Hiya, yes that is absolutely fine with me thanks.',
    'I will have a look at that and get back to you.',
    'That is really kind of you, thank you very much.',
    'I hope everything has been going okay for you lately.',
    'Thanks for sorting it, speak to you again soon.'
  ];
  const client={getChatById:async id=>{
    accesses++;
    assert.equal(id,'447700900123@c.us');
    return {isGroup:false,fetchMessages:async()=>[
      {fromMe:false,type:'chat',body:"Other person's private text."},
      ...own.map(body=>({fromMe:true,type:'chat',body}))
    ]};
  }};
  const result=await learnOwnerStyle(client,['447700900123@c.us']);
  assert.equal(accesses,1);
  assert.ok(result.adequate);
  assert.equal(result.count,7);
  assert.ok(!result.content.includes("Other person's private text."));
});
test('style learning rejects group and failed chat lookup',async()=>{
  const client={getChatById:async id=> id.includes('222')
    ? {isGroup:true,fetchMessages:async()=>{throw Error('group must not be read');}}
    : Promise.reject(new Error('stale WhatsApp chat'))};
  const x=await learnOwnerStyle(client,['222222222222@c.us','333333333333@c.us']);
  assert.equal(x.adequate,false);
  assert.equal(x.count,0);
});
