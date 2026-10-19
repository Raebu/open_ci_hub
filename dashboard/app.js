'use strict';
const $ = id => document.getElementById(id);
const state = {mode:'desktop', ready:false};
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
async function refresh(){try{const data=await api('/api/overview');state.mode=data.mode;state.role=data.role;$('mode').textContent=data.mode==='mobile'?'Secure mobile session':'Local desktop session';show('access',false);show('workspace',true);state.ready=true;show('repositories',state.role!=='viewer');show('signout',state.role==='owner');renderProviders(data);renderDevices(data)}catch(e){state.ready=false;show('workspace',false);show('access',true);show('signout',false);if(e.status===401&&e.data?.mobile){$('access-title').textContent='Approve this mobile device';$('access-detail').textContent=`Signed into Tailscale as ${e.data.identity}. Request permission, then approve it from the desktop dashboard.`;show('pair',true)}else{$('access-title').textContent='Open CI Hub desktop session required';$('access-detail').textContent='Launch python hub.py dashboard on your PC. It opens a private administrator link in your browser.';show('pair',false)}if(e.status!==401)notice(e.message,true)}}

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
      row.append(left,actions);root.append(row);
    }
  }catch(e){root.replaceChildren();notice(e.message,true)}
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
  $('refresh').addEventListener('click',async()=>{await refresh();await loadDiagnostics();});
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
  if(state.ready)await loadDiagnostics();
}
document.addEventListener('DOMContentLoaded',init);
