#!/usr/bin/env node
/* Private Chromium, never attached to KDE or a personal browser profile. */
import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright-core";
function guard(ok, why) {if(!ok) throw new Error(why)}
function checkHost(host) {
 guard(typeof host==="string" && /^(?:[a-z0-9-]+\.)+[a-z]{2,63}$/.test(host)
   && !host.endsWith(".local") && !host.includes("xn--"),"Invalid host");
 return host;
}
function checkURL(raw, allowed) {
 guard(typeof raw==="string" && raw.length<=2048 && !raw.includes("\\"),
   "Invalid URL");
 let u; try {u=new URL(raw)} catch{throw new Error("Malformed URL")}
 guard(u.protocol==="https:" && !u.username && !u.password && !u.hash
   && (u.port===""||u.port==="443") && allowed.has(checkHost(u.hostname)),
   "Blocked origin or scheme");
 return u;
}
async function execute(plan) {
 guard(plan && typeof plan==="object" && !Array.isArray(plan),"Invalid plan");
 guard(Array.isArray(plan.hosts) && plan.hosts.length>0 && plan.hosts.length<=8,
   "Invalid hosts");
 const allowed=new Set(plan.hosts.map(checkHost));
 guard(Array.isArray(plan.actions)&&plan.actions.length>0&&plan.actions.length<=12,
   "Invalid actions");
 checkURL(plan.url,allowed);
 const timeout=Math.min(30000,Math.max(5000,Number(plan.timeoutMs)||12000));
 const browser=await chromium.launch({
   executablePath:process.env.OCI_CHROMIUM_PATH||"/usr/bin/chromium",
   headless:true,timeout:timeout,
   args:["--disable-dev-shm-usage","--no-first-run","--no-default-browser-check"]});
 let context;
 try {
   context=await browser.newContext({
     acceptDownloads:false,serviceWorkers:"block",permissions:[],
     viewport:{width:1100,height:720}});
   await context.route("**/*",async route=>{
     try {checkURL(route.request().url(),allowed);await route.continue()}
     catch{await route.abort("blockedbyclient")}
   });
   const page=await context.newPage();
   await page.goto(plan.url,{waitUntil:"domcontentloaded",timeout});
   const records=[];
   for(const [index,action] of plan.actions.entries()){
     guard(action && typeof action==="object","Invalid action");
     if(action.type!=="screenshot")
       guard(typeof action.selector==="string" && action.selector.length>0 &&
         action.selector.length<=150,"Invalid selector");
     if(action.type==="extract"){
       const v=await page.locator(action.selector).first().innerText({timeout:5000});
       records.push({action:"extract",index,text:v.slice(0,1500)});
     }else if(action.type==="click"||action.type==="fill"){
       guard(plan.allowInteraction===true,"Interaction requires owner approval");
       if(action.type==="fill"){
         guard(typeof action.value==="string"&&action.value.length<=240,"Invalid value");
         await page.locator(action.selector).first().fill(action.value,{timeout:5000});
       }else await page.locator(action.selector).first().click({timeout:5000});
       records.push({action:action.type,index,completed:true});
     }else if(action.type==="screenshot"){
       const dir=process.env.OCI_AUTONOMY_ARTIFACT_DIR;
       guard(typeof dir==="string"&&dir.length>0&&fs.existsSync(dir)&&
         fs.lstatSync(dir).isDirectory()&&!fs.lstatSync(dir).isSymbolicLink(),
         "Private artifact directory missing");
       const name="page-"+index+".jpg",dest=path.join(dir,name);
       await page.screenshot({path:dest,type:"jpeg",quality:65,
         animations:"disabled",fullPage:false,timeout:8000});
       guard(fs.statSync(dest).size<2000000,"Artifact too large");
       records.push({action:"screenshot",index,filename:name,bytes:fs.statSync(dest).size});
     }else throw new Error("Unapproved action");
     checkURL(page.url(),allowed);
   }
   return {ok:true,final_url:page.url(),records};
 }finally{
   if(context) await context.close().catch(()=>{});
   await browser.close().catch(()=>{});
 }
}
async function main(){
 const jobPath=process.env.OCI_AUTONOMY_PLAN_PATH;
 let wire;
 if(jobPath){
   guard(jobPath==="/input/plan.json","Invalid mission file path");
   guard(fs.statSync(jobPath).size<=16384,"Plan too large");
   wire=fs.readFileSync(jobPath,"utf8");
 }else{
   let size=0,parts=[];
   for await(const chunk of process.stdin){
     size+=chunk.length;guard(size<=16384,"Plan too large");parts.push(chunk);
   }
   wire=Buffer.concat(parts).toString("utf8");
 }
 const req=JSON.parse(wire);
 process.stdout.write(JSON.stringify(await execute(req))+"\n");
}

main().catch(err=>{
 const msg=String(err?.message||"");
 const safe=/^(Invalid|Blocked|Malformed|Interaction|Unapproved|Private|Artifact|Plan)/.test(msg)
   ?msg.slice(0,100):"browser_job_failed";
 process.stdout.write(JSON.stringify({ok:false,error:safe})+"\n");
 process.exitCode=1;
});
