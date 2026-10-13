import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import crypto from 'node:crypto';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const dist=path.join(root,'static-output');
const read=p=>fs.readFileSync(path.join(dist,p),'utf8');
test('当前规格、下载全文、机器清单和事实资料保持同一版本与正文',()=>{
 const source=fs.readFileSync(path.join(root,'../docs/产品规格说明书.md'),'utf8');
 assert.equal(read('downloads/shengying-specification.md'),source);
 const metadata=JSON.parse(read('specification-source.json'));
 assert.equal(metadata.markdown_sha256,crypto.createHash('sha256').update(source).digest('hex'));
 assert.equal(metadata.product_version,'1.2.2');assert.equal(metadata.headings,28);assert.equal(metadata.api_routes,46);
 const current=read('specification/index.html');assert.ok(current.includes('MediaRecorder'));assert.ok(current.includes('Qwen3-ASR-0.6B-8bit'));assert.ok(current.includes('doubao-seedream-5-0-pro-260628'));assert.ok(current.includes('macOS'));assert.ok(current.includes('真实账号上传及完整待发布界面尚待首次登录后验证'));
 const audit=JSON.parse(read('downloads/shengying-api.json'));assert.equal(audit.routes.length,46);
 const tableCount=source.split('\n').filter(line=>/^\| (?:--- \| ?)+$/.test(line)).length;assert.equal((current.match(/<table>/g)||[]).length,tableCount,'说明书全部Markdown表格必须正常渲染');
 const apiTable=current.slice(current.indexOf('id="spec-section-28"'));assert.equal((apiTable.match(/<tr>/g)||[]).length,47,'接口附录应渲染46条路由和表头');
 const offline=read('downloads/shengying-specification.html');assert.ok(offline.includes('<style>'));assert.ok(offline.includes('data:image/svg+xml;base64,'));assert.ok(!offline.includes('src="/assets/'));assert.ok(!offline.includes('href="/assets/'));
 const facts=JSON.parse(read('product-facts.json'));assert.equal(facts.features.voice_backends.length,6);assert.ok(facts.features.queue.includes('失败保留并继续'));assert.equal(facts.features.defaults.music_enabled,false);assert.equal(facts.features.defaults.title_enabled,false);
 assert.ok(read('llms-full.txt').includes(source));assert.ok(read('guide/product-01.html').includes('历史档案'));
 for(const file of ['index.html','faq/index.html','facts/index.html'])assert.ok(!read(file).includes('当前无自动字幕、背景音乐混音'));
});
test('每个索引页面的中文标题与描述有明确且独立的主题',()=>{
 const urls=[...read('sitemap.xml').matchAll(/<loc>([^<]+)<\/loc>/g)].map(m=>new URL(m[1]).pathname);
 const htmlFor=p=>read(p.endsWith('/')?p+'index.html':p+'.html');
 const descriptions=urls.map(p=>htmlFor(p).match(/<meta name="description" content="([^"]*)"/)[1]);
 const titles=urls.map(p=>htmlFor(p).match(/<title>([^<]*)<\/title>/)[1]);
 assert.equal(new Set(titles).size,urls.length);assert.equal(new Set(descriptions).size,urls.length,'章节介绍不应因相同前言形成重复描述');
});
test('公开页面完整承载45章及中文招商、搜索资源',()=>{
 assert.ok(fs.existsSync(path.join(dist,'index.html')),'需要生成可公开访问的中文招商首页');
 const home=read('index.html');assert.ok(home.includes('wzqy2019'));assert.ok(home.includes('PC'));assert.ok(home.includes('Android'));assert.ok(home.includes('<dialog'));assert.ok(home.includes('费用'));assert.ok(home.includes('application/ld+json'));
 const map=JSON.parse(read('content-map.json'));assert.equal(map.chapters.length,45);
 assert.equal(map.chapters.filter(c=>c.kind==='product').length,20);assert.equal(map.chapters.filter(c=>c.kind==='technical').length,25);
 assert.equal(map.source_chinese_characters,28939);
 const sitemap=read('sitemap.xml');assert.equal((sitemap.match(/<loc>/g)||[]).length,52);
 assert.ok(read('robots.txt').includes('Allow: /'));assert.ok(read('llms.txt').includes('wzqy2019'));assert.ok(read('llms-full.txt').length>60000);
 for(const c of map.chapters){assert.equal(c.source_text_sha256,c.rendered_text_sha256,'完整正文须与来源一致');const html=read(c.file);assert.ok(html.includes('id="chapter-content"'));assert.ok(html.includes(c.title));assert.ok(html.includes('<link rel="canonical"'));assert.ok(html.includes('index,follow'));assert.ok(!html.includes('/Users/wdx/'));assert.ok(!html.includes('{{diagram:'));assert.ok(fs.existsSync(path.join(dist,c.markdown)));}
 for(const c of [home,...map.chapters.map(c=>read(c.file)),read('guide/index.html'),read('faq/index.html')])assert.ok(c.includes('lang="zh-CN"'));
});
test('所有静态内链与资产存在，全文不依赖脚本获取',()=>{
 assert.ok(fs.existsSync(path.join(dist,'index.html')),'网站生成后验证静态资源');
 const htmlFiles=[];function walk(p){for(const d of fs.readdirSync(p,{withFileTypes:true})){const f=path.join(p,d.name);if(d.isDirectory())walk(f);else if(f.endsWith('.html'))htmlFiles.push(f);}}walk(dist);
 for(const p of htmlFiles){const html=fs.readFileSync(p,'utf8');for(const m of html.matchAll(/(?:href|src)="([^"#]+)(?:#[^"]*)?"/g)){let ref=m[1].replace(/&amp;/g,'&');if(/^(https?:|data:|mailto:|tel:)/.test(ref))continue;ref=ref.split('?')[0];let target=ref.startsWith('/')?path.join(dist,ref):path.resolve(path.dirname(p),decodeURI(ref));if(ref.endsWith('/'))target=path.join(target,'index.html');assert.ok(fs.existsSync(target),`${path.relative(dist,p)} 链接不存在：${ref}`);}assert.ok(!html.includes('fetch('),'页面正文应由静态HTML直接提供');}
 assert.equal(htmlFiles.length,56);
});
test('真实作品案例与GEO事实资料提供可直接读取的来源和联系方式',()=>{
 assert.ok(fs.existsSync(path.join(dist,'cases/index.html')),'八个头条案例需要独立公开展示页');
 const cases=read('cases/index.html');for(const id of ['7694973286849135140','7694961587576209967','7694927202420359708','7694918702332002845','7694893301542961674','7694881639034159625','7694879262158914063','7694843490920743424'])assert.ok(cases.includes(id));
 assert.ok(cases.includes('移动信息杂谈'));const facts=JSON.parse(read('product-facts.json'));assert.equal(facts.contact.wechat,'wzqy2019');assert.equal(facts.features.predefined_online_voice_count,26);assert.ok(facts.sources.length>=3);assert.ok(read('facts/index.html').includes('wzqy2019'));
 const key=fs.readFileSync(path.join(root,'seo/indexnow-key.txt'),'utf8').trim();assert.match(key,/^[a-f0-9]{32}$/);assert.equal(read(key+'.txt').trim(),key);
});
test('八个案例展示对应原片真实封面，不使用示意标题卡',()=>{
 for(const file of ['index.html','cases/index.html']){
  const html=read(file);assert.equal((html.match(/class="case-thumbnail"/g)||[]).length,8,'首页及案例页均需八张真实封面');assert.ok(!html.includes('作品标题卡'));
  for(const id of ['7694973286849135140','7694961587576209967','7694927202420359708','7694918702332002845','7694893301542961674','7694881639034159625','7694879262158914063','7694843490920743424'])assert.ok(html.includes('/assets/cases/'+id+'.jpg'));
 }
});
