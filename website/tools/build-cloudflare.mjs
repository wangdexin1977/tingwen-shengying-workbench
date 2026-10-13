import fs from 'node:fs';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
const root=process.cwd();
for(const script of ['tools/build-site.mjs','tools/prepare-counter-content.mjs']){
 const result=spawnSync(process.execPath,[script],{cwd:root,stdio:'inherit'});
 if(result.status!==0)process.exit(result.status||1);
}
const output=path.join(root,'dist');
fs.rmSync(output,{recursive:true,force:true});
fs.cpSync(path.join(root,'static-output'),output,{recursive:true});
const content=fs.readFileSync(path.join(root,'server/generated-content.mjs'),'utf8').replace(/^export default /,'const contents = ');
const handler=fs.readFileSync(path.join(root,'server/visit-counter.mjs'),'utf8');
fs.writeFileSync(path.join(output,'_worker.js'),content+'\n'+handler+'\nexport default {async fetch(request,env){const response=await handleContentRequest(request,env,contents);return response??env.ASSETS.fetch(request);}};\n');
const key=fs.readFileSync(path.join(root,'seo/indexnow-key.txt'),'utf8').trim();
fs.writeFileSync(path.join(output,'_routes.json'),JSON.stringify({version:1,include:['/*'],exclude:['/assets/*','/robots.txt','/'+key+'.txt']},null,2)+'\n');
console.log('Cloudflare Pages worker and media ready.');
