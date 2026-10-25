'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {parseDirectSend,parseExistingChatSend,verifiedExistingChat}=require('../direct_send');
test('a single explicitly addressed UK WhatsApp message is parsed',()=>{
 assert.deepEqual(parseDirectSend("send +447935457312 Hi, it's Martin. Just testing that my WhatsApp connection is working."),{
  digits:'447935457312',message:"Hi, it's Martin. Just testing that my WhatsApp connection is working."
 });
});
test('unknown formats and commands are never treated as send requests',()=>{
 for(const cmd of ['send','send 07935457312 hello','send @c.us hello','reply 0 hi','send +447935457312','send +447935457312 ','send +447935457312 '+('a'.repeat(1201))]){
  assert.equal(parseDirectSend(cmd),null,cmd.slice(0,35));
 }
});

test('explicit existing-chat ID is parsed separately from phone number',()=>{
  assert.deepEqual(parseExistingChatSend('send 204543673544822@lid Hello there'),{
    chatId:'204543673544822@lid',message:'Hello there'
  });
  assert.deepEqual(parseExistingChatSend('send 447700900123@c.us Hi'),{
    chatId:'447700900123@c.us',message:'Hi'
  });
  for(const cmd of ['send 204543673544822@g.us Hi','send invalid@lid Hi',
    'send 204543673544822@lid','send 204543673544822@lid '+('x'.repeat(1201))]) {
    assert.equal(parseExistingChatSend(cmd),null);
  }
  assert.equal(parseDirectSend('send 204543673544822@lid Hello'),null);
});

test('only a real existing individual chat is eligible for ID-addressed manual send',()=>{
  const id='204543673544822@lid';
  const direct={id:{_serialized:id},isGroup:false,isChannel:false,isReadOnly:false,name:'Test'};
  const group={id:{_serialized:'123456@g.us'},isGroup:true};
  const channel={id:{_serialized:'555555555555@lid'},isGroup:false,isChannel:true};
  const readOnly={id:{_serialized:'999999999999@lid'},isGroup:false,isReadOnly:true};
  const chats=[group,channel,readOnly,direct];
  assert.equal(verifiedExistingChat(chats,id),direct);
  assert.equal(verifiedExistingChat(chats,'123456@g.us'),null);
  assert.equal(verifiedExistingChat(chats,'555555555555@lid'),null);
  assert.equal(verifiedExistingChat(chats,'999999999999@lid'),null);
  assert.equal(verifiedExistingChat(chats,'123456789123456@lid'),null);
  assert.equal(verifiedExistingChat(null,id),null);
});
