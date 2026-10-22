'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {summarise,ACK_NAMES}=require('../receipts');
test('matches ACK to exact submitted message even when ACK event occurs earlier',()=>{
  const events=[
    {at:'2026-10-22T08:53:32.014Z',kind:'outgoing-ack-3',chatHash:'a',messageHash:'msg1'},
    {at:'2026-10-22T08:53:32.023Z',kind:'one-off-send-submitted',chatHash:'a',messageHash:'msg1'}
  ];
  const result=summarise(events);
  assert.equal(result.length,1);
  assert.equal(result[0].state,'read reported by WhatsApp');
  assert.equal(result[0].matchedBySameMessageId,true);
  assert.equal(result[0].recipientVerified,false);
  assert.equal(JSON.stringify(result).includes('msg1'),false);
});
test('does not mistake unrelated message acknowledgement as delivery',()=>{
  const result=summarise([
    {at:'t',kind:'outgoing-ack-3',messageHash:'other'},
    {at:'t',kind:'one-off-send-submitted',messageHash:'submitted'}
  ]);
  assert.equal(result[0].matchedBySameMessageId,false);
  assert.equal(result[0].state,'no acknowledgement observed');
  assert.equal(result[0].acknowledgement,null);
});
test('displays actual acknowledgement granularity without claiming recipient identity',()=>{
  const make=n=>summarise([{kind:'one-off-send-submitted',messageHash:'id'},
    {kind:'outgoing-ack-'+n,messageHash:'id'}])[0];
  assert.equal(make(1).state,'server accepted');
  assert.equal(make(2).state,'recipient device acknowledged');
  assert.equal(make(3).state,'read reported by WhatsApp');
  assert.equal(make(-1).state,'error');
  assert.equal(make(4).state,'played reported by WhatsApp');
  assert.equal(make(1).recipientVerified,false);
});
