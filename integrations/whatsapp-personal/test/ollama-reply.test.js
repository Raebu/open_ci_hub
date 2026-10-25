'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {generateReply,unverifiableAutobiography}=require('../ollama');
const examples='Hiya, thank you very much, speak soon. Lovely to hear from you. No worries, I hope you have a lovely evening. Thanks so much.';
function fake(text, capture) {
  return async (url, options)=>{
    if(capture)capture.push({url,body:JSON.parse(options.body)});
    return {ok:true,json:async()=>({message:{content:JSON.stringify({reply:text,needs_review:false})}})};
  };
}
test('keeps local model warm briefly, reduces size, and never transmits to a cloud API',async()=>{
  const calls=[];
  const response=await generateReply({
    model:'qwen2.5:1.5b',sender:'synthetic',examples,
    conversation:[{fromMe:false,body:'Hello there'}],
    fetcher:fake('Hiya, hope you are well!',calls)
  });
  assert.equal(response.needsReview,false);
  assert.equal(calls.length,1);
  assert.equal(calls[0].url,'http://127.0.0.1:11434/api/chat');
  assert.equal(calls[0].body.keep_alive,'5m');
  assert.equal(calls[0].body.options.num_thread,4);
  assert.equal(calls[0].body.options.num_predict,80);
  assert.equal(calls[0].body.messages.at(-1).content,'Hello there');
});
test('holds invented autobiographical detail and chatbot phrasing',async()=>{
  for(const message of ['Just a busy week for me, but thanks!','I went out with friends yesterday.','Let me know if there is anything I can help with.']){
    assert.equal(unverifiableAutobiography(message),true,message);
    const result=await generateReply({model:'test',sender:'synthetic',examples,
      conversation:[{fromMe:false,body:'Hello there'}],fetcher:fake(message)});
    assert.equal(result.needsReview,true,message);
  }
});
test('holds sensitive incoming messages even if the model approves them',async()=>{
  const result=await generateReply({model:'test',sender:'synthetic',examples,
    conversation:[{fromMe:false,body:'I love you'}],fetcher:fake('Hiya!')});
  assert.equal(result.needsReview,true);
});
