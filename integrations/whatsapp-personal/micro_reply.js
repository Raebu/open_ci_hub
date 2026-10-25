'use strict';
const {needsReview,safeReply}=require('./policy');

/*
 * Deterministic, owner-style-aware micro-replies while local Ollama is
 * resource constrained. Never answers questions or invents owner facts.
 * Exact-message matches only; contacts/allowlisting happen in main runner.
 */
function microReply(message,styleExamples='') {
  if(typeof message!=='string' || !message.trim() ||
      needsReview(message)||message.length>45)return null;
  const normalized=message.trim().toLowerCase().replace(/[!. ]+$/g,'').replace(/\s+/g,' ');
  const style=String(styleExamples).toLowerCase();
  const greet = /\bhiya\b/.test(style) ? 'Hiya!' : (/\bhey\b/.test(style) ? 'Hey!' : 'Hi!');
  const cases = new Map([
    ['hi',greet],['hello',greet],['hey',greet],['hiya',greet],
    ['good morning','Morning!'],['morning','Morning!'],
    ['good evening','Evening!'],['evening','Evening!'],
    ['good night','Night!'],['night','Night!'],
    ['thanks',/\bno worries\b/.test(style) ? 'No worries!' : 'You’re welcome!'],
    ['thank you',/\bno worries\b/.test(style) ? 'No worries!' : 'You’re welcome!'],
    ['cheers',/\bno worries\b/.test(style) ? 'No worries!' : 'You’re welcome!'],
    ['ok','Okay!'],['okay','Okay!'],
    ['alright','Alright!'],['sounds good','Sounds good!'],
    ['cool','Nice!'],['great','Great!']
  ]);
  const reply=cases.get(normalized)||null;
  return reply && safeReply(reply) ? reply : null;
}
module.exports={microReply};
