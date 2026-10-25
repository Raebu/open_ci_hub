'use strict';
const net=require('node:net');
const fs=require('node:fs');
const path=require('node:path');
function startManualControl({stateDir,client,isConnected,audit,shortHash,isSelfChat,note}){
  const socketPath=path.join(stateDir,'manual-control.sock');
  if(fs.existsSync(socketPath)){
    if(!fs.lstatSync(socketPath).isSocket())throw Error('Control socket path is not a socket');
    fs.unlinkSync(socketPath);
  }
  const server=net.createServer(conn=>{
    let data='',processed=false;
    conn.setTimeout(40000,()=>conn.destroy());
    conn.on('error',()=>{});
    conn.on('data',chunk=>{
      if(processed)return;
      data+=chunk.toString('utf8');
      if(data.length>1800){processed=true;conn.end(JSON.stringify({ok:false,error:'Request too large'})+'\n');return;}
      const newline=data.indexOf('\n');
      if(newline<0)return;
      processed=true;
      (async()=>{
        try {conn.end(JSON.stringify({ok:true,...await handle(JSON.parse(data.slice(0,newline)))})+'\n');}
        catch(err){conn.end(JSON.stringify({ok:false,error:String(err.message||err).slice(0,160)})+'\n');}
      })();
    });
  });
  server.on('error',err=>note('WhatsApp local control error: '+err.message));
  server.listen(socketPath,()=>{
    fs.chmodSync(socketPath,0o600);
    note('WhatsApp manual control socket ready');
  });
  async function handle(req){
    if(!req||!['lookup','send'].includes(req.type))throw Error('Unsupported command');
    if(!isConnected())throw Error('WhatsApp is not connected');
    const digits=String(req.number||'').replace(/^\+/,'');
    if(!/^\d{8,15}$/.test(digits))throw Error('Invalid international number');
    const result=await client.getNumberId(digits);
    if(!result)throw Error('Number not registered on WhatsApp');
    const id=result._serialized??result.$1;
    if(typeof id!=='string'||!/^\d{6,20}@(c\.us|lid)$/.test(id))
      throw Error('Unrecognised WhatsApp ID');
    if(isSelfChat(digits,id,client.info))throw Error('Cannot send to self chat');
    const chats=await client.getChats();
    const chat=chats.find(x=>x?.id?._serialized===id&&!x.isGroup&&!x.isChannel&&!x.isReadOnly);
    const name=String(chat?.name||'');
    if(req.type==='lookup')return {chatId:id,chatName:name,existingChat:!!chat};
    const expected=String(req.expectedName||'').trim();
    if(!chat||!expected||expected.length>60||
       !name.toLowerCase().includes(expected.toLowerCase()))
      throw Error('Intended recipient identity not confirmed in existing chat; no message sent');
    const message=String(req.message||'').trim();
    if(!message||message.length>500)throw Error('Bad message length');
    const sent=await client.sendMessage(id,message,{sendSeen:false,waitUntilMsgSent:true});
    if(!sent?.id?._serialized)throw Error('Send status unknown, do not retry');
    audit('one-off-send-submitted',id,sent.id._serialized);
    return {submitted:true,chatName:name,receiptHash:shortHash(sent.id._serialized),deliveryConfirmed:false};
  }
  return server;
}
module.exports={startManualControl};
