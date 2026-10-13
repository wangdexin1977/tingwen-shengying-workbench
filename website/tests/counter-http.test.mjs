import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';
const origin=process.env.TINGWEN_QA_ORIGIN||'http://127.0.0.1:17872';const production=origin.startsWith('https://');const report={origin};
async function read(){const r=await fetch(origin+'/api/visits');assert.equal(r.status,200);assert.equal(r.headers.get('cache-control'),'no-store');return r.json();}
test('真实HTTP请求包含真人与无脚本爬虫；累计值持久共享',{timeout:60000},async()=>{
 const before=await read();assert.ok(Number.isInteger(before.total));
 const uas=['Mozilla/5.0','Googlebot','bingbot','OAI-SearchBot'];
 for(const ua of uas){const r=await fetch(origin+'/',{headers:{'user-agent':ua}});assert.equal(r.status,200);const body=await r.text();assert.match(body,/<strong id="visit-total">[0-9,]+ 次<\/strong>/);assert.ok(!body.includes('class="floating-contact"'));}
 const text=await fetch(origin+'/llms.txt');assert.equal(text.status,200);await text.text();
 const after=await read();assert.ok(after.total>=before.total+5);if(!production)assert.equal(after.total,before.total+5);
 const concurrent=production?12:100;
 await Promise.all(Array.from({length:concurrent},async()=>{const r=await fetch(origin+'/cases/');assert.equal(r.status,200);await r.text();}));
 const final=await read();assert.ok(final.total>=after.total+concurrent);if(!production)assert.equal(final.total,after.total+concurrent);
 await fetch(origin+'/',{method:'HEAD'});const image=await fetch(origin+'/assets/cases/7694843490920743424.jpg');assert.equal(image.status,200);await image.arrayBuffer();await read();const excluded=await read();assert.ok(excluded.total>=final.total);if(!production)assert.equal(excluded.total,final.total);
 Object.assign(report,{passed:true,before:before.total,after:excluded.total,requests_verified:5+concurrent,concurrent,server_rendered:true,without_javascript:true,excluded_assets_and_head:!production,started_at:excluded.started_at});fs.mkdirSync('qa',{recursive:true});fs.writeFileSync('qa/counter-http-report.json',JSON.stringify(report,null,2)+'\n');
});
