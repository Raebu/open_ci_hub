'use strict';
const fs=require('node:fs');
const path=require('node:path');
const ACK_NAMES=Object.freeze({
  '-1':'error','0':'pending','1':'server accepted',
  '2':'recipient device acknowledged','3':'read acknowledgement reported by WhatsApp (recipient/phone not independently verified)','4':'played reported by WhatsApp'
});
const SENT=new Set(['one-off-send-submitted','manual-send-submitted','auto-send-submitted']);
function summarise(events,max=5){
  if(!Array.isArray(events))throw Error('Audit must be an array');
  const outgoing=events.filter(x=>SENT.has(x?.kind) && typeof x?.messageHash==='string');
  return outgoing.slice(-max).reverse().map(item=>{
    const acknowledgements=events.filter(x=>
      typeof x?.kind==='string' &&
      x.kind.startsWith('outgoing-ack-') &&
      x.messageHash===item.messageHash);
    const last=acknowledgements.at(-1);
    const latestAck=last ? Number(last.kind.slice('outgoing-ack-'.length)) : null;
    return {
      at:item.at,submitted:item.kind,
      acknowledgement:latestAck,
      state:last ? (ACK_NAMES[latestAck]||'unrecognised acknowledgement') : 'no acknowledgement observed',
      matchedBySameMessageId:!!last,
      recipientVerified:false // No contact identity verification in audit
    };
  });
}
function readAudit(file) {
  const stat=fs.lstatSync(file);
  if(!stat.isFile() || stat.isSymbolicLink() ||
      (stat.mode&0o077)!==0 || stat.size>2_000_000)
    throw Error('Private audit log needs owner review');
  return fs.readFileSync(file,'utf8').split('\n').filter(Boolean).map(x=>JSON.parse(x));
}
module.exports={summarise,readAudit,ACK_NAMES};
