'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {safeReply,needsReview,looksLikePlaceholder}=require('../policy');
const {generateReply}=require('../ollama');
const examples='Hiya, thank you very much, speak soon. Lovely to hear from you. No worries, I hope you have a lovely evening. Thanks so much.';
function fake(reply,needs_review=false){
  return async()=>({ok:true,json:async()=>({message:{content:JSON.stringify({reply,needs_review})}})});
}
const sourceConversation=[
  {fromMe:true,body:"Good morning, my beautiful. I hope your exercises go well."},
  {fromMe:false,body:"Thanks for effort but I think these words mean nothing if can't act upon like discussing about future, simple household stuff plans what is gonna happen next something which makes me feel peace inside."}
];

test('exact screenshot placeholder is NEVER a sendable WhatsApp message',async()=>{
  assert.equal(looksLikePlaceholder('Short natural WhatsApp response'),true);
  assert.equal(safeReply('Short natural WhatsApp response'),false);
  for(const message of ['short natural whatsapp response','Short natural WhatsApp reply',
     'Example response', 'Your reply here', 'Placeholder text','Reply goes here']){
    assert.equal(safeReply(message),false,message);
  }
  const result=await generateReply({model:'test',sender:'synthetic',
    conversation:[{fromMe:false,body:'Hi'}],examples,
    fetcher:fake('Short natural WhatsApp response')});
  assert.equal(result.needsReview,true);
  assert.equal(result.reply,'Short natural WhatsApp response');
});

test('screenshot relationship and household planning request always requires human review',async()=>{
  const message=sourceConversation[1].body;
  assert.equal(needsReview(message),true);
  const draft=await generateReply({model:'test',sender:'synthetic',
    conversation:sourceConversation,examples,
    fetcher:fake('No worries, have a lovely morning!')});
  assert.equal(draft.needsReview,true);
  for(const sensitive of [
   'We need to discuss our future together',
   'Can we make household plans?',
   'I need to feel secure',
   'What is going to happen next?',
   'Words mean nothing without action'
  ])assert.equal(needsReview(sensitive),true,sensitive);
});
test('real short non-placeholder courtesy replies remain possible for ordinary low-risk small talk',()=>{
  assert.equal(safeReply('Morning!'),true);
  assert.equal(safeReply('No worries, speak soon!'),true);
});
test('prompt does not contain response placeholder as literal answer example',async()=>{
  let prompt='';
  await generateReply({model:'test',sender:'synthetic',
    conversation:[{fromMe:false,body:'Morning'}],examples,
    fetcher:async(_,request)=>{
      prompt=JSON.parse(request.body).messages[0].content;
      return fake('Morning!')();
    }});
  assert.equal(prompt.toLowerCase().includes('"reply":"short natural whatsapp response"'),false);
  assert.match(prompt,/actual message/i);
});
