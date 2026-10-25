"""Mobile dashboard DOM and session regression tests."""
import re
import subprocess
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
HTML=ROOT/"dashboard/index.html"
JS=ROOT/"dashboard/app.js"
NODE=r"""
const fs=require('fs'),vm=require('vm');
const [htmlfile,jsfile,mode]=process.argv.slice(1);
const html=fs.readFileSync(htmlfile,'utf8'),js=fs.readFileSync(jsfile,'utf8');
const ids=[...html.matchAll(/id="([^"]+)"/g)].map(x=>x[1]);
const cls=()=>{const s=new Set(['hidden']);return{toggle(n,v){if(v)s.add(n);else s.delete(n)},contains(n){return s.has(n)}}};
const elm=()=>({classList:cls(),textContent:'',children:[],append(...x){this.children.push(...x)},replaceChildren(...x){this.children=x},addEventListener(){}});
const nodes=new Map(ids.map(x=>[x,elm()]));
if(mode==='broken')nodes.delete('repositories');
const data={mode:'mobile',role:mode==='viewer'?'viewer':'editor',identity:'mobile@example.test',providers:[]};
const sandbox={document:{getElementById:id=>nodes.get(id)||null,createElement:elm,addEventListener(){}},
  location:{hostname:'private.ts.net'},Map,Date,TextDecoder,
  fetch:async()=>({ok:mode!=='pair',status:mode==='pair'?401:200,json:async()=>mode==='pair'?{mobile:true,identity:data.identity,error:'Login required'}:data}),
  console};
vm.createContext(sandbox);vm.runInContext(js,sandbox);
(async()=>{
 const ok=await vm.runInContext('refresh()',sandbox),ready=vm.runInContext('state.ready',sandbox);
 const hidden=x=>nodes.get(x).classList.contains('hidden'),label=x=>nodes.get(x).textContent;
 if(mode==='editor'||mode==='viewer'){
   if(!ok||!ready||hidden('workspace')||!hidden('access'))throw Error('mobile screen not ready');
   if(hidden('repositories')!==(mode==='viewer'))throw Error('bad permissions');
   if(label('mode')!=='Secure mobile session')throw Error('mode wrong');
 }else if(mode==='pair'){
   if(ok||ready||hidden('access')||hidden('pair')||!hidden('workspace'))throw Error('pairing broken');
 }else if(mode==='broken'){
   if(ok||ready||!hidden('access')||!label('notice').startsWith('Dashboard could not render'))throw Error('auth/error not distinguished');
 }
 console.log('pass '+mode);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
class MobileDashboardTests(unittest.TestCase):
    def test_html_identifiers_match_javascript(self):
        h=HTML.read_text(); j=JS.read_text()
        ids=set(re.findall(r'\bid="([^"]+)"',h))
        selectors=set(re.findall(r"\$\('([^']+)'\)",j))
        selectors|=set(re.findall(r"show\('([^']+)'",j))
        self.assertFalse(selectors-ids,selectors-ids)
        self.assertFalse(set(re.findall(r'href="#([^"]+)"',h))-ids)

    def test_approved_mobile_and_pairing_no_false_desktop_error(self):
        for role in ("editor","viewer","pair","broken"):
            with self.subTest(role=role):
                p=subprocess.run(["node","-e",NODE,str(HTML),str(JS),role],
                                 capture_output=True,text=True,timeout=12)
                self.assertEqual(p.returncode,0,p.stderr or p.stdout)
