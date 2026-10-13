import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url);const {chromium}=require('playwright');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');const base=process.env.TINGWEN_QA_ORIGIN||'http://127.0.0.1:18082';const qa=path.join(root,'qa');fs.mkdirSync(qa,{recursive:true});
const records=[];
test('真实浏览器检查招商联系、手机排版、全文阅读及AI可读性',{timeout:120000},async()=>{
 const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
 try{
  for(const width of [360,390,1440]){
   const context=await browser.newContext({viewport:{width,height:1000},reducedMotion:'reduce',permissions:['clipboard-read','clipboard-write']});
   const page=await context.newPage();page.setDefaultTimeout(10000);const errors=[],external=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith(base)&&/^https?:/.test(r.url()))external.push(r.url());});
   for(const route of ['/','/specification/','/guide/','/guide/product-07.html','/guide/technical-02.html','/contact/','/faq/','/facts/','/cases/']){
    const response=await page.goto(base+route,{waitUntil:'load'});assert.equal(response.status(),200);
    assert.ok(await page.locator('h1').count()===1,'每页一个清晰的主标题');
    const metrics=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,width:innerWidth,ids:[...document.querySelectorAll('[id]')].map(e=>e.id)}));assert.ok(metrics.scroll<=metrics.width+1,`${route} ${width}: 页面横向溢出 ${metrics.scroll}`);assert.equal(new Set(metrics.ids).size,metrics.ids.length,'页面ID唯一');
    const data=await page.locator('script[type="application/ld+json"]').textContent();const graph=JSON.parse(data)['@graph'];assert.ok(graph.find(x=>x['@type']==='Organization').contactPoint.description.includes('wzqy2019'),'AI结构化数据应包含真实微信号');
    assert.ok(await page.locator('.footer-brand').textContent().then(t=>t.includes('wzqy2019')),'微信必须存在于可见静态页尾');
    assert.deepEqual(await page.locator('#main-nav a').allTextContents().then(t=>t.slice(0,2)),['产品功能','作品案例']);
    if(route==='/'){
     assert.deepEqual(await page.locator('main>section').evaluateAll(s=>s.slice(0,2).map(e=>e.id)),['features','cases']);
     assert.equal(await page.evaluate(()=>scrollY),0,'直接打开首页应在产品功能首屏');
     if(process.env.TINGWEN_COUNTER_QA==='1'){assert.match(await page.locator('#visit-total').textContent(),/^[0-9,]+ 次$/);assert.ok(await page.locator('.visit-counter').isVisible());assert.equal(await page.locator('.floating-contact').count(),0,'已移除悬浮招商按钮');assert.ok(await page.locator('.visit-counter').evaluate(e=>e.getBoundingClientRect().right<=innerWidth&&e.getBoundingClientRect().bottom<=innerHeight),'访问总数完整显示在右下角');}
     assert.ok(await page.locator('#features h1').evaluate(e=>{const r=e.getBoundingClientRect();return r.top>=document.querySelector('.site-header').getBoundingClientRect().bottom&&r.bottom<innerHeight;}),'功能标题必须在首屏');
     assert.ok(await page.locator('#features .feature-details h3').first().evaluate(e=>e.getBoundingClientRect().bottom<innerHeight),'首屏应显示具体功能');
     assert.ok(await page.locator('.hero-description').evaluate(e=>e.getBoundingClientRect().right<=innerWidth),'手机首屏说明不可被裁切');
     await page.locator('.hero-actions [data-contact]').click();assert.equal(await page.locator('#contact-dialog').evaluate(d=>d.open),true);
     await page.locator('#copy-wechat').click();await page.waitForFunction(()=>document.querySelector('#copy-status').textContent.length>0);assert.ok((await page.locator('#copy-status').textContent()).includes('已复制'));assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),'wzqy2019');
     await page.screenshot({path:path.join(qa,`招商-微信弹窗-${width}.png`)});await page.keyboard.press('Escape');assert.equal(await page.locator('#contact-dialog').evaluate(d=>d.open),false);
     if(width<700){await page.locator('#mobile-menu').click();assert.equal(await page.locator('#mobile-menu').getAttribute('aria-expanded'),'true');await page.locator('#main-nav a[href="/#features"]').click();assert.equal(await page.locator('#mobile-menu').getAttribute('aria-expanded'),'false');}
     const image=page.locator('.fusion-architecture img');await image.scrollIntoViewIfNeeded();await image.evaluate(async i=>{if(!i.complete)await new Promise((resolve,reject)=>{i.addEventListener('load',resolve,{once:true});i.addEventListener('error',reject,{once:true});});if(!i.naturalWidth)throw new Error('架构图未载入');});
     const audio=await page.locator('audio').evaluate(async a=>{a.load();await new Promise((resolve,reject)=>{a.addEventListener('loadedmetadata',resolve,{once:true});a.addEventListener('error',reject,{once:true});});return {duration:a.duration,src:a.currentSrc};});assert.ok(audio.duration>0,'内置真实样音可读取');
     await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:path.join(qa,`招商-首页-${width}.png`)});
    }
    if(route==='/specification/'){
     assert.equal(await page.locator('.spec-rail a').count(),28);assert.ok((await page.locator('.specification-body').textContent()).includes('doubao-seedream-5-0-pro-260628'));assert.ok((await page.locator('.specification-body').textContent()).length>20000);
     const link=page.locator('.spec-rail a[href="#spec-section-7"]');await link.click();assert.ok((await page.locator('#spec-section-7').textContent()).includes('六种本机后端'));
     await page.emulateMedia({media:'print'});assert.equal(await page.locator('.spec-rail').isVisible(),false);assert.ok(await page.locator('.specification-body').isVisible());await page.emulateMedia({media:'screen'});
     await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:path.join(qa,`新版-产品规格-${width}.png`)});
    }
    if(route==='/guide/'){
     assert.equal(await page.locator('.chapter-link').count(),45);await page.locator('#chapter-search').fill('wzqy2019');const count=await page.locator('.chapter-link:not([hidden])').count();assert.ok(count>0&&count<45);await page.locator('#chapter-search').fill('找不到的内容xkz987');assert.equal(await page.locator('.chapter-link:not([hidden])').count(),0);await page.locator('#chapter-search').fill('');assert.equal(await page.locator('.chapter-link:not([hidden])').count(),45);
     await page.screenshot({path:path.join(qa,`招商-完整目录-${width}.png`)});
    }
    if(route==='/guide/technical-02.html'&&width===1440){await page.locator('svg').first().screenshot({path:path.join(qa,'招商-架构图.png')});await page.emulateMedia({media:'print'});assert.equal(await page.locator('.site-header').isVisible(),false);assert.ok(await page.locator('#chapter-content').isVisible());const sizes=await page.locator('svg').evaluateAll(ss=>ss.map(s=>s.getBoundingClientRect().width<=s.closest('figure').getBoundingClientRect().width+1));assert.ok(sizes.every(Boolean));await page.emulateMedia({media:'screen'});}
    if(route==='/cases/'){assert.equal(await page.locator('.case-card').count(),8);for(const image of await page.locator('.case-thumbnail').all()){await image.scrollIntoViewIfNeeded();await image.evaluate(async i=>{if(!i.complete)await new Promise((resolve,reject)=>{i.addEventListener('load',resolve,{once:true});i.addEventListener('error',reject,{once:true});});if(i.naturalWidth!==864||i.naturalHeight!==486)throw new Error('封面未完整载入');});}await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:path.join(qa,`招商-作品案例-${width}.png`),fullPage:true});}
    records.push({route,width,status:200,overflow:false,contact_visible:true,jsonld_contact:true});
   }
   assert.deepEqual(errors,[]);assert.deepEqual(external,[]);await context.close();
  }
  const denied=await browser.newContext({viewport:{width:390,height:900}});await denied.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{value:{writeText:()=>Promise.reject(new Error('denied'))}}));const denial=await denied.newPage();await denial.goto(base+'/contact/');await denial.locator('main [data-contact]').click();await denial.locator('#copy-wechat').click();assert.ok((await denial.locator('#copy-status').textContent()).includes('手动复制'));assert.equal(await denial.locator('#wechat-value').inputValue(),'wzqy2019');await denied.close();
  const nojs=await browser.newContext({javaScriptEnabled:false});const crawl=await nojs.newPage();for(const route of ['/','/specification/','/contact/','/guide/product-15.html','/guide/technical-13.html']){await crawl.goto(base+route);assert.ok((await crawl.locator('main').textContent()).length>(route==='/contact/'?300:600));if(route==='/contact/')assert.ok((await crawl.locator('main').textContent()).includes('wzqy2019'));}await nojs.close();
  const offlineContext=await browser.newContext({offline:true,viewport:{width:390,height:900}});const offlinePage=await offlineContext.newPage();const offlineErrors=[];offlinePage.on('pageerror',e=>offlineErrors.push(e.message));await offlinePage.goto(new URL('file://'+path.join(root,'dist/downloads/shengying-specification.html')).href);assert.ok((await offlinePage.locator('.specification-body').textContent()).length>20000);assert.ok(await offlinePage.locator('.spec-diagram img').evaluate(i=>i.complete&&i.naturalWidth>0));assert.equal(await offlinePage.locator('.specification-body table').last().locator('tbody tr').count(),46);const offlineWidth=await offlinePage.evaluate(()=>({scroll:document.documentElement.scrollWidth,width:innerWidth}));assert.ok(offlineWidth.scroll<=offlineWidth.width+1);assert.deepEqual(offlineErrors,[]);await offlineContext.close();
  fs.writeFileSync(path.join(qa,'browser-report.json'),JSON.stringify({passed:true,checks:records,clipboard_copy:true,clipboard_denied_fallback:true,without_javascript_readable:true,offline_html_readable:true,external_resource_requests:0,apk_changes:false},null,2)+'\n');
 }finally{await browser.close();}
});
