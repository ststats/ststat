import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import http from 'node:http';
import {createHash} from 'node:crypto';
const game=await fs.readFile(new URL('./game.html',import.meta.url));
const server=http.createServer((req,res)=>{res.setHeader('Content-Type','text/html; charset=utf-8');res.end(game)});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const profile=process.env.PROFILE||'desktop',entrants=Number(process.env.ENTRANTS||8);
const groups={
 graphics:['scene','flat','off','resolution70','resolution125'],
 layers:['scene','noBackground','noEffects','noNameplates','noMinimap'],
 activity:['scene','noUI','noAudio','wideCamera','frozenCamera'],
 pipeline:['scene','fps30','noKarts','vectorKarts','noFaces']
};
const group=process.env.GROUP||'graphics',variants=groups[group];if(!variants)throw Error('Unknown group');
const browser=await chromium.launch();const results=[];await fs.mkdir('results',{recursive:true});
const q=(a,p)=>a.length?a.toSorted((a,b)=>a-b)[Math.floor((a.length-1)*p)]:null;
try {for(let round=0;round<3;round++)for(let j=0;j<variants.length;j++){
 const variant=variants[(j+round*2)%variants.length],name=`${group}-${profile}-${entrants}-${round+1}-${variant}`;
 const context=await browser.newContext({viewport:profile==='desktop'?{width:1280,height:900}:{width:390,height:844},deviceScaleFactor:1});
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(String(e)));
 await context.addInitScript(()=>{let seed=20260912;Math.random=()=>((seed=(Math.imul(seed,1664525)+1013904223)>>>0)/4294967296)});
 let cdp,tracing=false;
 try {
 await page.goto(`http://127.0.0.1:${server.address().port}`);await page.waitForFunction(()=>typeof ready!=='undefined'&&ready);
 await page.evaluate(({variant,entrants})=>{
  while(names.length<entrants)addDriver('Guest '+names.length);chosen=new Set(names.map((_,i)=>i).slice(0,entrants));syncSelection();$('laps').value='5';setCamera('auto');
  const ratio=variant==='resolution70'?.7:variant==='resolution125'?1.25:1;
  function applyResolution(){const width=cv.clientWidth;if(!width)return;const wanted=Math.round(Math.min(1200,Math.max(600,Math.round(width*Math.min(devicePixelRatio||1,1.5))))*ratio);if(cv.width!==wanted){cv.width=wanted;cv.height=Math.round(wanted*740/1200);canvasScale=wanted/1200;renderDirty=true;}if(variant==='fps30')renderInterval=1/30;}
  if(variant==='noBackground')drawTrack=()=>{g.fillStyle='#deeadf';g.fillRect(0,0,W,H)};
  if(variant==='noEffects'){drawEffects=()=>{};drawHitStatus=()=>{};}
  if(variant==='noNameplates')drawNameplates=()=>{};
  if(variant==='noMinimap')drawMini=()=>{};
  if(variant==='noKarts')drawKart=()=>{};
  if(variant==='noFaces')drawKartFace=()=>{};
  if(variant==='vectorKarts')drawBody=function(r,detail){if(detail){g.fillStyle='#18292335';g.beginPath();g.ellipse(3,6,15,18,0,0,7);g.fill();g.fillStyle='#243033';for(let side of [-1,1]){g.fillRect(side*10-3,-10,6,10);g.fillRect(side*10-3,6,6,10)}}g.fillStyle=colors[r.id];g.beginPath();g.roundRect(-9,-16,18,33,5);g.fill();if(detail){g.fillStyle='#f4f4d7';g.fillRect(-5,-14,10,3)}g.fillStyle='#253039';g.fillRect(-14,10,28,4);};
  const draw=render;let pulse=0;
  render=function(dt){applyResolution();if(variant==='off')return;if(variant==='flat'){cameraStep(dt,order());g.setTransform(1,0,0,1,0,0);g.fillStyle=pulse++%2?'#deeadf':'#dde9de';g.fillRect(0,0,cv.width,cv.height);drawMini(dt);return;}draw(dt)};
  start();
  if(variant==='noAudio')raceAudio.toggle();
  if(variant==='noUI')updateUI=()=>{};
  if(variant==='wideCamera')setCamera('wide');
  if(variant==='frozenCamera'){camera={x:600,y:370,z:2.4,angle:0};cameraStep=()=>{};}
  applyResolution();
 },{variant,entrants});
 await page.waitForFunction(()=>state==='racing');
 cdp=await context.newCDPSession(page);
 await cdp.send('Tracing.start',{categories:'devtools.timeline,disabled-by-default-devtools.timeline,v8,disabled-by-default-v8.gc,blink.user_timing,gpu',transferMode:'ReturnAsStream'});tracing=true;
 await page.evaluate(v=>performance.mark('BENCH_'+v),variant);
 await page.waitForTimeout(45000);
 const snapshot=await page.evaluate(()=>({state,elapsed,entrants:racers.length,canvas:[cv.width,cv.height],renderInterval,freezes:freezeLog,standings:order().map(r=>({id:r.id,progress:r.progress,finish:r.finish}))}));
 const complete=new Promise(r=>cdp.once('Tracing.tracingComplete',r));await cdp.send('Tracing.end');const {stream}=await complete;tracing=false;
 const chunks=[];while(true){const c=await cdp.send('IO.read',{handle:stream});chunks.push(c.base64Encoded?Buffer.from(c.data,'base64'):Buffer.from(c.data));if(c.eof)break;}await cdp.send('IO.close',{handle:stream});
 const raw=Buffer.concat(chunks);await fs.writeFile(`results/${name}.trace.json`,raw);
 const events=JSON.parse(raw).traceEvents,thread=events.find(e=>e.name==='thread_name'&&e.args?.name==='CrRendererMain');
 const summary={name,variant,round:round+1,errors,...snapshot};
 for(const n of ['Commit','FireAnimationFrame','MinorGC','MajorGC']){const a=events.filter(e=>e.ph==='X'&&e.name===n&&(!thread||e.tid===thread.tid)).map(e=>e.dur/1000);summary[n]={count:a.length,p95:q(a,.95),max:a.length?Math.max(...a):null,over50:a.filter(v=>v>50).length};}
 results.push(summary);await page.screenshot({path:`results/${name}.png`});
 if(errors.length)throw Error(errors.join('\n'));
 }catch(e){results.push({name,error:String(e)});throw e;}finally{if(tracing)await cdp.send('Tracing.end').catch(()=>{});await context.close();await fs.writeFile('results/summary.json',JSON.stringify({browser:browser.version(),sha256:createHash('sha256').update(game).digest('hex'),environment:'GitHub Linux headless, NOT user GPU or physical phone. Sequential variants per runner; 45s measurement per run.',results},null,2));}
}}finally{await browser.close();server.close();}
