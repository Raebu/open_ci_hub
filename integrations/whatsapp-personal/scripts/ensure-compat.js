'use strict';
/* Version-checked compatibility patch. No LocalAuth/session/chat access. */
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const {spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');
const packagePath=path.join(root,'node_modules/whatsapp-web.js/package.json');
const target=path.join(root,'node_modules/whatsapp-web.js/src/util/Injected/Utils.js');
const patchFile=path.join(root,'patches/whatsapp-web.js+1.34.7.patch');
const ORIGINAL='0d0f88565f481dbfeb9493b04b24033a2cb60f5fd2fd0e84e543b461d98878fe';
const FIXED='14bf863748f8c83f5fc77dde3dab41718790d009afcd8d02779c9ffcb1c4dd98';
const sha256=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
function ensure() {
  if ([packagePath,target,patchFile].some(file=>fs.lstatSync(file).isSymbolicLink()))
    throw Error('Symlinked WhatsApp library or compatibility patch refused');
  if (JSON.parse(fs.readFileSync(packagePath,'utf8')).version!=='1.34.7')
    throw Error('Unsupported WhatsApp Web library version; review patch before upgrade');
  const before=sha256(fs.readFileSync(target));
  if (before===FIXED) return {patched:true,changed:false};
  if (before!==ORIGINAL) throw Error('Unexpected WhatsApp library content; refusing modification');
  const run=spawnSync('patch',['--batch','--forward','-p1','-i',patchFile],
      {cwd:root,encoding:'utf8',timeout:12000,stdio:['ignore','pipe','pipe']});
  if(run.error || run.status!==0)throw Error('Cannot apply reviewed WhatsApp compatibility patch');
  if(sha256(fs.readFileSync(target))!==FIXED)
    throw Error('Patched WhatsApp library checksum mismatch; refusing to start');
  return {patched:true,changed:true};
}
if(require.main===module){
  try{
    const result=ensure();
    console.log(result.changed?'WhatsApp compatibility patch applied.':'WhatsApp compatibility patch verified.');
  } catch(error) {
    console.error(error.message);
    process.exitCode=1;
  }
}
module.exports={ensure,sha256,ORIGINAL,FIXED};
