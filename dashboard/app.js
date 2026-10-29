'use strict';
const $ = id => document.getElementById(id);
const state = {mode:'desktop', ready:false, pipelineBusy:false, pipelineSelected:false, statuses:new Map(), failureCache:new Map(), failurePromises:new Map()};
const show = (id,on) => $(id).classList.toggle('hidden',!on);
function notice(msg,error=false){const x=$('notice');x.textContent=msg;x.classList.toggle('error',error);show('notice',!!msg)}
async function api(path, body){
  const r=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','X-Open-CI-Request':'1'}:{},credentials:'same-origin',body:body?JSON.stringify(body):undefined});
  let data;try{data=await r.json()}catch{throw new Error('Unexpected server response.')}
  if(!r.ok){throw Object.assign(new Error(data.error||'Request failed.'),{status:r.status,data});}
  return data;
}
function el(tag,text,className=''){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
function btn(text,fn,kind='secondary'){let b=el('button',text,'btn '+kind);b.type='button';b.addEventListener('click',async()=>{b.disabled=true;try{await fn();}catch(e){notice(e.message,true)}finally{b.disabled=false}});return b;}
function renderProviders(data){const root=$('cards');root.replaceChildren();for(const p of data.providers){
  const card=el('article',undefined,'provider-card'),top=el('div',undefined,'provider-top');
  const icon=el('div',({github:'GH',gitlab:'GL',circleci:'CI'})[p.id],'provider-icon');const flag=el('div',p.authenticated?'● Signed in':'○ Not connected','state'+(p.authenticated?' connected':''));top.append(icon,flag);card.append(top,el('h3',p.id==='circleci'?'CircleCI':p.id==='gitlab'?'GitLab':'GitHub'),el('p',`Runner: ${p.runner}`));
  const controls=el('div',undefined,'controls');
  if(state.mode==='desktop')controls.append(btn(p.authenticated?'Reconnect':'Sign in',async()=>{const out=await api('/api/provider/login',{provider:p.id});notice(out.message);},'primary'));
  if(p.configured&&state.role!=='viewer'){controls.append(btn('Start',async()=>{notice((await api('/api/runner',{provider:p.id,action:'start'})).message);await refresh();}));controls.append(btn('Stop',async()=>{notice((await api('/api/runner',{provider:p.id,action:'stop'})).message);await refresh();}));}
  if(p.configured && p.id==='gitlab' && state.role==='owner'){
    controls.append(btn('Add project',async()=>notice((await api('/api/runner/add-gitlab',{scope:'project'})).message)));
    controls.append(btn('Add group',async()=>notice((await api('/api/runner/add-gitlab',{scope:'group'})).message)));
  }
  if(!p.configured && p.id==='gitlab' && state.role==='owner')
    controls.append(btn('Register group runner',async()=>{
      const result=await api('/api/runner/group-setup',{});notice(result.message);
    }));
  if(!p.configured){
    card.append(el('p','Register a runner from the owner PC; the guided setup opens in a terminal.','small'));
    if(state.role==='owner') controls.append(btn('Register runner',async()=>{
      const out=await api('/api/runner/setup',{provider:p.id});notice(out.message);
    }));
  }
  card.append(controls);root.append(card);
}}
function renderDevices(data){let root=$('device-users');root.replaceChildren();if(state.mode!=='desktop'){root.append(el('p',`Signed in as ${data.identity}`,'small'));return;}
  const pending=data.pending_users||[],approved=data.approved_users||{};
  root.append(el('h3',`Requests (${pending.length})`));const a=el('div',undefined,'users');
  if(!pending.length)a.append(el('p','No pending devices.','small'));
  for(const u of pending){let row=el('div',undefined,'user');row.append(el('span',u),btn('View only',async()=>{await api('/api/mobile/approve',{user:u,role:'viewer'});notice('Viewer approved.');await refresh();},'primary'),btn('Allow management',async()=>{if(!confirm('Give '+u+' permission to create repositories and control runners?'))return;await api('/api/mobile/approve',{user:u,role:'editor'});notice('Editor approved.');await refresh();}));a.append(row)}root.append(a);
  root.append(el('h3',`Approved (${Object.keys(approved).length})`));const b=el('div',undefined,'users');
  if(!Object.keys(approved).length)b.append(el('p','No mobile users approved.','small'));
  for(const [u,role] of Object.entries(approved)){let row=el('div',undefined,'user');row.append(el('span',`${u} (${role})`),btn('Revoke',async()=>{if(!confirm('Revoke access for '+u+'?'))return;await api('/api/mobile/revoke',{user:u});notice('Access revoked.');await refresh();}));b.append(row)}root.append(b);
}
async function refresh(){
  let data;
  try {
    data = await api('/api/overview');
  } catch (error) {
    state.ready = false;
    show('workspace', false);
    show('access', true);
    show('signout', false);
    if (error.status === 401 && error.data?.mobile) {
      $('access-title').textContent = 'Approve this mobile device';
      $('access-detail').textContent = 'Signed into Tailscale as ' + error.data.identity + '. Request permission, then approve it from the desktop dashboard.';
      show('pair', true);
      notice('', false);
    } else if (error.status === 401 && location.hostname.endsWith('.ts.net')) {
      $('access-title').textContent = 'Private mobile identity unavailable';
      $('access-detail').textContent = 'Connect this device to the same Tailscale network and check that Tailscale Serve is forwarding your verified identity.';
      show('pair', false);
      notice('', false);
    } else if (error.status === 401) {
      $('access-title').textContent = 'Open CI Hub desktop session required';
      $('access-detail').textContent = 'Launch python hub.py dashboard on your PC. It opens a private administrator link in your browser.';
      show('pair', false);
      notice('', false);
    } else {
      $('access-title').textContent = 'Unable to connect to Open CI Hub';
      $('access-detail').textContent = 'The dashboard could not check your session. Verify the private connection and try again.';
      show('pair', false);
      notice(error.message, true);
    }
    return false;
  }
  try {
    state.mode = data.mode;
    state.role = data.role;
    $('mode').textContent = data.mode === 'mobile' ? 'Secure mobile session' : 'Local desktop session';
    show('access', false);
    show('workspace', true);
    show('repositories', data.role !== 'viewer');
    show('onboarding', data.role !== 'viewer');
    show('signout', data.role === 'owner');
    renderProviders(data);
    renderDevices(data);
    state.ready = true;
    notice('', false);
    return true;
  } catch (error) {
    state.ready = false;
    notice('Dashboard could not render: ' + error.message, true);
    return false;
  }
}

function safeLink(href,label){
  const a=el('a',label);const u=String(href||'');
  if(/^https:\/\/[a-z0-9.-]+\//i.test(u)){a.href=u;a.target='_blank';a.rel='noopener noreferrer';}
  else {return el('span',label,'subtle');}
  return a;
}
async function browseRepositories(){
  const provider=$('browse-provider').value,owner=$('browse-owner').value.trim(),root=$('repository-items');
  root.replaceChildren(el('p','Loading repositories…'));
  try{
    const data=await api(`/api/repos?provider=${encodeURIComponent(provider)}&owner=${encodeURIComponent(owner)}`);
    root.replaceChildren();if(!data.items.length){root.append(el('p','No repositories returned. Check provider sign-in and visibility.'));return;}
    for(const x of data.items){const row=el('article',undefined,'result-row'),left=el('div');
      left.append(safeLink(x.url,x.name),el('p',`${x.private?'Private':'Public'} · ${x.description||'No description'}`));
      row.append(left,btn('View pipelines',async()=>{
        $('pipeline-provider').value=provider;$('pipeline-repo').value=x.name;
        document.getElementById('pipelines').scrollIntoView({behavior:'smooth'});
        await browsePipelines();
      }));root.append(row);
    }
  }catch(e){root.replaceChildren();notice(e.message,true)}
}
async function explainCiWorkflow(){
  const provider=$('pipeline-provider').value, repo=$('pipeline-repo').value.trim();
  const root=$('ci-workflow-report');
  root.replaceChildren(el('p','Checking workflow policy…'));
  if(provider!=='github'){
    root.replaceChildren(el('p','This workflow-file allowlist applies to the GitHub personal runner pool. GitLab failure reasons are shown under failed native jobs.'));
    return;
  }
  try{
    const report=await api('/api/ci/workflow?provider=github&repo='+encodeURIComponent(repo));
    root.replaceChildren(el('strong',report.verified?'GitHub workflow is eligible':'Workflow rejected — '+report.code),
                         el('p',report.message),el('p','Next: '+report.suggestion,'small'));
    for(const issue of report.issues||[]){
      const line=el('article',undefined,'result-row');
      line.append(el('strong',issue.workflow+' — '+issue.code),
                  el('p',issue.reason),el('p','Fix: '+issue.next_step,'small'));
      root.append(line);
    }
  }catch(e){root.replaceChildren(el('p','Workflow diagnostics unavailable: '+e.message));}
}
async function showFailurePreview(provider,repo,run,root){
  const key=provider+':'+repo+':'+run;
  let cached=state.failureCache.get(key);
  if(!cached || Date.now()-cached.at>120000){
    let inFlight=state.failurePromises.get(key);
    if(!inFlight){
      inFlight=api('/api/ci/failure?provider='+encodeURIComponent(provider)+
        '&repo='+encodeURIComponent(repo)+'&run='+encodeURIComponent(run)+'&logs=1');
      state.failurePromises.set(key,inFlight);
      inFlight.finally(()=>state.failurePromises.delete(key)).catch(()=>{});
    }
    try{
      const data=await inFlight;
      cached={at:Date.now(),data};
      state.failureCache.set(key,cached);
    }catch(e){
      if(root.isConnected)root.replaceChildren(el('p','Failure details unavailable: '+e.message,'small'));
      return;
    }
  }
  if(!root.isConnected)return;
  const data=cached.data;
  root.replaceChildren(el('p',data.message,'small'));
  if(data.commit)root.append(el('p','Commit: '+data.commit+' · Attempt: '+(data.attempt||'?'),'small'));
  if(data.run_url)root.append(safeLink(data.run_url,'Open original workflow run'));
  for(const job of data.failures||[]){
    const line=el('div',undefined,'result-row');
    line.append(el('strong',job.job+' · '+job.state),
                el('p',job.reason,'small'),
                el('p','Runner: '+(job.runner_assigned?job.runner:'NOT ASSIGNED')+
                  ' · Job #'+job.job_id+
                  (job.exit_code_from_log!==null&&job.exit_code_from_log!==undefined
                    ?' · Observed exit code: '+job.exit_code_from_log:''),
                  'small'));
    if(job.log_state==='available'){
      line.append(el('p',job.excerpt_kind==='error_context'
        ?'Actual failing log lines (redacted):':'End of job log (no error marker found):','small'),
        el('pre',job.log_excerpt||'No log text returned.','job-log'),
        el('p','Selected excerpt only; full provider log and artifacts retain more details.','small'));
    }else{
      line.append(el('p','Exact error text unavailable: '+(job.log_note||'Provider did not return a transcript.'),'small'));
    }
    if(job.url)line.append(safeLink(job.url,'Open original job and logs'));
    root.append(line);
  }
}
async function browsePipelines(){
  if(state.pipelineBusy)return;state.pipelineBusy=true;state.pipelineSelected=true;
  const provider=$('pipeline-provider').value,repo=$('pipeline-repo').value.trim(),root=$('pipeline-items');
  root.replaceChildren(el('p','Loading pipelines…'));
  try{
    const data=await api(`/api/pipelines?provider=${encodeURIComponent(provider)}&repo=${encodeURIComponent(repo)}`);
    root.replaceChildren();if(!data.items.length){root.append(el('p','No pipeline runs returned.'));return;}
    let failurePreviews=0;
    for(const x of data.items){const row=el('article',undefined,'result-row'),left=el('div'),actions=el('div',undefined,'controls');
      const name=el('strong',x.title||'Pipeline');
      left.append(name,el('p',`#${x.id} · ${x.status} · ${x.branch||'unknown branch'} · ${x.created||''}`));
      if(x.url)left.append(safeLink(x.url,'Open in provider'));
      if(['failure','failed','timed_out'].includes(String(x.status).toLowerCase()) &&
         provider!=='circleci'){
        const details=el('div',undefined,'ci-failure-summary');
        if(failurePreviews<3){
          failurePreviews++;
          details.append(el('p','Checking provider job failure reasons…','small'));
          void showFailurePreview(provider,repo,x.id,details);
        }else{
          details.append(el('p','Failed — choose Jobs, logs & artifacts to inspect.','small'));
        }
        left.append(details);
      }
      if(state.role!=='viewer')for(const action of ['retry','cancel'])actions.append(btn(action==='retry'?'Retry':'Cancel',async()=>{
        if(!confirm(`Are you sure you want to ${action} pipeline #${x.id} for ${repo}?`))return;
        const out=await api('/api/pipeline',{provider,repo,id:x.id,action,confirm:true});notice(out.message);
        await browsePipelines();await loadDiagnostics();
      }));
      if(provider!=='circleci')actions.append(btn('Jobs, logs & artifacts',async()=>{
        await showJobDetails(provider,repo,x.id);
      }));
      const previous=state.statuses.get(provider+':'+repo+':'+x.id);
      if(previous&&previous!==x.status)notice(`${x.title||'Pipeline'} #${x.id}: ${previous} → ${x.status}`);
      state.statuses.set(provider+':'+repo+':'+x.id,x.status);
      row.append(left,actions);root.append(row);
    }
  }catch(e){root.replaceChildren();notice(e.message,true)}finally{state.pipelineBusy=false}
}
async function showJobDetails(provider,repo,run){
  const root=$('job-details');root.replaceChildren(el('h3',`Pipeline #${run}: native jobs`));
  root.append(btn('Close job details',async()=>root.replaceChildren()));
  const query=`provider=${encodeURIComponent(provider)}&repo=${encodeURIComponent(repo)}&run=${encodeURIComponent(run)}`;
  const failureRoot=el('div',undefined,'ci-failure-report');
  root.append(failureRoot);
  await showFailurePreview(provider,repo,run,failureRoot);
  try{
    const proof=await api('/api/execution-evidence?'+query);
    const label=proof.verification==='runner-reported'?'Receipt available — not independently attested':'Execution unproven';
    root.append(el('h3','Execution proof: '+label),
      el('p',proof.message,'small'),
      el('p','Commit: '+(proof.commit||'unknown')+' · Pipeline: '+proof.pipeline_status+' · Native jobs: '+proof.jobs.length,'small'));
    for(const job of proof.jobs){
      const details=el('div',undefined,'result-row');
      details.append(el('strong',job.name+' · '+job.status),
        el('span','Runner: '+(job.runner||'NOT ASSIGNED')+
          (job.failure_reason?' · Failure: '+job.failure_reason:'')+
          (job.duration_seconds!=null?' · '+job.duration_seconds+' seconds':''),'small'));
      root.append(details);
      for(const step of job.steps||[])root.append(el('p','  '+step.name+': '+step.status,'small'));
    }
  }catch(err){root.append(el('p','Execution proof unavailable: '+err.message,'small'));}
  const jobs=await api('/api/jobs?'+query);
  if(!jobs.items.length)root.append(el('p','No jobs yet. The provider may still be preparing the pipeline.'));
  for(const job of jobs.items){
    const card=el('article',undefined,'job-card'),actions=el('div',undefined,'controls');
    card.append(el('strong',`${job.name} · ${job.status}`),el('p',`Worker: ${job.runner}`));
    if(job.url)actions.append(safeLink(job.url,'Open native job'));
    actions.append(btn('Load log',async()=>{
      const result=await api('/api/job-log?'+query+'&job='+encodeURIComponent(job.id));
      const old=card.querySelector('pre');if(old)old.remove();
      card.append(el('pre',result.text||'No log content available.','job-log'));
      notice(result.note);
    }));card.append(actions);root.append(card);
  }
  const artifacts=await api('/api/artifacts?'+query);
  root.append(el('h3','Artifacts'));
  if(!artifacts.items.length)root.append(el('p','No retained artifacts reported by the provider.'));
  for(const item of artifacts.items){
    const row=el('div',undefined,'result-row');
    row.append(item.expired?el('span',item.name+' (expired)'):safeLink(item.url,item.name),el('span',`${item.size} bytes`,'small'));
    root.append(row);
  }
  root.scrollIntoView({behavior:'smooth'});
}

async function loadDiagnostics(){
  const root=$('health-panel');root.replaceChildren();
  try{const v=await api('/api/health');
    for(const [title,value] of [['Docker',v.docker?'Ready':'Unavailable'],['CPUs',v.cpus||'Unknown'],
      ['Free disk',v.disk_free_gb+' GB'],['GitHub CLI',v.provider_binaries.github?'Installed':'Missing'],
      ['GitLab CLI',v.provider_binaries.gitlab?'Installed':'Missing'],['CircleCI CLI',v.provider_binaries.circleci?'Installed':'Missing']]){
      const cell=el('div',undefined,'stat');cell.append(el('strong',String(value)),el('small',title));root.append(cell);
    }
  }catch(e){root.append(el('p',e.message));}
  const audit=$('audit-panel');audit.replaceChildren();
  if(state.role!=='owner')return;
  try{const logs=await api('/api/activity');audit.append(el('h3','Recent management activity'));
    for(const x of logs.items.slice(0,15)){
      const row=el('div',undefined,'result-row');row.append(el('span',`${x.action} · ${x.target}`),
        el('span',`${x.actor} · ${x.outcome}`,'subtle'));audit.append(row);
    }
  }catch(e){audit.append(el('p',e.message))}
}

let hostInfo=null;
function hostTargets(){
  const targets=hostInfo?($('host-action').value==='flatpak_install'?hostInfo.allowed_flatpaks:hostInfo.allowed_services):[];
  const select=$('host-target');select.replaceChildren();
  for(const target of targets||[]){const option=el('option',target);option.value=target;select.append(option);}
  $('host-prepare').disabled=!targets?.length;
}
async function loadHost(){
  const status=$('host-status');status.replaceChildren();
  try{
    hostInfo=await api('/api/host');
    if(!hostInfo.available){
      status.append(el('h3','Host agent not running'),el('p',hostInfo.message||'Start the dedicated local user service on your PC.'));
      show('host-controls',false);return;
    }
    status.append(el('h3','● Host connected'),el('p',hostInfo.system?.hostname||'Linux host'),
      el('p','OS: '+(hostInfo.system?.system||'Unknown')+' · Free disk: '+(hostInfo.system?.disk_free_gb??'?')+' GB','small'),
      el('p','Approved desktop apps: '+(hostInfo.allowed_flatpaks?.length||0)+' · User services: '+(hostInfo.allowed_services?.length||0),'small'));
    show('host-controls',state.role==='owner'||state.role==='editor');
    hostTargets();
    terminalControls();
  }catch(err){hostInfo=null;status.append(el('p',err.message));show('host-controls',false);terminalControls();}
}
async function requestHostAction(){
  const kind=$('host-action').value,target=$('host-target').value;
  if(!target){notice('No target has been approved on the PC yet.',true);return;}
  const preview=await api('/api/host/preview',{kind,target});
  if(!confirm('Host operation: '+preview.description+'\n\n'+preview.warning+'\n\nThis changes your PC. Continue?'))return;
  const results=$('host-results');results.replaceChildren(el('p','Waiting for the host agent to finish…'));
  try{
    const accepted=await api('/api/host/execute',{kind,target,preview_token:preview.preview_token,confirm:true});
    results.replaceChildren(el('p','Host task accepted. Tracking the actual outcome…'));
    if(!accepted.job_id)throw new Error('Host returned no job reference.');
    let done=null;
    for(let i=0;i<90;i++){
      await new Promise(resolve=>setTimeout(resolve,2000));
      done=await api('/api/host/job',{job_id:accepted.job_id});
      if(done.status!=='running')break;
    }
    if(!done||done.status==='running')throw new Error('Host action is still running. Inspect agent audit before retrying.');
    results.replaceChildren(el('p',done.message||done.status));
    if(done.status!=='success')throw new Error(done.message||'Host operation failed.');
    notice('Approved host operation completed.');
    await loadHost();
    await loadDiagnostics();
  }catch(err){results.replaceChildren(el('p',err.message));notice(err.message,true);}
}

// The mobile UI is a bounded text transcript, not an xterm emulator.
// Fish/bash prompts and colourised tools send ANSI CSI/OSC sequences. Strip
// them across poll boundaries so escape fragments never appear as literal text.
class TerminalTextFilter {
  constructor(){this.reset();}
  reset(){this.mode='text';this.previous='';this.sequenceLength=0;}
  push(chunk){
    let out='';
    for(const ch of chunk){
      const code=ch.charCodeAt(0);
      if(this.mode==='text'){
        if(code===27){this.mode='escape';this.sequenceLength=0;}
        else if(code===10||code===9||code>=32){if(code!==127&&!(code>=128&&code<=159))out+=ch;}
        // Ignore BEL, CR, backspace and other nonprintable controls. This
        // plain-text view does not emulate cursor movement or full-screen apps.
      }else{
        this.sequenceLength++;
        if(this.sequenceLength>8192){this.mode='text';continue;}
        if(this.mode==='escape'){
          if(ch==='[')this.mode='csi';
          else if(ch===']')this.mode='osc';
          else if(ch==='P'||ch==='^'||ch==='_')this.mode='string';
          else if('()*+-./'.includes(ch))this.mode='charset';
          else if(code!==27)this.mode='text';
        }else if(this.mode==='csi'){
          // CSI parameters and intermediates end at the final 0x40..0x7e.
          if(code>=64&&code<=126||code<32||code>63)this.mode='text';
        }else if(this.mode==='osc'||this.mode==='string'){
          if(code===27){this.previous=this.mode;this.mode='string-escape';}
          else if(code===7&&this.mode==='osc')this.mode='text';
        }else if(this.mode==='string-escape'){
          if(ch==='\\')this.mode='text'; // ESC backslash = ST
          else if(code!==27)this.mode=this.previous;
        }else if(this.mode==='charset'){
          this.mode='text';
        }
      }
    }
    return out;
  }
}
let terminalSession=null;
let terminalPolling=false;
let terminalDecoder=new TextDecoder('utf-8');
const terminalTextFilter=new TerminalTextFilter();
function terminalControls(){
  const connected=!!terminalSession;
  $('terminal-open').disabled=connected||!(hostInfo?.available&&hostInfo?.terminal_enabled)||(state.role!=='owner'&&state.role!=='editor');
  $('terminal-close').disabled=!connected;
  $('terminal-interrupt').disabled=!connected;
  $('terminal-input').disabled=!connected;
  $('terminal-send').disabled=!connected;
}
function terminalMessage(text){
  const area=$('terminal-output');
  area.textContent=(area.textContent+text).slice(-20000);
  area.scrollTop=area.scrollHeight;
}
async function terminalRead(){
  if(!terminalSession||terminalPolling||document.hidden)return;
  terminalPolling=true;
  try{
    const data=await api('/api/host/terminal/read',{session_id:terminalSession});
    if(data.data_b64){
      const bytes=Uint8Array.from(atob(data.data_b64),c=>c.charCodeAt(0));
      terminalMessage(terminalTextFilter.push(terminalDecoder.decode(bytes,{stream:true})));
    }
    if(!data.running){
      terminalMessage('\n[Shell exited; session no longer running]\n');
      terminalSession=null;
      terminalControls();
    }
  }catch(err){
    terminalMessage('\n[Terminal connection: '+err.message+']\n');
    terminalSession=null;
    terminalControls();
  }finally{terminalPolling=false;}
}
async function terminalOpen(){
  const p=await api('/api/host/terminal/preview',{});
  if(!confirm('Start a real Linux user shell on your PC?\n\n'+p.warning+
      '\n\nThis session can read and change your files. Only continue on your trusted device.'))return;
  const opened=await api('/api/host/terminal/open',{preview_token:p.preview_token,confirm:true});
  terminalSession=opened.session_id;
  terminalDecoder=new TextDecoder('utf-8');
  terminalTextFilter.reset();
  $('terminal-output').textContent='';
  $('terminal-status').textContent='Interactive user shell · idle expiry '+opened.idle_timeout+'s';
  terminalControls();
  await terminalRead();
}
async function terminalSend(value){
  if(!terminalSession)return;
  const bytes=new TextEncoder().encode(value);
  if(bytes.length>512)throw new Error('Input is too large for one terminal write.');
  const wire=btoa(Array.from(bytes,b=>String.fromCharCode(b)).join(''));
  await api('/api/host/terminal/write',{session_id:terminalSession,data_b64:wire});
  await terminalRead();
}
async function terminalClose(){
  if(!terminalSession)return;
  const old=terminalSession;
  terminalSession=null;
  terminalControls();
  try{
    await api('/api/host/terminal/close',{session_id:old});
    terminalMessage('\n[Terminal closed]\n');
  }catch(err){notice(err.message,true);}
}

async function loadApps(){
  const root=$('apps-items');root.replaceChildren(el('p','Checking application job queues…'));
  if(state.role==='viewer'){root.replaceChildren(el('p','Application monitoring requires an approved editor or owner session.'));return;}
  try{
    const inventory=await api('/api/apps/inventory');
    root.replaceChildren();
    const res=inventory.resources;
    if(res){
      if(res.policy){
        const p=res.policy;
        $('apps-mode').value=p.mode||'balanced';
        $('apps-mode-apply').disabled=state.mode==='mobile'&&$('apps-mode').value==='performance';
        root.append(el('p','Mode: '+p.mode+' · '+(p.paused?'New work paused':'New work enabled')+
          ' · Admission: '+p.reason+' · Effective concurrency: '+p.max_parallel+
          ' · Memory pressure: '+(p.memory_pressure_pct===null?'unknown':p.memory_pressure_pct+'%')+
          ' · CPU temperature: '+(p.temperature_c===null?'unknown':p.temperature_c+' °C')+
          ' · Free disk: '+(p.disk?.min_free_mb==null?'unknown':p.disk.min_free_mb+' MiB')+
          ' · Disk status: '+(p.disk?.min_free_percent==null?'unknown':p.disk.min_free_percent+'% free'),'small'));
      }
      root.append(el('p','Native CI containers: '+(res.native_runners===null?'unknown':res.native_runners)+
        ' · Native reserved RAM: '+(res.reserved_native_runner_ram_mb===null?'unknown':res.reserved_native_runner_ram_mb+' MiB')+
        ' · Native CPU reservations: '+(res.native_cpu_slots===null?'unknown':res.native_cpu_slots),'small'));
      const c=res.capacity;
      root.append(el('h3','Parallel work and resource safeguards'),
        el('p','Active jobs: '+res.active_jobs+'/'+c.max_parallel+
          ' · Reserved RAM: '+res.reserved_ram_mb+' MiB'+
          ' · Available RAM: '+(res.memory_available_mb===null?'unknown':res.memory_available_mb+' MiB')+
          ' · System RAM reserve: '+c.min_free_ram_mb+' MiB','small'));
      const gpus=Object.entries(res.gpu);
      if(!gpus.length)root.append(el('p','GPU VRAM telemetry unavailable: GPU jobs pause automatically.','small'));
      for(const [id,gpu] of gpus){
        if(gpu.mode==='shared'){
          root.append(el('p','GPU '+id+' · '+(gpu.model||'Intel integrated graphics')+
            ' · Shared system RAM (no separate VRAM pool)'+
            ' · Shared GPU RAM reserved: '+(res.reserved_shared_gpu_ram_mb||0)+' MiB'+
            ' · GPU jobs: '+res.active_integrated_gpu_jobs+'/'+res.max_integrated_gpu_jobs,'small'));
        }else{
          root.append(el('p','GPU '+id+
            ' · Dedicated VRAM free: '+gpu.free_mb+'/'+gpu.total_mb+' MiB'+
            ' · Reserved: '+(res.reserved_vram_mb[id]||0)+' MiB'+
            ' · Minimum free: '+c.min_free_vram_mb+' MiB','small'));
        }
      }
      root.append(el('p','Resource reservations are admission controls; RAM hard limits require protected worker cgroups. VRAM is monitored, not hardware-enforced.','small'));
    }
    const fleet=inventory.fleet_devices||[];
    root.append(el('h3','Remote edge fleet · owner approved'));
    if(!fleet.length){
      root.append(el('p','No remote workers enrolled. Windows ARM64 and Android devices are opt-in; no cloud machine is silently assumed free.','small'));
    }
    for(const machine of fleet){
      const item=el('article',undefined,'result-row');
      const label=machine.app+' · '+machine.kind+' · '+(machine.online?'Ready':'Offline or throttled');
      item.append(el('strong',label),el('p',
        'Tasks: '+machine.tasks.join(', ')+
        ' · Free RAM: '+(machine.free_ram_mb===null?'unavailable':machine.free_ram_mb+' MiB')+
        ' · Billing: '+machine.billing,'small'));
      root.append(item);
    }
    if(!inventory.apps.length){root.append(el('p','No applications registered yet. Use the local owner CLI to authorise one.'));return;}
    for(const app of inventory.apps){
      const card=el('article',undefined,'result-row');
      const details=el('div');
      details.append(el('strong',app.app),
        el('p','Approved tasks: '+app.tasks.join(', '),'small'),
        el('p','State: '+(app.enabled?'enabled':'disabled')+
          ' · Max parallel: '+app.max_parallel+
          ' · Queue: '+Object.entries(app.queues).map(([k,v])=>k+' '+v).join(', '),'small'));
      card.append(details);root.append(card);
    }
    const queue=$('apps-queue');queue.replaceChildren();
    try{
      const preview=await api('/api/apps/queue');
      for(const job of preview.jobs.slice(0,24)){
        const item=el('div',undefined,'result-row');
        item.append(el('span',job.app+' · '+job.task+' · '+job.status+
          ' · priority '+job.priority,'small'));
        if(job.status==='queued'){
          item.append(btn('Higher priority',async()=>{
            await api('/api/apps/priority',{job_id:job.job_id,priority:Math.min(5,job.priority+1),confirm:true});
            await loadApps();
          }));
        }
        queue.append(item);
      }
      if(!preview.jobs.length)queue.append(el('p','No queued, running or stranded application jobs.','small'));
    }catch(err){queue.replaceChildren(el('p',err.message));}
  }catch(e){root.replaceChildren(el('p','App runtime unavailable: '+e.message));}
}
async function appControl(action){
  if(!confirm(action==='emergency_stop'?
    'Emergency stop all managed application scopes? Running jobs will remain stranded until verified safe recovery. Existing CI runners will NOT be stopped.':
    'Apply '+action+' to the application runtime?'))return;
  const result=await api('/api/apps/control',{action,confirm:true});
  notice(result.message||('Application control applied: '+action));
  await loadApps();
}

async function loadInternet(){
  const root=$('internet-items');root.replaceChildren(el('p','Loading network access policies…','small'));
  try{
    const result=await api('/api/ci/network');
    root.replaceChildren();
    if(!result.projects.length)root.append(el('p','No approved CI repository profiles configured.','small'));
    for(const project of result.projects){
      const item=el('article',undefined,'result-row');
      const details=el('div');
      details.append(el('strong',project.repo));
      details.append(el('p','Network: '+(project.mode==='full'?'Full public web (controlled)':'Offline')+
        ' · '+(project.trusted?'Trusted opt-in':'Default isolation')+
        ' · provider '+project.provider,'small'));
      details.append(el('p','Approved branches: '+project.branches.join(', '),'small'));
      item.append(details);root.append(item);
    }
    root.append(el('p',result.note||'Network policy is private and owner-controlled.','small'));
  }catch(err){root.replaceChildren(el('p',err.message,'small'));}
}
async function loadDeployments(){
  const root=$('deployments-items');root.replaceChildren(el('p','Loading verified release records…','small'));
  try{
    const result=await api('/api/deployments');
    root.replaceChildren();
    if(!result.targets.length){
      root.append(el('p','No deployment targets activated. Configure a reviewed private target policy before enabling staging.','small'));
    }
    for(const target of result.targets){
      const card=el('article',undefined,'result-row'), body=el('div');
      body.append(el('strong',target.target+' · '+target.environment));
      body.append(el('p','Adapter: '+target.adapter+' · '+(target.enabled?'Approved':'Disabled')+
        ' · '+target.last_status,'small'));
      if(target.current_commit)body.append(el('p','Commit '+target.current_commit.slice(0,12)+
        ' · release '+(target.current_digest||'').slice(0,12),'small'));
      if(target.previous_release)body.append(el('p','Previous release '+target.previous_release.slice(0,12),'small'));
      if(target.recent_failure)body.append(el('p','Last failure '+target.recent_failure,'small'));
      const hist=el('details');hist.append(el('summary','Release history ('+target.recent_history.length+')'));
      const history=el('div',undefined,'results');
      for(const item of [...target.recent_history].reverse()){
        history.append(el('p',item.when+' · '+item.phase+' · '+item.sha.slice(0,12),'small'));
      }
      hist.append(history);body.append(hist);card.append(body);root.append(card);
    }
    root.append(el('p',result.note||'Status is based on local evidence, not a live provider health check.','small'));
  }catch(err){root.replaceChildren(el('p',err.message,'small'));}
}
async function loadMachines(){
  const root=$('machine-items');root.replaceChildren(el('p','Loading trusted PCs…'));
  try{
    const data=await api('/api/machines');
    root.replaceChildren();
    if(!data.machines.length)root.append(el('p','No additional computers enrolled yet.','small'));
    for(const machine of data.machines){
      const card=el('article',undefined,'result-row'),details=el('div');
      details.append(el('strong',machine.name),el('p',machine.host,'small'));
      const note=el('p','Reachability not checked; runner state unknown.','small');
      details.append(note);
      const actions=el('div',undefined,'controls');
      actions.append(safeLink(machine.url,'Open this PC dashboard'));
      actions.append(btn('Check HTTPS',async()=>{
        const result=await api('/api/machines/probe?host='+encodeURIComponent(machine.host));
        note.textContent=result.reachability+' · '+result.note;
      }));
      if(state.role==='owner'&&state.mode==='desktop')actions.append(btn('Remove',async()=>{
        if(!confirm('Remove '+machine.name+' from this PC inventory? No remote data is deleted.'))return;
        await api('/api/machines/remove',{host:machine.host,confirm:true});
        await loadMachines();
      }));
      card.append(details,actions);root.append(card);
    }
  }catch(err){root.replaceChildren(el('p',err.message));}
}

function configureOnboarding(){
  const provider=$('onboard-provider').value;
  show('circleci-class-label',provider==='circleci');
  $('onboard-result').replaceChildren();
}
async function previewOnboarding(){
  const provider=$('onboard-provider').value, repo=$('onboard-repo').value.trim();
  const branch=$('onboard-branch').value.trim(),resource_class=$('onboard-class').value.trim();
  const root=$('onboard-result');root.replaceChildren(el('p','Checking native workflow…'));
  try{
    const plan=await api('/api/onboarding/plan',{provider,repo,branch,resource_class});
    root.replaceChildren(el('h3',`New: ${plan.path}`),el('p',plan.warning,'small'));
    root.append(el('pre',plan.content,'workflow-preview'));
    const readiness=el('p','Provider readiness has not been checked.','small');
    root.append(readiness,btn('Check native runner',async()=>{
      const result=await api('/api/onboarding/check',{provider,repo,branch,resource_class});
      readiness.textContent=result.message+' '+result.runners.map(r=>r.name+(r.busy?' (busy; jobs will queue)':'')).join(', ');
    }));
    root.append(btn('Open review request',async()=>{
      if(!confirm(`Create a ${provider==='gitlab'?'merge':'pull'} request for ${repo}? It will not be merged automatically.`))return;
      const created=await api('/api/onboarding/apply',{provider,repo,branch,resource_class,confirm:true});
      root.append(el('p',created.message));root.append(safeLink(created.url,'View review request'));
      notice('Review request created. Approve and merge it to activate push-triggered CI.');
      await loadDiagnostics();
    },'primary'));
  }catch(err){root.replaceChildren();notice(err.message,true)}
}

let raeburnDraft=null;
let raeburnSubmissionKey=null;
async function loadRaeburn(){
  const root=$('raeburn-missions'), health=$('raeburn-health');
  if(!root||!health)return;
  if(!['owner','editor'].includes(state.role)){
    root.replaceChildren(el('p','Mission controls require an approved editor or owner session.','small'));
    $('raeburn-form').classList.add('hidden');
    return;
  }
  $('raeburn-form').classList.remove('hidden');
  try{
    const data=await api('/api/raeburn/command');
    const counts=data.counts||{}, memory=data.memory||{};
    const readiness=data.readiness||{}, backup=readiness.backup||{};
    health.replaceChildren();
    const stateRow=el('div',undefined,'result-row');
    stateRow.append(el('strong',readiness.local_release_ready?'Local service healthy':'Local checks need attention'));
    stateRow.append(el('p',
      String(memory.memory_events??0)+' events · '+String(counts.queued||0)+' queued · '+
      String(counts.awaiting_approval||0)+' handoffs awaiting review','small'));
    health.append(stateRow);
    const proof=el('div',undefined,'result-row');
    proof.append(el('strong',
      'Receipts '+(readiness.receipts?.invalid===0?'verified':'CHECK REQUIRED')+
      ' · Backup '+(backup.integrity_verified?'verified':'CHECK REQUIRED')));
    proof.append(el('p','Separate drive: '+(backup.separate_filesystem?'yes':'no')+
      ' · Off-site recovery: '+(readiness.disaster_recovery_complete?'verified':'not verified'),'small'));
    health.append(proof);
    if((readiness.attention_required||[]).length){
      health.append(el('p','Release attention: '+readiness.attention_required.join(', ').replaceAll('_',' '),'small'));
    }
    root.replaceChildren();
    for(const signal of (data.attention||[]).slice(0,4)){
      const noticeRow=el('div',undefined,'result-row');
      noticeRow.append(el('strong',signal.lane+' · '+signal.reason),
                       el('p',String(signal.count)+' records require further evidence (not independently verified)','small'));
      noticeRow.append(btn('Review',async()=>{
        $('raeburn-objective').value='Review '+signal.lane+' items requiring evidence: '+signal.reason;
        $('raeburn-mode').value='assist';
        await previewRaeburn();
      }));
      root.append(noticeRow);
    }
    if(!data.missions?.length)root.append(el('p','No objectives yet. Preview your first objective above.','small'));
    for(const mission of (data.missions||[])){
      const row=el('article',undefined,'result-row');
      row.append(el('strong',mission.objective));
      row.append(el('p',String(mission.lane)+' · '+String(mission.mode)+' · '+String(mission.status)+
        ' · '+String(mission.steps_completed)+'/'+String(mission.steps_total)+' steps','small'));
      if(mission.status==='awaiting_approval')row.append(el('p',
        'External action is not executed automatically. Review the handoff before proceeding.','small'));
      const controls=el('div',undefined,'controls');
      controls.append(btn('View evidence',async()=>{
        const item=await api('/api/raeburn/mission?id='+encodeURIComponent(mission.id));
        const details=$('raeburn-detail');
        details.replaceChildren(el('strong','Evidence for '+mission.objective));
        for(const step of item.steps||[]){
          const line=el('div',undefined,'result-row');
          line.append(el('strong',step.kind+' · '+step.status));
          line.append(el('p',step.receipt?
              'SHA-256: '+step.receipt+' · '+(step.receipt_valid?'Recalculated':'NOT VERIFIED'):
              'No completed receipt','small'));
          if(step.summary)line.append(el('pre',step.summary,'job-log'));
          details.append(line);
        }
      }));
      const actions=(mission.status==='queued'?['pause','cancel']:
        mission.status==='paused'?['resume','cancel']:
        mission.status==='awaiting_approval'?['approve_handoff','cancel']:
        mission.status==='failed'?['retry_safe']:
        mission.status==='needs_reconciliation'?['reconcile','cancel']:[]);
      for(const action of actions){
        const label=({pause:'Pause',resume:'Resume',cancel:'Cancel',
          approve_handoff:'Acknowledge handoff',retry_safe:'Retry safe step',
          reconcile:'Mark for manual investigation'})[action];
        controls.append(btn(label,async()=>{
          if(!confirm(label+' for '+mission.objective+'? This does not execute external business actions.'))return;
          await api('/api/raeburn/control',{id:mission.id,action,confirm:true});
          await loadRaeburn();
        }));
      }
      row.append(controls);
      root.append(row);
    }
  }catch(error){health.replaceChildren(el('p','Command Centre unavailable: '+error.message));}
}
async function previewRaeburn(){
  const objective=$('raeburn-objective').value.trim(),mode=$('raeburn-mode').value;
  $('raeburn-create').disabled=true;
  raeburnDraft=null;raeburnSubmissionKey=null;
  const plan=await api('/api/raeburn/preview',{objective,mode});
  const root=$('raeburn-plan');
  root.replaceChildren(el('strong','Plan · '+plan.lane+' · '+plan.mode));
  root.append(el('p','Steps: '+plan.steps.join(' → '),'small'));
  root.append(el('p',plan.external_action_requested?
    'External work requires a separate approved executor. RaeburnAI will research and prepare the handoff only.':
    'All steps are bounded and read-only, except owner-approved private memory synchronisation.','small'));
  raeburnDraft={objective,mode};raeburnSubmissionKey=crypto.randomUUID().replace(/-/g,'');
  $('raeburn-create').disabled=false;
}
async function createRaeburn(){
  if(!raeburnDraft || raeburnDraft.objective!==$('raeburn-objective').value.trim() ||
       raeburnDraft.mode!==$('raeburn-mode').value){
    throw new Error('Objective changed. Preview the new plan before confirming.');
  }
  if(!confirm('Queue this approved, restricted mission? No external writes or spending are enabled.'))return false;
  const result=await api('/api/raeburn/create',{objective:raeburnDraft.objective,
     mode:raeburnDraft.mode,key:raeburnSubmissionKey,confirm:true});
  notice('RaeburnAI queued '+result.id.slice(0,10)+'; evidence will appear below.');
  $('raeburn-create').disabled=true;
  raeburnDraft=null;
  await loadRaeburn();
  return true;
}
async function loadAutonomy(){
  const root=$('autonomy-items');root.replaceChildren();
  if(state.role!=='owner'&&state.role!=='editor'){root.append(el('p','Background mission details require management permission.'));return;}
  try{
    const data=await api('/api/autonomy');
    const head=el('div',undefined,'result-row');
    head.append(el('strong','Foreground-first policy: '+(data.admission?.eligible?'Ready':'Throttled')),
      el('span',String(data.admission?.reason||'Unknown')+' · '+String(data.admission?.memory_available_mb??'?')+' MB available','small'));
    root.append(head);
    const capabilities=el('div',undefined,'result-row');
    capabilities.append(
      el('strong','Private graphical desktop: '+(data.private_display?.active?'Running':'Offline')),
      el('span','Login-session reuse: '+(data.login_session_cache?.enabled?'Enabled':'Not enabled')+
        ' · Cloud billing: '+String(data.cloud_cost?.policy||'Unknown'),'small'));
    root.append(capabilities);
    if(!data.configured){root.append(el('p','No background missions enrolled on this PC.','small'));return;}
    if(!data.missions?.length){root.append(el('p','No missions submitted.','small'));return;}
    for(const mission of data.missions){
      const row=el('div',undefined,'result-row');
      const progress=(mission.steps_completed??0)+'/'+(mission.steps_total??'?')+' steps';
      row.append(el('strong',mission.id),el('span',mission.status+' · '+progress+
        ' · '+String(mission.evidence_receipts??0)+' evidence receipts','small'));
      const actions=el('div',undefined,'controls');
      for(const operation of (mission.status==='paused'?['resume','cancel']:
        mission.status==='queued'?['pause','cancel']:[])){
        actions.append(btn(operation==='resume'?'Resume':operation==='pause'?'Pause':'Cancel',
          async()=>{
            if(!confirm(operation+' mission '+mission.id+'? Running work will not be interrupted.'))return;
            await api('/api/autonomy/control',{mission:mission.id,action:operation,confirm:true});
            await loadAutonomy();
          }));
      }
      row.append(actions);
      root.append(row);
    }
  }catch(error){root.replaceChildren(el('p','Background status unavailable: '+error.message));}
}

async function init(){
  let secret=null;if(location.hash.startsWith('#access=')){secret=decodeURIComponent(location.hash.slice(8));history.replaceState(null,'',location.pathname+location.search);}
  if(secret){try{await api('/api/session',{secret});}catch(e){notice(e.message,true)}}
  $('signout').addEventListener('click',async()=>{
    try{await api('/api/logout',{});document.dispatchEvent(new Event('open-ci-logout'));notice('Signed out of this desktop session.');await refresh();}
    catch(e){notice(e.message,true)}
  });
  $('refresh').addEventListener('click',async()=>{await refresh();await loadDiagnostics();await loadHost();await loadMachines();await loadApps();await loadDeployments();await loadInternet();await loadAutonomy();await loadRaeburn();});
  $('apps-refresh').addEventListener('click',loadApps);
  $('autonomy-refresh').addEventListener('click',loadAutonomy);
  $('raeburn-refresh').addEventListener('click',loadRaeburn);
  $('raeburn-form').addEventListener('submit',async e=>{
    e.preventDefault();try{await previewRaeburn()}catch(error){notice(error.message,true)}
  });
  $('raeburn-create').addEventListener('click',async()=>{
    const button=$('raeburn-create');button.disabled=true;
    try{const created=await createRaeburn();if(!created)button.disabled=false}
    catch(error){notice(error.message,true);button.disabled=false}
  });
  for(const id of ['raeburn-objective','raeburn-mode']){
    $(id).addEventListener('input',()=>{
      $('raeburn-create').disabled=true;
      raeburnDraft=null;
    });
  }

  $('apps-pause').addEventListener('click',()=>appControl('pause').catch(e=>notice(e.message,true)));
  $('apps-resume').addEventListener('click',()=>appControl('resume').catch(e=>notice(e.message,true)));
  $('apps-emergency').addEventListener('click',()=>appControl('emergency_stop').catch(e=>notice(e.message,true)));
  $('apps-mode-apply').addEventListener('click',()=>appControl($('apps-mode').value).catch(e=>notice(e.message,true)));
  $('apps-mode').addEventListener('change',()=>{
    $('apps-mode-apply').disabled=state.mode==='mobile'&&$('apps-mode').value==='performance';
  });
  $('machines-refresh').addEventListener('click',loadMachines);
  $('deployments-refresh').addEventListener('click',loadDeployments);
  $('internet-refresh').addEventListener('click',loadInternet);
  $('machine-enroll').addEventListener('submit',async e=>{
    e.preventDefault();
    const name=$('machine-name').value.trim(),host=$('machine-host').value.trim().toLowerCase();
    if(!confirm('Trust the exact machine address '+host+'? It will only become a navigation/health entry.'))return;
    try{await api('/api/machines/add',{name,host,confirm:true});
      $('machine-enroll').reset();await loadMachines();}
    catch(err){notice(err.message,true)}
  });
  $('host-refresh').addEventListener('click',loadHost);
  $('terminal-open').addEventListener('click',async()=>{try{await terminalOpen()}catch(err){notice(err.message,true)}});
  $('terminal-close').addEventListener('click',terminalClose);
  $('terminal-interrupt').addEventListener('click',async()=>{try{await terminalSend('\x03')}catch(err){notice(err.message,true)}});
  $('terminal-form').addEventListener('submit',async e=>{e.preventDefault();const value=$('terminal-input').value;$('terminal-input').value='';if(!value.trim())return;try{await terminalSend(value+'\n')}catch(err){notice(err.message,true)}});
  setInterval(terminalRead,1000);
  terminalControls();
  $('host-action').addEventListener('change',hostTargets);
  $('host-form').addEventListener('submit',async e=>{e.preventDefault();const button=$('host-prepare');button.disabled=true;try{await requestHostAction();}catch(err){notice(err.message,true)}finally{hostTargets();}});
  $('ci-explain').addEventListener('click',explainCiWorkflow);
  $('browse-repos').addEventListener('click',browseRepositories);
  $('onboard-provider').addEventListener('change',configureOnboarding);
  $('onboard-form').addEventListener('submit',async e=>{e.preventDefault();await previewOnboarding();});
  $('browse-pipelines').addEventListener('click',browsePipelines);
  $('pair').addEventListener('click',async()=>{try{const out=await api('/api/mobile/request',{});notice(out.message)}catch(e){notice(e.message,true)}});
  $('repo-visibility').addEventListener('change',()=>show('public-warning',$('repo-visibility').value==='public'));
  $('new-repo').addEventListener('submit',async e=>{e.preventDefault();const provider=$('repo-provider').value,name=$('repo-name').value.trim(),visibility=$('repo-visibility').value,description=$('repo-desc').value.trim();
    if(visibility==='public'&&!$('confirm-public').checked){notice('Confirm public visibility first.',true);return;}
    if(!confirm(`Create ${visibility} ${provider} repository “${name}”?`))return;
    const b=$('create-repo');b.disabled=true;try{const out=await api('/api/repos',{provider,name,visibility,description,confirm_public:$('confirm-public').checked});notice(out.url?`${out.message} ${out.url}`:out.message);$('new-repo').reset();show('public-warning',false);}catch(err){notice(err.message,true)}finally{b.disabled=false}
  });
  configureOnboarding();
  await refresh();
  if(state.ready){await loadDiagnostics();await loadHost();await loadMachines();await loadApps();await loadDeployments();await loadInternet();await loadAutonomy();await loadRaeburn();
    show('machine-enroll',state.role==='owner'&&state.mode==='desktop');}
  setInterval(()=>{if(state.ready&&state.pipelineSelected&&!document.hidden)browsePipelines().catch(e=>notice(e.message,true));},20000);
  setInterval(()=>{if(state.ready&&['owner','editor'].includes(state.role)&&!document.hidden)loadRaeburn().catch(()=>{});},30000);
}
document.addEventListener('DOMContentLoaded',init);

async function loadLocalCI(){
  const root=document.getElementById('local-ci-items');
  if(!root)return;
  root.replaceChildren(el('p','Loading local job ledger…'));
  try{
    const result=await api('/api/ci/local');root.replaceChildren();
    root.append(el('p','Available RAM: '+result.resources.memory_available_mb+' MiB · Reserved desktop headroom: '+result.resources.capacity.min_free_ram_mb+' MiB','small'));
    for(const job of result.items){
      const item=el('div',undefined,'result-item');
      item.append(el('strong',job.repo+' · '+job.status));
      item.append(el('p',job.sha.slice(0,12)+' · '+job.branch+' · '+job.job_id,'small'));
      if(job.detail)item.append(el('p',job.detail,'small'));
      const inspect=el('button','View logs and evidence','btn secondary');
      inspect.type='button';inspect.onclick=async()=>{
        try{const record=await api('/api/ci/local/job?job='+encodeURIComponent(job.job_id));
          document.getElementById('local-ci-evidence').textContent=JSON.stringify(record.receipt||{status:record.scheduler_status},null,2)+'\n\n'+record.log;
        }catch(err){document.getElementById('local-ci-evidence').textContent=err.message;}
      };item.append(inspect);
      if(['queued','running','stranded'].includes(job.status)){
        const cancel=el('button','Cancel job','btn secondary');cancel.type='button';
        cancel.onclick=async()=>{if(!confirm('Cancel this local CI job?'))return;
          try{await api('/api/ci/local/cancel',{job_id:job.job_id,confirm:true});await loadLocalCI();}
          catch(err){document.getElementById('local-ci-evidence').textContent=err.message;}
        };item.append(cancel);
      }root.append(item);
    }
    if(!result.items.length)root.append(el('p','No local CI jobs recorded.'));
  }catch(err){root.replaceChildren(el('p',err.message));}
}
document.getElementById('local-ci-refresh')?.addEventListener('click',loadLocalCI);
