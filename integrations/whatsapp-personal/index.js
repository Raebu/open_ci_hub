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
require('./scripts/ensure-compat').ensure();
const {Client,LocalAuth}=require('whatsapp-web.js');
const QR=require('qrcode-terminal');
const {chatAllowed,needsReview,safeReply,shortHash,validConfig}=require('./policy');
const {generateReply}=require('./ollama');
const {microReply}=require('./micro_reply');
const {learnOwnerStyle}=require('./style_learning');
const {parseDirectSend,parseExistingChatSend,verifiedExistingChat}=require('./direct_send');
const {isSelfChat}=require('./recipient_identity');
const {summarise,readAudit}=require('./receipts');
const {startManualControl}=require('./manual_control');

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
  const bootTimestamp=Math.floor(Date.now()/1000);
  // The Web client can replay recent notifications after reconnecting.
  // The audit stores hashed message IDs, never raw conversation content.
  try {
    if(fs.existsSync(auditPath))for(const event of readAudit(auditPath)){
      if(event.kind==='approved-inbound' && event.messageHash)seen.add(event.messageHash);
    }
  } catch(error){
    throw Error('Cannot verify previous inbound receipt IDs; refusing automatic startup');
  }
  let styleReady=false;
  const recentChats=[];
  const daily=todayAutoCounts();
  let connected=false;
  const controlServer=startManualControl({stateDir,client,isConnected:()=>connected,audit,shortHash,isSelfChat,note:console.log});
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
    if(!manual && (!fresh.autoSend||!styleReady))throw Error('Automatic sending is not enabled or owner style is not ready');
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
  async function sendOnceToNumber(digits,text){
    if(!connected)throw Error('WhatsApp is not connected');
    if(!/^\d{8,15}$/.test(digits) || !text || text.length>1200)
      throw Error('Invalid one-off recipient or message');
    const result=await client.getNumberId(digits);
    if(!result)throw Error('Recipient number is not registered on WhatsApp');
    const resolved=result._serialized ?? result.$1;
    if(typeof resolved!=='string'||!/^\d{6,20}@(c\.us|lid)$/.test(resolved))
      throw Error('WhatsApp returned an unrecognised recipient identifier');
    if(isSelfChat(digits,resolved,client.info))
      throw Error('This is your own WhatsApp account. Self-chat does not verify delivery to another phone; no message sent.');
    const message=await client.sendMessage(resolved,text,{sendSeen:false,waitUntilMsgSent:true});
    if(!message?.id?._serialized)
      throw Error('Send status unknown: no outgoing message ID returned; DO NOT retry automatically');
    audit('one-off-send-submitted',resolved,message.id._serialized);
    note('One-off message submitted to WhatsApp for '+digits+'. Delivery is not yet confirmed.');
  }
  async function sendToExistingChat(chatId,text) {
    if(!connected)throw Error('WhatsApp is not connected');
    const chats=await client.getChats();
    const target=verifiedExistingChat(chats,chatId);
    if(!target)throw Error('ID is not an existing individual chat. Run contacts or resolve first.');
    if(isSelfChat('',chatId,client.info))
      throw Error('Cannot use your own self-chat to verify message delivery. No message sent.');
    note('Verified individual chat: '+String(target.name||'Unnamed').slice(0,60)+
         ' ('+chatId+'). Sending one manual message.');
    const sent=await client.sendMessage(chatId,text,{sendSeen:false,waitUntilMsgSent:true});
    const id=sent?.id?._serialized;
    if(!id)throw Error('Unknown send status: no message ID; do not retry automatically');
    audit('one-off-send-submitted',chatId,id);
    note('One-off message submitted. Type outbox to inspect acknowledgements; intended recipient still must be checked.');
  }
  async function inspectRecipient(digits) {
    if(!connected)throw Error('WhatsApp is not connected');
    const result=await client.getNumberId(digits);
    if(!result){note('WhatsApp did not identify that number as a registered account. No message sent.');return;}
    const id=result._serialized??result.$1;
    if(typeof id!=='string'||!/^\d{6,20}@(c\.us|lid)$/.test(id))
      throw Error('WhatsApp returned an unsupported recipient ID');
    note('WhatsApp resolves the entered number to '+id+'. No message sent.');
    if(isSelfChat(digits,id,client.info))
      note('SELF-CHAT: this resolves to your linked account. A read receipt does not confirm another recipient received it; test with a separate consenting account.');
    else note('Recipient identity is not independently confirmed; verify the contact/chat before sending.');
    if(id.endsWith('@lid'))note('This is a LID identifier: compare its chat name/profile with the intended recipient before sending.');
    try {
      const chat=await client.getChatById(id);
      if(chat && !chat.isGroup)
        note('Known chat title: '+String(chat.name||'Unnamed').slice(0,65));
      else note('No existing one-to-one chat title available for verification.');
    } catch {note('Unable to retrieve profile name; the recipient identity remains unverified.');}
  }
  function showOutbox() {
    try{
      const items=summarise(readAudit(auditPath),5);
      if(!items.length){note('No outgoing messages recorded in the local audit.');return;}
      for(let index=0;index<items.length;index++){
        const item=items[index];
        note((index+1)+'. '+item.at+' — '+item.submitted+
           '; '+item.state+'; exact message-ID match='+item.matchedBySameMessageId+
           '; recipient identity NOT independently verified.');
      }
    }catch(error){note('Cannot inspect private outbox: '+String(error.message).slice(0,130));}
  }
  async function replyTo(id){
    pending.delete(id);
    try{
      const fresh=config();
      if(!fresh.autoSend||!styleReady||!chatAllowed(id,new Set(fresh.approvedChats)))return;
      const latest=history.get(id)||[];
      if(!latest.length||latest.at(-1).fromMe)return;
      const pendingText=latest.filter(m=>!m.fromMe).slice(-4).map(m=>m.body).join('\n');
      if(needsReview(pendingText)){
        audit('held-sensitive',id,pendingText);
        note('Owner review needed for '+shortHash(id)+' (sensitive context).');
        return;
      }
      const examples=style();
      // Immediate approved low-risk response without loading a large language
      // model into memory. This works during host memory pressure and never
      // claims knowledge of the owner's day, plans or personal circumstances.
      const lowRisk = microReply(pendingText,examples);
      const previous=latest.slice(0,-1);
      if(lowRisk && !previous.slice(-3).some(m=>needsReview(m.body))){
        await send(id,lowRisk);
        audit('auto-micro-reply',id,lowRisk);
        return;
      }
      const memory=fs.readFileSync('/proc/meminfo','utf8');
      const available=Number((/MemAvailable:\s*(\d+) kB/.exec(memory)||[])[1]||0);
      if(available<4096*1024) {
        audit('held-resource-pressure',id,pendingText);
        note('AI held: under 4 GiB available system memory. No message sent.');
        return;
      }
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
  async function prepareOwnerStyle(){
    const existing=style().trim();
    if(existing.length>=180){
      styleReady=true;
      note('Owner writing style loaded from private existing examples. No message text logged.');
      return;
    }
    note('Learning writing style locally from owner-sent messages in approved one-to-one chats.');
    const examples=await learnOwnerStyle(client,[...allowed]);
    if(!examples.adequate){
      styleReady=false;
      note('Insufficient owner-authored non-sensitive examples ('+examples.count+'); automatic sending held. Add approved examples to the private style file.');
      return;
    }
    if(fs.existsSync(stylePath)&&fs.lstatSync(stylePath).isSymbolicLink())
      throw Error('Symlinked style file refused');
    fs.writeFileSync(stylePath,examples.content+'\n',{mode:0o600,flag:'w'});
    styleReady=true;
    note('Owner writing style prepared locally from '+examples.count+' non-sensitive sent messages. Nothing sent.');
  }
  client.on('ready',()=>{
    connected=true;
    note('Connected. Type help, chats, or reply <number> <text>.');
    prepareOwnerStyle().catch(error=>{
      styleReady=false;
      note('Owner writing style unavailable; automatic sending held ('+String(error.name||'error')+').');
    });
  });
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
    if(!identifier)return;
    const receiptKey=shortHash(identifier);
    // Never automatically answer messages replayed from before this process started.
    if(Number(msg.timestamp||0)<bootTimestamp-30)return;
    if(seen.has(receiptKey))return;
    seen.add(receiptKey);
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
    if(enabled.autoSend && styleReady){
      pending.set(id,setTimeout(()=>{replyTo(id).catch(()=>{});},enabled.debounceMs));
    } else note('MANUAL: use chats then reply <number> <message>.');
  });
  rl.on('line',async line=>{
    const cmd=line.trim();
    if(cmd==='help'){note('contacts; chats; outbox — inspect sent status; resolve +<number> — verify recipient without sending; send +<number> <text>; send <existing-chat@lid> <text>; reply <index> <text>; status; quit');return;}
    if(cmd==='chats'){recentChats.forEach((id,i)=>note(i+': '+id));return;}
    if(cmd==='contacts'){
      if(!connected){note('WhatsApp is not connected.');return;}
      try {
        const chats=await client.getChats();
        const direct=chats.filter(x=>!x.isGroup && /^\d{6,20}@(c\.us|lid)$/.test(x.id?._serialized||''));
        for(const chat of direct.slice(0,15)) {
          note((chat.name||'Unnamed contact').slice(0,65)+': '+chat.id._serialized);
        }
        note(direct.length
          ? 'Showing up to 15 individual chats. Copy only approved IDs into the private config file and restart.'
          : 'No supported one-to-one chats returned. WhatsApp Web may still be synchronising.');
      } catch(e) {
        note(e?.message==='r'
          ? 'WhatsApp Web contact compatibility error (r). Restart this terminal app to load the patched library.'
          : 'Unable to list contacts: '+String(e?.message||e?.name||'unknown error').slice(0,140));
      }
      return;
    }
    if(cmd==='outbox'){showOutbox();return;}
    if(/^resolve\s+\+?[1-9]\d{7,14}$/.test(cmd)){
      try{await inspectRecipient(cmd.replace(/^resolve\s+\+?/,''));}
      catch(error){note('Recipient lookup failed: '+String(error.message).slice(0,140));}
      return;
    }
    if(cmd==='status'){note('Online: '+connected+'; Auto requested: '+config().autoSend+'; style ready: '+styleReady+'; allowlisted contacts: '+config().approvedChats.length);return;}
    if(cmd==='quit'){for(const t of pending.values())clearTimeout(t);rl.close();await client.destroy();process.exit(0);}
    if(cmd.startsWith('send ')){
      const one=parseDirectSend(cmd);
      const exact=parseExistingChatSend(cmd);
      if(!one && !exact){
        note('Use: send +447000000000 Your message text or send <existing-chat@lid> Your message text');
        return;
      }
      try{
        if(one)await sendOnceToNumber(one.digits,one.message);
        else await sendToExistingChat(exact.chatId,exact.message);
      }catch(error){note('One-off send not confirmed: '+String(error?.message||error).slice(0,180));}
      return;
    }
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
    try{controlServer.close();}catch{}
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
