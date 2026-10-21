'use strict';
const assert=require('node:assert/strict');
const {test}=require('node:test');
const {normalisePhone,messageLink,draft}=require('../dashboard/whatsapp-personal.js');

test('UK personal number normalises national and international formats',()=>{
  assert.equal(normalisePhone('07700 900123'),'447700900123');
  assert.equal(normalisePhone('+44 (7700) 900123'),'447700900123');
  assert.equal(normalisePhone('0044 7700 900123'),'447700900123');
  assert.equal(normalisePhone('7700900123'),'447700900123');
});

test('international E.164-style number is preserved',()=>{
  assert.equal(normalisePhone('+1 (415) 555-0123'),'14155550123');
  assert.equal(normalisePhone('+977 980 123 4567'),'9779801234567');
});

test('rejects malformed personal numbers and injection characters',()=>{
  for(const value of ['', 'abc','+44;alert(1)','<script>','0','+1', '44/7700900123']){
    assert.throws(()=>normalisePhone(value));
  }
});

test('official link contains encoded edit-ready text, not a send request',()=>{
  const link=messageLink('07700 900123','Hello\nThank you & take care');
  assert.equal(link,'https://wa.me/447700900123?text=Hello%0AThank%20you%20%26%20take%20care');
  assert.equal(new URL(link).hostname,'wa.me');
  assert.equal(new URL(link).searchParams.get('text'),'Hello\nThank you & take care');
});

test('empty and overly long messages never produce a link',()=>{
  assert.throws(()=>messageLink('07700 900123','  '));
  assert.throws(()=>messageLink('07700 900123','x'.repeat(2401)));
});

test('personal writing creates a suggestion without contacting WhatsApp',()=>{
  const result=draft({name:'Sarah',subject:'Thank you for yesterday.',intent:'thanks',tone:'warm'});
  assert.match(result,/Hi Sarah,/);
  assert.match(result,/Thank you so much/);
  assert.match(result,/Thank you for yesterday/);
});

test('unsupported tone and purpose are rejected',()=>{
  assert.throws(()=>draft({intent:'bulk_send',tone:'warm'}));
  assert.throws(()=>draft({intent:'hello',tone:'malicious'}));
});
