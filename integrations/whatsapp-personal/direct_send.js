'use strict';
const INTERNATIONAL=/^\+?[1-9][0-9]{7,14}$/;
function parseDirectSend(input) {
  if(typeof input!=='string')return null;
  const match=/^send\s+(\+?[1-9][0-9]{7,14})\s+([\s\S]{1,1200})$/.exec(input.trim());
  if(!match)return null;
  const digits=match[1].replace('+','');
  const message=match[2].trim();
  if(!INTERNATIONAL.test(match[1])||!message||message.length>1200)return null;
  return {digits,message};
}
function parseExistingChatSend(input) {
  if(typeof input!=='string')return null;
  const match=/^send\s+(\d{6,20}@(c\.us|lid))\s+([\s\S]{1,1200})$/.exec(input.trim());
  if(!match)return null;
  const message=match[3].trim();
  if(!message || message.length>1200)return null;
  return {chatId:match[1],message};
}
function verifiedExistingChat(chats,chatId) {
  if(!Array.isArray(chats) || typeof chatId!=='string')return null;
  return chats.find(chat=>
    chat?.id?._serialized===chatId &&
    chat.isGroup===false &&
    chat.isChannel!==true &&
    chat.isReadOnly!==true) || null;
}
module.exports={parseDirectSend,parseExistingChatSend,verifiedExistingChat};
