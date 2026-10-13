import test from 'node:test';import assert from 'node:assert/strict';import {DatabaseSync} from 'node:sqlite';
import {handleContentRequest} from '../server/visit-counter.mjs';
const contents={'/':{body:'<strong id="visit-total">统计加载中</strong>',type:'text/html',counted:true,home:true},'/cases/':{body:'案例',type:'text/html',counted:true},'/llms.txt':{body:'AI产品资料',type:'text/plain',counted:true},'/guide/product-01':{body:'产品指南',type:'text/html',counted:true}};
function setup(){
 const sql=new DatabaseSync(':memory:');
 sql.exec("CREATE TABLE site_visits(key TEXT PRIMARY KEY NOT NULL,total INTEGER NOT NULL DEFAULT 0,started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)");
 const DB={withSession(mode){
  assert.equal(mode,'first-primary');
  return {prepare(query){return {async first(){return sql.prepare(query).get()??null;}};}};
 }};
 return {sql,env:{DB}};
}

const request=(p='/',options={})=>new Request('https://tingwen.test'+p,options);
test('真人、AI、搜索和无脚本请求共享一个累加值；首页直接显示真实数值',async()=>{const {sql,env}=setup();try{for(const ua of ['Mozilla/5.0','OAI-SearchBot','Googlebot','bingbot']){const r=await handleContentRequest(request('/',{headers:{'User-Agent':ua}}),env,contents);assert.equal(r.status,200);assert.equal(r.headers.get('Cache-Control'),'no-store');assert.match(await r.text(),/\d+ 次/);}await handleContentRequest(request('/llms.txt'),env,contents);const r=await handleContentRequest(request('/api/visits'),env,contents);assert.equal((await r.json()).total,5);}finally{sql.close();}});
test('并发100次成功内容访问都计入；统计读取、重定向、HEAD、图片及404不计入',async()=>{const {sql,env}=setup();try{await Promise.all(Array.from({length:100},()=>handleContentRequest(request('/cases/'),env,contents)));for(const path of ['/api/visits','/api/visits','/guide/product-01.html','/assets/x.png','/not-found'])await handleContentRequest(request(path),env,contents);await handleContentRequest(request('/',{method:'HEAD'}),env,contents);const r=await handleContentRequest(request('/api/visits'),env,contents);assert.equal((await r.json()).total,100);const denied=await handleContentRequest(request('/api/visits',{method:'POST'}),env,contents);assert.equal(denied.status,405);}finally{sql.close();}});
test('数据库故障仍展示正文，显示暂不可用；不开设虚构数字',async()=>{const env={DB:{withSession(){throw new Error('DB failure');}}};const r=await handleContentRequest(request(),env,contents);assert.equal(r.status,200);assert.match(await r.text(),/暂不可用/);const api=await handleContentRequest(request('/api/visits'),env,contents);assert.equal(api.status,503);assert.equal((await api.json()).total,null);});
