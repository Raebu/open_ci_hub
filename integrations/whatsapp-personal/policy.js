'use strict';
const {createHash} = require('node:crypto');
const ALLOWED_ID = /^\d{6,20}@(c\.us|lid)$/;
const RISK = [
  /\b(?:money|paid|pay(?:ment)?|loan|refund|invoice|bank|transfer|salary|debt|account number|sort code|password|otp|pin code|verification code)\b/i,
  /[£$€]/, /\b(?:lawyer|contract|agreement|court|police|legal|claim|lawsuit|sign|guarantee|promise)\b/i,
  /\b(?:health|hospital|doctor|diagnosis|medication|medicine|emergency|suicid|self.harm|hurt myself|pregnant)\b/i,
  /\b(?:relationship|divorce|break.up|i love you|sex|sexual|fight|argument|upset|angry|distressed)\b/i,
  /\b(?:urgent|asap|right now|immediately|come over|meet me|book|buy|where do you live|address|postcode)\b/i,
  /https?:\/\/|(?:\d{3,}[\s-]?){3,}/i,
  /\b(?:household|our future|the future|future together|discuss(?:ing)? (?:about )?(?:our )?future|plans? for (?:us|the family)|feel(?:ing)? (?:at )?peace|peace inside|feel(?:ing)? secure|reassur(?:e|ance)|words mean nothing|actions? (?:rather|instead) than words|what(?: is|'s) (?:gonna|going to) happen next)\b/i
];
function chatAllowed(id, approved) {
  return typeof id==='string' && ALLOWED_ID.test(id) && approved instanceof Set && approved.has(id);
}
function needsReview(text) {
  if(typeof text!=='string' || !text.trim() || text.length>600) return true;
  return RISK.some(r=>r.test(text));
}
function looksLikePlaceholder(text) {
  if(typeof text!=='string')return true;
  const body=text.trim().replace(/[.?!]+$/g,'').trim();
  if(!body)return true;
  return /^(?:(?:a |the )?(?:short |brief |quick |simple )*(?:natural |friendly |casual |warm |personal )*(?:whats\s?app |text |chat )?(?:response|reply|message|text)|(?:insert|write|your|draft|sample|example|placeholder)\s+(?:reply|message|response)(?:\s+here)?|(?:reply|message|response)\s+(?:goes here|here)|tbd|todo)$/i.test(body)
    || /\b(?:sample output|placeholder text|example response|as an ai language model|your reply here|short natural whatsapp response)\b/i.test(body)
    || /^\s*(?:\{|\[).*(?:needs_review|reply)\s*[":]/i.test(body);
}
function safeReply(text) {
  return typeof text==='string' && text.trim().length>=1 && text.length<=450 &&
    !looksLikePlaceholder(text) && !needsReview(text) &&
    !/\b(?:automated assistant|i am human|i'm definitely martin)\b/i.test(text);
}
function shortHash(value) {return createHash('sha256').update(String(value)).digest('hex').slice(0,24);}
function validConfig(cfg) {
  if(!cfg || typeof cfg!=='object' || Array.isArray(cfg) || cfg.schema!==1) throw Error('Invalid configuration schema');
  if(typeof cfg.autoSend!=='boolean') throw Error('autoSend must be boolean');
  if(!Array.isArray(cfg.approvedChats)||cfg.approvedChats.length>80||
     cfg.approvedChats.some(x=>typeof x!=='string'||!ALLOWED_ID.test(x)))
     throw Error('Only exact approved individual chat IDs are permitted');
  if(cfg.autoSend && !cfg.approvedChats.length)throw Error('Cannot auto-send without approved contacts');
  if(typeof cfg.ollamaModel!=='string'||!/^[a-zA-Z0-9._:-]{3,90}$/.test(cfg.ollamaModel))throw Error('Invalid Ollama model');
  if(!Number.isInteger(cfg.debounceMs)||cfg.debounceMs<5000||cfg.debounceMs>30000)throw Error('Debounce must be 5-30 seconds');
  if(!Number.isInteger(cfg.maxPerContactDay)||cfg.maxPerContactDay<1||cfg.maxPerContactDay>15)throw Error('Max daily replies must be 1-15');
  return cfg;
}
module.exports={chatAllowed,needsReview,safeReply,looksLikePlaceholder,shortHash,validConfig};
