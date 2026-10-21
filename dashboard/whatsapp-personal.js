/* Open CI Hub — personal WhatsApp composer.
 * Personal data never leaves the browser until the owner opens an official
 * WhatsApp click-to-chat URL; WhatsApp itself handles final sending.
 * No reverse-engineered WhatsApp Web protocols, tokens, bulk sending or bot.
 */
(function (root) {
  'use strict';
  const MAX_MESSAGE = 2400;
  const MAX_CONTEXT = 500;
  const CONVERSATION = Object.freeze({
    hello: {warm:'I hope you are doing well.',casual:'Hope you’re well!',professional:'I hope you are well.',brief:'Hope you’re well.'},
    thanks: {warm:'Thank you so much. I really appreciate it.',casual:'Thanks so much, really appreciate it!',professional:'Thank you for your help. I appreciate it.',brief:'Thanks so much!'},
    followup: {warm:'I wanted to follow up with you.',casual:'Just checking back in!',professional:'I am following up on our earlier conversation.',brief:'Quick follow-up.'},
    checkin: {warm:'I was thinking of you and wanted to see how you are.',casual:'How are things going?',professional:'I wanted to check how things are going.',brief:'How are you?'},
    apology: {warm:'I’m sorry about what happened.',casual:'Really sorry about that.',professional:'I apologise for what happened.',brief:'I’m sorry.'}
  });
  function normalisePhone(input, defaultCountry='44') {
    if (typeof input !== 'string' || input.length>40) throw new Error('Enter a valid international phone number.');
    const raw=input.trim();
    if (!/^\+?[0-9()\s.-]+$/.test(raw) || !/[0-9]/.test(raw)) throw new Error('Phone number contains unsupported characters.');
    let digits=raw.replace(/[^0-9]/g,'');
    if (digits.startsWith('00')) digits=digits.slice(2);
    else if (digits.startsWith('0')) {
      if (!/^[0-9]{1,3}$/.test(defaultCountry)) throw new Error('Enter the full international phone number.');
      digits=defaultCountry+digits.slice(1);
    } else if (!raw.startsWith('+') && defaultCountry==='44' && /^7[0-9]{9}$/.test(digits)) {
      digits='44'+digits;
    }
    if (!/^[1-9][0-9]{7,14}$/.test(digits)) throw new Error('Use a complete international phone number, including country code.');
    return digits;
  }
  function messageLink(phone, message) {
    const number=normalisePhone(phone);
    if (typeof message!=='string' || !message.trim()) throw new Error('Write a message before opening WhatsApp.');
    if (message.length>MAX_MESSAGE) throw new Error('Message is too long for a reliable click-to-chat link.');
    return 'https://wa.me/'+number+'?text='+encodeURIComponent(message.trim());
  }
  function draft({name='',subject='',intent='hello',tone='warm'}={}) {
    const source=CONVERSATION[intent];
    if (!source) throw new Error('Choose an available message purpose.');
    if (!['warm','casual','professional','brief'].includes(tone)) throw new Error('Choose an available writing style.');
    const safeName=typeof name==='string'?name.trim().slice(0,80):'';
    const safeSubject=typeof subject==='string'?subject.trim().slice(0,MAX_CONTEXT):'';
    const opening=safeName?('Hi '+safeName+','):'Hi,';
    const body=source[tone];
    const finalLine= tone==='professional'?'Kind regards,':tone==='brief'?'Thanks!':tone==='casual'?'Speak soon!':'Take care,';
    return [opening,body+(safeSubject?' '+safeSubject:''),finalLine].join('\n\n');
  }
  const api={normalisePhone,messageLink,draft,MAX_MESSAGE};
  if (typeof module==='object' && module.exports) module.exports=api;
  if (!root || !root.document) return;
  const $=id=>root.document.getElementById(id);
  function notify(text,bad=false) {
    const status=$('whatsapp-status');
    if (!status) return;
    status.textContent=text;
    status.classList.toggle('error',bad);
  }
  function buildDraft() {
    try {
      const suggested=draft({name:$('whatsapp-name').value,
        subject:$('whatsapp-context').value,intent:$('whatsapp-intent').value,
        tone:$('whatsapp-tone').value});
      $('whatsapp-suggestion').value=suggested;
      notify('Suggestion created. Your current message has not been overwritten.');
    } catch (err) { notify(err.message,true); }
  }
  function useSuggestion() {
    const suggestion=$('whatsapp-suggestion').value;
    if (!suggestion) return notify('Create a suggestion first.',true);
    if ($('whatsapp-message').value.trim() &&
        !root.confirm('Replace your current draft with this suggestion?')) return;
    $('whatsapp-message').value=suggestion;
    notify('Draft updated. Nothing has been sent.');
    $('whatsapp-message').focus();
  }
  async function copyDraft() {
    const message=$('whatsapp-message').value.trim();
    if (!message) return notify('Write a message first.',true);
    try {
      await root.navigator.clipboard.writeText(message);
      notify('Message copied. Nothing has been sent.');
    } catch (err) {notify('Clipboard unavailable here. Select and copy the message manually.',true);}
  }
  function openWhatsApp() {
    const phone=$('whatsapp-phone').value;
    const name=$('whatsapp-name').value.trim();
    const message=$('whatsapp-message').value;
    try {
      const link=messageLink(phone,message);
      const destination=name?name+' ('+normalisePhone(phone)+')':normalisePhone(phone);
      if (!root.confirm('Open WhatsApp for '+destination+'?\n\nReview the recipient and press Send inside WhatsApp. Open CI Hub will NOT send automatically.')) return;
      root.open(link,'_blank','noopener,noreferrer');
      notify('WhatsApp was requested with your prefilled message. NOT SENT — if a tab opened, check the recipient and press Send in WhatsApp.');
    } catch(err) {notify(err.message,true);}
  }
  root.document.addEventListener('DOMContentLoaded',()=>{
    if (!$('whatsapp-compose')) return;
    root.document.addEventListener('open-ci-logout',()=>{
      for (const id of ['whatsapp-name','whatsapp-phone','whatsapp-context','whatsapp-message','whatsapp-suggestion']) $(id).value='';
      notify('Personal message fields cleared on sign-out. Nothing was sent.');
    });
    $('whatsapp-write').addEventListener('click',buildDraft);
    $('whatsapp-apply').addEventListener('click',useSuggestion);
    $('whatsapp-copy').addEventListener('click',copyDraft);
    $('whatsapp-open').addEventListener('click',openWhatsApp);
    $('whatsapp-clear').addEventListener('click',()=>{
      if (!root.confirm('Clear your personal WhatsApp drafts and contact fields on this page?')) return;
      for (const id of ['whatsapp-name','whatsapp-phone','whatsapp-context','whatsapp-message','whatsapp-suggestion']) $(id).value='';
      notify('Draft and recipient cleared. No message was sent.');
    });
  });
})(typeof window==='undefined'?null:window);
