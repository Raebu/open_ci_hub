'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawnSync}=require('node:child_process');
const {sha256,ORIGINAL,FIXED}=require('../scripts/ensure-compat');

test('reinstalls patch from exact pristine library and rejects unknown modifications',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'oci-wa-compat-test-'));
  try{
    const localTarget=path.join(root,'node_modules/whatsapp-web.js/src/util/Injected/Utils.js');
    const localPackage=path.join(root,'node_modules/whatsapp-web.js/package.json');
    const localPatch=path.join(root,'patches/whatsapp-web.js+1.34.7.patch');
    const localHelper=path.join(root,'scripts/ensure-compat.js');
    for(const item of [localTarget,localPatch,localHelper])
      fs.mkdirSync(path.dirname(item),{recursive:true,mode:0o700});
    fs.copyFileSync(path.join(__dirname,'../node_modules/whatsapp-web.js/src/util/Injected/Utils.js'),localTarget);
    fs.writeFileSync(localPackage,'{"version":"1.34.7"}');
    fs.copyFileSync(path.join(__dirname,'../patches/whatsapp-web.js+1.34.7.patch'),localPatch);
    fs.copyFileSync(path.join(__dirname,'../scripts/ensure-compat.js'),localHelper);
    let run=spawnSync('patch',['--batch','--reverse','-p1','-i',localPatch],
      {cwd:root,encoding:'utf8',timeout:10000});
    assert.equal(run.status,0,run.stderr+' '+run.stdout);
    assert.equal(sha256(fs.readFileSync(localTarget)),ORIGINAL);
    run=spawnSync(process.execPath,[localHelper],{cwd:root,encoding:'utf8',timeout:10000});
    assert.equal(run.status,0,run.stderr);
    assert.equal(sha256(fs.readFileSync(localTarget)),FIXED);
    run=spawnSync(process.execPath,[localHelper],{cwd:root,encoding:'utf8',timeout:10000});
    assert.equal(run.status,0,'Application is idempotent');
    fs.appendFileSync(localTarget,'\n// unexpected third-party modification\n');
    run=spawnSync(process.execPath,[localHelper],{cwd:root,encoding:'utf8',timeout:10000});
    assert.notEqual(run.status,0,'Unknown dependency content must fail closed');
  }finally{
    fs.rmSync(root,{recursive:true,force:true});
  }
});
