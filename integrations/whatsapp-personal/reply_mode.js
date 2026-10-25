'use strict';
/** Owner-local emergency pause/resume; no WhatsApp messages sent here. */
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {validConfig}=require('./policy');
function update(mode, base=path.join(os.homedir(),'.config','open-ci-hub')){
  if(!['pause','resume','status'].includes(mode))throw Error('Use pause, resume or status.');
  const settings=path.join(base,'personal-whatsapp.json');
  const styleFile=path.join(base,'personal-whatsapp-style.txt');
  if(fs.lstatSync(settings).isSymbolicLink()||fs.lstatSync(base).isSymbolicLink())
    throw Error('Symlinked owner configuration refused');
  const st=fs.statSync(settings);
  if(st.uid!==process.getuid() || (st.mode&0o077)!==0)throw Error('Private configuration permissions invalid');
  const config=validConfig(JSON.parse(fs.readFileSync(settings,'utf8')));
  if(mode==='status'){
    return {mode:config.autoSend?'automatic':'paused',approved:config.approvedChats.length};
  }
  if(mode==='resume'){
    if(config.approvedChats.length===0)throw Error('No approved contacts; refusing auto-send');
    if(fs.lstatSync(styleFile).isSymbolicLink())throw Error('Symlinked style file refused');
    const style=fs.readFileSync(styleFile,'utf8').trim();
    if(style.length<180)throw Error('No sufficient owner-authored writing style; refusing auto-send');
  }
  config.autoSend=mode==='resume';
  const tmp=path.join(base,'.wa-config-'+process.pid+'.tmp');
  let fd;
  try {
    fd=fs.openSync(tmp,'wx',0o600);
    fs.writeSync(fd,JSON.stringify(config,null,2)+'\n');
    fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;
    fs.renameSync(tmp,settings);
  }finally{
    if(fd!==undefined)fs.closeSync(fd);
    if(fs.existsSync(tmp))fs.unlinkSync(tmp);
  }
  return {mode:config.autoSend?'automatic':'paused',approved:config.approvedChats.length};
}
if(require.main===module){
  try{console.log(JSON.stringify(update(process.argv[2]||'status')));}
  catch(error){console.error(error.message);process.exitCode=1;}
}
module.exports={update};
