'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {isSelfChat,canonicalId}=require('../recipient_identity');

test('self account number is recognised without sending a message',()=>{
  assert.equal(isSelfChat('447700900123','447700900123@c.us',
        {wid:{_serialized:'447700900123@c.us'}}),true);
  assert.equal(isSelfChat('447700900123','447700900123@lid',
        {wid:{$1:'447700900123@lid'}}),true);
  assert.equal(isSelfChat('447700900123',{_serialized:'998877665544@lid'},
        {wid:{_serialized:'998877665544@lid'}}),true);
});
test('separate recipient must not be misclassified as self',()=>{
  assert.equal(isSelfChat('447700900123','447700900123@c.us',
        {wid:{_serialized:'447700900111@c.us'}}),false);
  assert.equal(isSelfChat('447700900123','447700900123@c.us',
        {wid:{_serialized:'33445566@lid'}}),false);
  assert.equal(isSelfChat('447700900123','447700900123@c.us',{}),false);
});
test('identifier shapes are parsed deterministically without exposing secrets',()=>{
  assert.equal(canonicalId({_serialized:'123456789@c.us'}),'123456789@c.us');
  assert.equal(canonicalId({$1:'123456789@lid'}),'123456789@lid');
  assert.equal(canonicalId({user:'123456789',server:'c.us'}),'123456789@c.us');
  assert.equal(canonicalId(null),'');
});
