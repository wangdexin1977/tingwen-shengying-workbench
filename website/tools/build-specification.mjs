import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { marked } from 'marked';

export function buildSpecification({shell,write,config}) {
 const root=process.cwd(),source=path.join(root,'../docs/产品规格说明书.md');
 const markdown=fs.readFileSync(source,'utf8');
 write('assets/diagrams/shengying-architecture.svg',fs.readFileSync(path.join(root,'src/assets/shengying-architecture.svg'),'utf8'));
 const audit=fs.readFileSync(path.join(root,'../docs/接口与参数清单.json'),'utf8');
 let article=marked.parse(markdown.replace(/^# .*\n/, '').replace(/```mermaid[\s\S]*?```/, '<figure class="spec-diagram"><img src="/assets/diagrams/shengying-architecture.svg" alt="听文声影工作台架构：文案、语音模型、配图、队列、FFmpeg 与头条交接"><figcaption>本机制作与平台发布交接的实际模块关系。</figcaption></figure>'));
 const toc=[];let count=0;
 article=article.replace(/<h2>([\s\S]*?)<\/h2>/g,(_,title)=>{const id='spec-section-'+(++count);toc.push({id,title});return `<h2 id="${id}">${title}</h2>`;});
 // 说明书为版本管理的固定内容，不接收网页用户输入；代码和表格均由 Markdown 转义。
 article=article.replaceAll('href="接口与参数清单.json"','href="/downloads/shengying-api.json"');
 const rail=toc.map(item=>`<a href="#${item.id}">${item.title}</a>`).join('');
 const actions='<div class="knowledge-actions"><a href="/downloads/shengying-specification.md">下载 Markdown 全文 ↗</a><a href="/downloads/shengying-specification.html">离线 HTML / 打印 PDF ↗</a><a href="/downloads/shengying-api.json">接口与参数 JSON ↗</a></div>';
 const body=`<section class="knowledge-hero"><div class="wrap"><p class="eyebrow">当前版本 · 产品、使用与技术全规格</p><h1>听文声影工作台产品规格说明书</h1><p>产品 ${config.version} · 2026-10-13 · 26 个主体章节＋完整参数与接口附录。说明书根据源码编写，涵盖自媒体价值、架构、所有工作区操作及模型调用。</p>${actions}</div></section><div class="wrap spec-layout"><aside class="spec-rail" aria-label="规格说明书目录"><strong>说明书目录</strong>${rail}</aside><article class="chapter-content specification-body">${article}</article></div>`;
 write('specification/index.html',shell({title:'产品规格说明书 · 模型、架构、使用与自媒体价值',description:'听文声影工作台1.2.2完整规格：六种语音后端、录音克隆、文档章节、配图、字幕音乐、MP3、1080p MP4、夜间队列、头条交接及46条接口。',url:'/specification/',body,schemas:[{'@type':'TechArticle',headline:'听文声影工作台产品规格说明书',dateModified:config.date,inLanguage:'zh-CN',url:config.origin+'/specification/'}]}));
 write('downloads/shengying-specification.md',markdown);
 write('downloads/shengying-api.json',audit);
 let offline=shell({title:'产品规格说明书 · 离线阅读与打印',description:'听文声影工作台完整产品规格离线HTML，内容与Markdown相同，可在浏览器打印保存PDF。',url:'/downloads/shengying-specification.html',noindex:true,body:`<div class="wrap reading-main spec-offline"><h1>听文声影工作台产品规格说明书</h1><div class="chapter-tools"><button id="print-page">打印 / 保存 PDF</button><a href="/specification/">返回在线目录</a></div><article class="chapter-content specification-body">${article}</article></div>`});
 const css=fs.readFileSync(path.join(root,'src/site.css'),'utf8');
 const diagram=fs.readFileSync(path.join(root,'src/assets/shengying-architecture.svg')).toString('base64');
 const favicon=fs.readFileSync(path.join(root,'static-output/assets/favicon.svg')).toString('base64');
 offline=offline.replace('<link rel="stylesheet" href="/assets/site.css">',`<style>${css}\n.site-header,.site-footer,dialog{display:none}</style>`).replaceAll('src="/assets/diagrams/shengying-architecture.svg"',`src="data:image/svg+xml;base64,${diagram}"`).replace('href="/assets/favicon.svg"',`href="data:image/svg+xml;base64,${favicon}"`).replace('<script src="/assets/site.js" defer></script>','<script>document.getElementById("print-page").addEventListener("click",()=>window.print());</script>').replace('href="/specification/"',`href="${config.origin}/specification/"`);
 write('downloads/shengying-specification.html',offline);
 write('specification-source.json',JSON.stringify({product_version:config.version,updated_at:config.date,markdown_sha256:crypto.createHash('sha256').update(markdown).digest('hex'),headings:toc.length,api_routes:JSON.parse(audit).routes.length},null,2)+'\n');
 return {markdown,toc};
}
