'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {parseDirectSend}=require('../direct_send');
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
