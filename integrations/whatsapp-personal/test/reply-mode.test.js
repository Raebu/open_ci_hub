'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {update}=require('../reply_mode');
test('pause/resume requires approved chats and real style examples',()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'oci-wa-mode-'));
  fs.chmodSync(dir,0o700);
  const config=path.join(dir,'personal-whatsapp.json');
  const style=path.join(dir,'personal-whatsapp-style.txt');
  const cfg={schema:1,autoSend:false,approvedChats:['447700900123@c.us'],
    ollamaModel:'qwen3.5:4b',debounceMs:10000,maxPerContactDay:6};
  try{
    fs.writeFileSync(config,JSON.stringify(cfg),{mode:0o600});
    fs.writeFileSync(style,'',{mode:0o600});
    assert.deepEqual(update('status',dir),{mode:'paused',approved:1});
    assert.throws(()=>update('resume',dir),/writing style/);
    fs.writeFileSync(style,'Thanks for getting back to me, really appreciate it. Hope everything is going okay. Speak soon.\n'.repeat(3));
    assert.equal(update('resume',dir).mode,'automatic');
    assert.equal(JSON.parse(fs.readFileSync(config)).autoSend,true);
    assert.equal(update('pause',dir).mode,'paused');
    assert.equal(JSON.parse(fs.readFileSync(config)).autoSend,false);
    assert.equal(fs.statSync(config).mode&0o777,0o600);
  } finally {fs.rmSync(dir,{recursive:true,force:true});}
});
