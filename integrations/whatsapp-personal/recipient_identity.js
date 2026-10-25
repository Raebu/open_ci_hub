'use strict';
/** Identify self-chat using WhatsApp's own account ID when available.
 *  The result never treats missing profile data as verified external delivery.
 */
function canonicalId(value) {
  if(typeof value==='string')return value;
  if(!value || typeof value!=='object')return '';
  return value._serialized||value.$1||(value.user&&value.server?value.user+'@'+value.server:'');
}
function numberPart(value) {
  const id=canonicalId(value);
  const match=/^([0-9]{6,20})@(c\.us|lid)$/.exec(id);
  return match?match[1]:'';
}
function isSelfChat(digits,resolved,info) {
  const target=canonicalId(resolved);
  const myself=[info?.wid,info?.me].filter(Boolean);
  const entered=typeof digits==='string'?digits.replace(/^\+/,''):'';
  for(const entry of myself){
    const selfId=canonicalId(entry);
    if(target && selfId && target===selfId)return true;
    const pn=numberPart(entry);
    // A LID is not a phone number; never equate its numeric ID with a PN.
    if(pn && /@c\.us$/.test(selfId) && pn===entered)return true;
    if(pn && target.endsWith('@c.us') && selfId.endsWith('@c.us') &&
        pn===numberPart(target))return true;
  }
  return false;
}
module.exports={canonicalId,isSelfChat};
