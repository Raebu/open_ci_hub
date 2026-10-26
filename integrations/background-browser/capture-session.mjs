#!/usr/bin/env node
/* Owner-interactive one-time sign-in. Never reads existing Chrome profiles. */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import readline from "node:readline/promises";
import { chromium } from "playwright-core";

const host=process.argv[2];
if(typeof host!=="string" || !/^(?:[a-z0-9-]+\.)+[a-z]{2,63}$/.test(host) ||
   host.includes("xn--") || host.endsWith(".local") || process.argv.length!==3) {
  console.error("Usage: node capture-session.mjs example.com");
  process.exit(2);
}
if(!fs.statSync("/dev/shm").isDirectory()) throw Error("Private RAM storage unavailable.");
let browser;
try {
  browser=await chromium.launch({executablePath:"/usr/bin/chromium",
    headless:false,timeout:15000,
    args:["--no-first-run","--no-default-browser-check","--disable-dev-shm-usage"]});
  const context=await browser.newContext({acceptDownloads:false,
    serviceWorkers:"block",permissions:[]});
  const page=await context.newPage();
  await page.goto("https://"+host+"/",{waitUntil:"domcontentloaded",timeout:25000});
  console.log("Sign in yourself in the temporary Chromium window for https://"+host);
  console.log("No passwords are requested by this terminal or stored by Open CI Hub.");
  const rl=readline.createInterface({input:process.stdin,output:process.stdout});
  try {await rl.question("Press Enter when fully signed in, or Ctrl+C to cancel: ")}
  finally {rl.close()}
  if(new URL(page.url()).hostname!==host || new URL(page.url()).protocol!=="https:")
    throw Error("Return to the approved HTTPS site before saving a session.");
  const full=await context.storageState({indexedDB:false});
  const cookies=full.cookies.filter(c=>c.domain===host&&c.secure===true);
  const origins=full.origins.filter(o=>o.origin==="https://"+host);
  if(!cookies.length && !origins.length) throw Error("No reusable same-site session was observed.");
  const safe=JSON.stringify({cookies,origins});
  if(Buffer.byteLength(safe)>262144) throw Error("Browser session exceeded the vault limit.");
  const name="/dev/shm/oci-session-"+crypto.randomBytes(14).toString("hex")+".json";
  const handle=fs.openSync(name,"wx",0o600);
  try{fs.writeFileSync(handle,safe,{encoding:"utf8"});fs.fsyncSync(handle)}
  finally{fs.closeSync(handle)}
  console.log("Temporary RAM-only session created at:",name);
  console.log("Import with browser_session_vault.py import HOST --state PATH --confirm");
  console.log("Then remove the unencrypted RAM-only copy with rm PATH.");
  await context.close();
} catch(error) {
  console.error("Capture refused:",String(error.message||"Browser capture failed").slice(0,130));
  process.exitCode=2;
} finally {
  if(browser) await browser.close().catch(()=>{});
}
