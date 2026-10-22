'use strict';
/**
 * Opt-in, unofficial personal WhatsApp assistant.
 * Never starts automatically; no bulk/unknown/group messaging.
 * Chat replies use the owner PC's installed local Ollama model.
 * WHATSAPP'S TERMS MAY PROHIBIT THIS AND THE ACCOUNT CAN BE RESTRICTED.
 */
process.umask(0o077);
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const readline=require('node:readline');
const {Client,LocalAuth}=require('whatsapp-web.js');
const QR=require('qrcode-terminal');
const {chatAllowed,needsReview,safeReply,shortHash,validConfig}=require('./policy');
const {generateReply}=require('./ollama');

const configDir=path.join(os.homedir(),'.config','open-ci-hub');
const configPath=path.join(configDir,'personal-whatsapp.json');
const stylePath=path.join(configDir,'personal-whatsapp-style.txt');
const stateDir=path.join(os.homedir(),'.local','state','open-ci-hub-personal-whatsapp');
const auditPath=path.join(stateDir,'events.jsonl');

function privateDir(dir){
  if(fs.existsSync(dir) && fs.lstatSync(dir).isSymbolicLink())throw Error('Symlinked config/state directory refused');
  fs.mkdirSync(dir,{recursive:true,mode:0o700});
  const s=fs.statSync(dir);
  if(s.uid!==process.getuid()||(s.mode&0o077)!==0)throw Error('Private directory must be owned by current user with mode 0700');
}
function ownerFile(file,required=true){
  if(!fs.existsSync(file)){
    if(required)throw Error('Required private file not configured: '+path.basename(file));
    return null;
  }
  if(fs.lstatSync(file).isSymbolicLink())throw Error('Symlinked private file refused');
  const s=fs.statSync(file);
  if(!s.isFile()||s.uid!==process.getuid()||(s.mode&0o077)!==0||s.size>16_384)
    throw Error('Owner file must be private, regular and small: '+path.basename(file));
  return fs.readFileSync(file,'utf8');
}
function init(){
  privateDir(configDir);
  privateDir(stateDir);
  if(!fs.existsSync(configPath)){
    const template={schema:1,autoSend:false,approvedChats:[],ollamaModel:'qwen3.5:4b',
      debounceMs:10000,maxPerContactDay:6};
    fs.writeFileSync(configPath,JSON.stringify(template,null,2)+'\n',{mode:0o600,flag:'wx'});
  }
  if(!fs.existsSync(stylePath))fs.writeFileSync(stylePath,'',{mode:0o600,flag:'wx'});
  console.log('Private settings created or retained: '+configPath);
  console.log('Add exact WhatsApp one-to-one chat IDs to approvedChats and your OWN approved message examples to the private style file.');
  console.log('Set autoSend=true ONLY after pairing, reviewing policy and testing manually.');
}
function config(){return validConfig(JSON.parse(ownerFile(configPath)));}
function style(){return ownerFile(stylePath);}
function audit(kind,id='',receipt=''){
  if(fs.existsSync(auditPath) && fs.lstatSync(auditPath).isSymbolicLink())
    throw Error('Symlinked private audit log refused');
  const item={at:new Date().toISOString(),kind,chatHash:shortHash(id),messageHash:shortHash(receipt)};
  fs.appendFileSync(auditPath,JSON.stringify(item)+'\n',{mode:0o600});
}
function todayAutoCounts(){
  const counts=new Map();
  if(!fs.existsSync(auditPath))return counts;
  const stat=fs.statSync(auditPath);
  if(!stat.isFile() || (stat.mode & 0o077)!==0 || stat.size>2_000_000)
    throw Error('Private audit needs secure rotation; auto-send disabled');
  const today=new Date().toISOString().slice(0,10);
  for(const line of fs.readFileSync(auditPath,'utf8').split('\n')){
    if(!line.trim())continue;
    const item=JSON.parse(line);
    if(item.kind!=='auto-send-submitted'||!item.at.startsWith(today))continue;
    const key=item.chatHash+'|'+today;
    counts.set(key,(counts.get(key)||0)+1);
  }
  return counts;
}
function main(){
  privateDir(configDir);privateDir(stateDir);
  const configured=config();
  const allowed=new Set(configured.approvedChats);
  console.log('Personal WhatsApp service started in '+(configured.autoSend?'approved-contacts AUTO mode':'MANUAL mode')+'.');
  console.log('This is an unofficial WhatsApp Web client; your personal account can be restricted.');
  console.log('A QR code may appear ON THIS TERMINAL ONLY. Never share it or session files.');
  const client=new Client({
    authStrategy:new LocalAuth({clientId:'raeburn-personal',dataPath:path.join(stateDir,'session')}),
    puppeteer:{headless:true,executablePath:'/usr/bin/chromium',
      args:['--disable-dev-shm-usage']}
  });
  const history=new Map();
  const pending=new Map();
  const seen=new Set();
  const recentChats=[];
  const daily=todayAutoCounts();
  let connected=false;
  const rl=readline.createInterface({input:process.stdin,output:process.stdout});
  function note(text){process.stdout.write(text+'\n');}
  function dayCount(id){
    const key=shortHash(id)+'|'+new Date().toISOString().slice(0,10);
    return {key,current:daily.get(key)||0};
  }
  function rememberId(id){
    if(recentChats.includes(id))recentChats.splice(recentChats.indexOf(id),1);
    recentChats.unshift(id);
    if(recentChats.length>12)recentChats.pop();
  }
  function remember(id,msg,fromMe){
    const messages=history.get(id)||[];
    messages.push({body:String(msg||'').slice(0,600),fromMe});
    history.set(id,messages.slice(-12));
  }
  async function send(id,text,manual=false){
    const fresh=config();
    if(!connected)throw Error('WhatsApp is not ready');
    if(!chatAllowed(id,new Set(fresh.approvedChats)))throw Error('Recipient no longer on the approved allowlist');
    if(!manual && !fresh.autoSend)throw Error('Automatic sending is disabled');
    if(!manual && !safeReply(text))throw Error('Reply requires owner review');
    const count=dayCount(id);
    if(!manual && count.current>=fresh.maxPerContactDay)throw Error('Daily per-contact limit reached');
    const sent=await client.sendMessage(id,text,{sendSeen:false,waitUntilMsgSent:true});
    if(!sent?.id?._serialized)throw Error('WhatsApp did not return an outgoing message identifier');
    if(!manual)daily.set(count.key,count.current+1);
    remember(id,text,true);
    audit(manual?'manual-send-submitted':'auto-send-submitted',id,sent.id._serialized);
    note('WhatsApp send submitted for approved chat '+shortHash(id)+'. Delivery NOT confirmed.');
  }
  async function replyTo(id){
    pending.delete(id);
    try{
      const fresh=config();
      if(!fresh.autoSend||!chatAllowed(id,new Set(fresh.approvedChats)))return;
      const latest=history.get(id)||[];
      if(!latest.length||latest.at(-1).fromMe)return;
      const pendingText=latest.filter(m=>!m.fromMe).slice(-4).map(m=>m.body).join('\n');
      if(needsReview(pendingText)){
        audit('held-sensitive',id,pendingText);
        note('Owner review needed for '+shortHash(id)+' (sensitive context).');
        return;
      }
      const memory=fs.readFileSync('/proc/meminfo','utf8');
      const available=Number((/MemAvailable:\s*(\d+) kB/.exec(memory)||[])[1]||0);
      if(available<4096*1024) {
        audit('held-resource-pressure',id,pendingText);
        note('AI held: under 4 GiB available system memory. No message sent.');
        return;
      }
      const examples=style();
      const response=await generateReply({model:fresh.ollamaModel,sender:id,
        conversation:latest,examples});
      if(response.needsReview){
        audit('held-review',id,response.reply);
        note('AI proposed a response requiring review for '+shortHash(id)+'. Nothing sent.');
        return;
      }
      // Re-check after generation: manual replies/new inbound may supersede.
      const beforeSend=history.get(id)||[];
      if(beforeSend.length!==latest.length || beforeSend.at(-1).fromMe)return;
      await send(id,response.reply);
    }catch(error){
      audit('reply-error',id,error.name||'Error');
      note('Reply blocked for '+shortHash(id)+': '+error.message.replace(/[^a-zA-Z0-9 .,()-]/g,'').slice(0,160));
    }
  }
  client.on('qr',qr=>{
    if(!process.stdout.isTTY){
      note('WhatsApp QR login needed. Run npm start interactively on owner PC; QR deliberately excluded from system logs.');
      return;
    }
    note('Scan ONLY using WhatsApp → Linked Devices → Link a device:');
    QR.generate(qr,{small:true});
  });
  client.on('ready',()=>{connected=true;note('Connected. Type help, chats, or reply <number> <text>.');});
  client.on('authenticated',()=>note('Authenticated. Session kept privately under owner state directory.'));
  client.on('auth_failure',()=>{connected=false;note('Authentication failed; no sending possible.');});
  client.on('disconnected',why=>{connected=false;note('Disconnected; no sending possible.');});
  client.on('message_ack',(message,ack)=>{
    if(message?.fromMe && message?.id?._serialized){
      audit('outgoing-ack-'+String(ack),message.to||'',message.id._serialized);
    }
  });
  client.on('message',msg=>{
    const id=msg?.from;
    if(!id||msg.fromMe||!msg.body||msg.type!=='chat')return;
    if(!chatAllowed(id,allowed))return; // no group, status, unknown, unsanctioned
    const identifier=msg.id?._serialized;
    if(!identifier||seen.has(identifier))return;
    seen.add(identifier);
    if(seen.size>3500)seen.clear();
    rememberId(id);
    remember(id,msg.body,false);
    audit('approved-inbound',id,identifier);
    if(process.stdout.isTTY)
      note('[Approved chat '+recentChats.indexOf(id)+'] Received: '+String(msg.body).slice(0,300));
    else note('Approved chat '+shortHash(id)+' has a new message. No contents logged.');
    const current=pending.get(id);
    if(current)clearTimeout(current);
    const enabled=config();
    if(enabled.autoSend){
      pending.set(id,setTimeout(()=>{replyTo(id).catch(()=>{});},enabled.debounceMs));
    } else note('MANUAL: use chats then reply <number> <message>.');
  });
  rl.on('line',async line=>{
    const cmd=line.trim();
    if(cmd==='help'){note('contacts — list IDs for owner approval; chats — list approved incoming; reply <index> <text> — manual send; status; quit');return;}
    if(cmd==='chats'){recentChats.forEach((id,i)=>note(i+': '+id));return;}
    if(cmd==='contacts'){
      if(!connected){note('WhatsApp is not connected.');return;}
      try {
        const chats=await client.getChats();
        for(const chat of chats.filter(x=>!x.isGroup && /^\d{6,20}@(c\.us|lid)$/.test(x.id?._serialized||'')).slice(0,15)) {
          note((chat.name||'Unnamed contact').slice(0,65)+': '+chat.id._serialized);
        }
        note('Copy only personally approved IDs into the private config file and restart.');
      } catch(e) { note('Cannot list contacts: '+e.message); }
      return;
    }
    if(cmd==='status'){note('Online: '+connected+'; Auto: '+config().autoSend+'; allowlisted contacts: '+config().approvedChats.length);return;}
    if(cmd==='quit'){for(const t of pending.values())clearTimeout(t);rl.close();await client.destroy();process.exit(0);}
    const parsed=/^reply\s+(\d{1,2})\s+(.{1,1200})$/.exec(cmd);
    if(parsed){
      const id=recentChats[Number(parsed[1])];
      if(!id){note('Unknown approved recent chat index.');return;}
      try{await send(id,parsed[2],true);}catch(e){note('Manual send blocked: '+e.message);}
      return;
    }
    if(cmd)note('Unknown command. Type help. Incoming WhatsApp text never executes commands.');
  });
  let exiting=false;
  const shutdown=async()=>{
    if(exiting)return;
    exiting=true;
    for(const timer of pending.values())clearTimeout(timer);
    rl.close();
    try{await client.destroy();}catch{}
    process.exit(0);
  };
  process.once('SIGTERM',shutdown);
  process.once('SIGINT',shutdown);
  client.initialize().catch(e=>{note('WhatsApp startup failed: '+e.message);process.exitCode=1;});
}

if(process.argv[2]==='init')init();
else if(process.argv.length===2)main();
else{console.error('Use npm run init or npm start');process.exitCode=2;}
