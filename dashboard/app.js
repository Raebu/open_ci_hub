'use strict';
const $ = id => document.getElementById(id);
const state = {mode:'desktop', ready:false, pipelineBusy:false, pipelineSelected:false, statuses:new Map()};
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
async function refresh(){try{const data=await api('/api/overview');state.mode=data.mode;state.role=data.role;$('mode').textContent=data.mode==='mobile'?'Secure mobile session':'Local desktop session';show('access',false);show('workspace',true);state.ready=true;show('repositories',state.role!=='viewer');show('onboarding',state.role!=='viewer');show('signout',state.role==='owner');renderProviders(data);renderDevices(data)}catch(e){state.ready=false;show('workspace',false);show('access',true);show('signout',false);if(e.status===401&&e.data?.mobile){$('access-title').textContent='Approve this mobile device';$('access-detail').textContent=`Signed into Tailscale as ${e.data.identity}. Request permission, then approve it from the desktop dashboard.`;show('pair',true)}else{$('access-title').textContent='Open CI Hub desktop session required';$('access-detail').textContent='Launch python hub.py dashboard on your PC. It opens a private administrator link in your browser.';show('pair',false)}if(e.status!==401)notice(e.message,true)}}

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
async function browsePipelines(){
  if(state.pipelineBusy)return;state.pipelineBusy=true;state.pipelineSelected=true;
  const provider=$('pipeline-provider').value,repo=$('pipeline-repo').value.trim(),root=$('pipeline-items');
  root.replaceChildren(el('p','Loading pipelines…'));
  try{
    const data=await api(`/api/pipelines?provider=${encodeURIComponent(provider)}&repo=${encodeURIComponent(repo)}`);
    root.replaceChildren();if(!data.items.length){root.append(el('p','No pipeline runs returned.'));return;}
    for(const x of data.items){const row=el('article',undefined,'result-row'),left=el('div'),actions=el('div',undefined,'controls');
      const name=el('strong',x.title||'Pipeline');
      left.append(name,el('p',`#${x.id} · ${x.status} · ${x.branch||'unknown branch'} · ${x.created||''}`));
      if(x.url)left.append(safeLink(x.url,'Open in provider'));
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

let terminalSession=null;
let terminalPolling=false;
const terminalDecoder=new TextDecoder('utf-8');
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
      terminalMessage(terminalDecoder.decode(bytes,{stream:true}));
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
    if(!inventory.apps.length){root.append(el('p','No applications registered yet. Use the local owner CLI to authorise one.'));return;}
    for(const app of inventory.apps){
      const card=el('article',undefined,'result-row');
      const details=el('div');
      details.append(el('strong',app.app),
        el('p','Approved tasks: '+app.tasks.join(', '),'small'),
        el('p','State: '+(app.enabled?'enabled':'disabled')+
          ' · Queue: '+Object.entries(app.queues).map(([k,v])=>k+' '+v).join(', '),'small'));
      card.append(details);root.append(card);
    }
    if(inventory.nodes&&inventory.nodes.length){
      root.append(el('h3','Approved worker PCs · reserved capacity'));
      for(const node of inventory.nodes){
        const box=el('article',undefined,'result-row');
        const name=el('strong',node.node+' · '+node.active_jobs+' running');
        const remaining=el('p',
          'RAM '+node.available_memory_mb+' MB free of budget · VRAM '+
          node.available_vram_mb+' MB free of budget · CPU slots '+
          node.available_cpu_slots+' · job slots '+node.available_job_slots,'small');
        box.append(name,remaining);root.append(box);
      }
      root.append(el('p','Reservations are scheduler estimates, not enforced process limits or independent hardware measurements.','small'));
    } else {
      root.append(el('p','Resource-safe parallel scheduling is not enabled: register an owner-approved worker node and task profiles.','small'));
    }
  }catch(e){root.replaceChildren(el('p','App runtime unavailable: '+e.message));}
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
async function init(){
  let secret=null;if(location.hash.startsWith('#access=')){secret=decodeURIComponent(location.hash.slice(8));history.replaceState(null,'',location.pathname+location.search);}
  if(secret){try{await api('/api/session',{secret});}catch(e){notice(e.message,true)}}
  $('signout').addEventListener('click',async()=>{
    try{await api('/api/logout',{});notice('Signed out of this desktop session.');await refresh();}
    catch(e){notice(e.message,true)}
  });
  $('refresh').addEventListener('click',async()=>{await refresh();await loadDiagnostics();await loadHost();await loadMachines();await loadApps();await loadApps();});
  $('apps-refresh').addEventListener('click',loadApps);
  $('machines-refresh').addEventListener('click',loadMachines);
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
  if(state.ready){await loadDiagnostics();await loadHost();await loadMachines();
    show('machine-enroll',state.role==='owner'&&state.mode==='desktop');}
  setInterval(()=>{if(state.ready&&state.pipelineSelected&&!document.hidden)browsePipelines().catch(e=>notice(e.message,true));},20000);
}
document.addEventListener('DOMContentLoaded',init);
