'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {microReply}=require('../micro_reply');
const {needsReview,safeReply}=require('../policy');

test('unambiguous small talk can get lightweight replies with no AI call',()=>{
  const style='Hiya mate. No worries, thanks for getting back to me. Cheers!';
  assert.equal(microReply('Hi',style),'Hiya!');
  assert.equal(microReply('Good morning!',style),'Morning!');
  assert.equal(microReply('Thank you.',style),'No worries!');
  assert.equal(microReply('Cheers',style),'No worries!');
  assert.equal(microReply('  hello!  ',style),'Hiya!');
});
test('unknown context, factual questions and high impact topics never get fallback answers',()=>{
  const blocked=['How was your weekend?', 'Are you at home?', 'Will you come over?',
    'I need the bank transfer', 'You promised', 'Please pay £40',
    'Did you speak to the doctor?', 'Can I come around tonight?',
    'What time are we meeting?', 'Do you love me?', 'Thanks for transferring £300',
    'Please share the password','https://example.com','', 'yes?', 'Hi\nMy account number is 1234'];
  for(const text of blocked){
    assert.equal(microReply(text,'Hiya, no worries'),null,text);
  }
});
test('all generated micro replies pass same automated outbound gate',()=>{
  for(const text of ['hi','morning','thank you','okay','good evening']){
    const reply=microReply(text,'Hiya. No worries.');
    assert.ok(reply);
    assert.equal(safeReply(reply),true);
    assert.equal(needsReview(reply),false);
  }
});
