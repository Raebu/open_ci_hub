'use strict';
/** Style learning from owner's own sent messages in explicitly approved chats.
 * Never returns or logs other people's messages; never uses a cloud LLM.
 */
const {needsReview}=require('./policy');

function approvedExample(message) {
  if(!message || message.fromMe!==true || message.type!=='chat')return null;
  if(typeof message.body!=='string')return null;
  const text=message.body.replace(/\s+/g,' ').trim();
  if(text.length<15 || text.length>245 || needsReview(text))return null;
  if(/https?:\/\/|@|(?:\d[\s().-]*){7,}|[£$€]/i.test(text))return null;
  if(/\b(?:postcode|password|otp|verification|card details|sort code|iban|account|medical|hospital)\b/i.test(text))return null;
  if(!/[.!? ]/.test(text))return null;
  return text;
}
function fromOwner(messages,max=20){
  if(!Array.isArray(messages))return [];
  const unique=new Set();
  for(const item of messages){
    const value=approvedExample(item);
    if(value)unique.add(value);
    if(unique.size>=max)break;
  }
  return [...unique];
}
async function learnOwnerStyle(client,ids,{maxContacts=9,maxMessages=45}={}) {
  const collected=[];
  const selected=ids.slice(0,maxContacts);
  for(const id of selected){
    try{
      const chat=await client.getChatById(id);
      if(!chat||chat.isGroup||chat.isChannel||typeof chat.fetchMessages!=='function')continue;
      const messages=await chat.fetchMessages({limit:maxMessages});
      collected.push(...fromOwner(messages,18));
    }catch{
      // A stale contact must never prevent safe startup.
    }
    if(collected.length>=26)break;
  }
  const chosen=fromOwner(collected.map(body=>({fromMe:true,type:'chat',body})),20);
  const content=chosen.join('\n');
  return {content,count:chosen.length,adequate:chosen.length>=5 && content.length>=180};
}
module.exports={approvedExample,fromOwner,learnOwnerStyle};
