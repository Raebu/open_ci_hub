'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {execFileSync}=require('node:child_process');

test('pinned 1.34.7 compatibility patch is installed and persisted',()=>{
  const pkg=require('../package.json');
  assert.equal(pkg.dependencies['whatsapp-web.js'],'1.34.7');
  assert.equal(pkg.dependencies['patch-package'],undefined);
  assert.ok(pkg.scripts.start.includes('scripts/ensure-compat.js'));
  assert.ok(pkg.scripts.postinstall.includes('scripts/ensure-compat.js'));
  const patch=fs.readFileSync(path.join(__dirname,'../patches/whatsapp-web.js+1.34.7.patch'),'utf8');
  const code=fs.readFileSync(path.join(__dirname,'../node_modules/whatsapp-web.js/src/util/Injected/Utils.js'),'utf8');
  assert.match(patch,/getMsgKeyId/);
  assert.match(code,/window\.WWebJS\.getMsgKeyId/);
  assert.match(code,/Promise\.allSettled/);
  assert.doesNotMatch(code,/\.Msg\.get\(newMsgKey\._serialized\)/);
  assert.doesNotMatch(code,/\.Msg\.get\(chat\.lastReceivedKey\._serialized\)/);
});
test('actual injected id resolution supports both naming formats',()=>{
  const code=fs.readFileSync(path.join(__dirname,'../node_modules/whatsapp-web.js/src/util/Injected/Utils.js'),'utf8');
  const match=code.match(/window\.WWebJS\.getMsgKeyId = \(key\) =>\s*([^;]+);/);
  assert.ok(match,'Missing compatibility resolver');
  const resolve=new Function('key','return '+match[1]);
  assert.equal(resolve({_serialized:'old-id'}),'old-id');
  assert.equal(resolve({$1:'new-id'}),'new-id');
  assert.equal(resolve({_serialized:'preferred',$1:'fallback'}),'preferred');
  assert.equal(resolve(null),undefined);
  assert.equal(resolve({}),undefined);
});
