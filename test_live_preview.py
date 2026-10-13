"""PC 实时样式预览：事件联动、草稿/任务素材及异步旧响应保护。"""
import re
import subprocess
import unittest
import app as server


class LivePreviewTests(unittest.TestCase):
    def run_ui(self, assertions):
        html = server.app.test_client().get('/').get_data(as_text=True)
        self.assertEqual(html.count('id="titlePreviewImage"'), 1)
        self.assertEqual(html.count('id="subtitlePreviewImage"'), 1)
        self.assertRegex(html, r'id="stylePreview"[\s\S]*?id="titlePreviewImage"[\s\S]*?id="subtitlePreviewImage"[\s\S]*?</div>')
        script = re.search(r'<script>(.*?)</script>', html, re.S).group(1)
        harness = r'''
const assert=require('node:assert/strict'),els=new Map(),timers=new Map(),calls=[];let tick=0,hold=false,resolveOld;
function el(id){if(!els.has(id))els.set(id,{value:'',style:{},dataset:{},scrollHeight:145,options:[],attributes:{},setAttribute(k,v){this.attributes[k]=v},getAttribute(k){return this.attributes[k]||null},removeAttribute(k){delete this.attributes[k];if(k==='src')delete this.src},replaceChildren(){},add(){},pause(){},load(){}});return els.get(id)}
global.document={getElementById:el,querySelectorAll:()=>[],createElement:()=>({}),head:{append(){}}};
global.Audio=class{pause(){}removeAttribute(){}load(){}};global.Option=class{};
global.localStorage={getItem(){return null},setItem(){},removeItem(){}};global.window={addEventListener(){}};
global.setInterval=()=>1;global.clearInterval=()=>{};
global.setTimeout=fn=>{const id=++tick;timers.set(id,fn);return id};global.clearTimeout=id=>timers.delete(id);
global.fetch=async(path,options)=>{const body=options?.body?JSON.parse(options.body):null;calls.push({path,body});
 if(hold&&path==='/api/title/preview'){hold=false;return new Promise(resolve=>resolveOld=()=>resolve({ok:true,json:async()=>({image:'STALE',actual_size:120})}))}
 return {ok:true,json:async()=>path.includes('/preview')?{image:JSON.stringify(body),actual_size:body.settings.size}:{jobs:[]}}};
async function drain(){while(timers.size){const batch=[...timers.values()];timers.clear();for(const fn of batch)await fn();}}
'''
        result = subprocess.run(['/opt/homebrew/bin/node', '-'], input=harness + script + '\n(async()=>{\n' + assertions + '\n})().catch(e=>{console.error(e);process.exitCode=1});', text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_empty_title_stays_hidden_then_follows_user_input_and_style(self):
        self.run_ui(r'''
await drain();
assert.equal(el('titlePreview').hidden,true);assert.equal(el('titlePreviewImage').hidden,true);
assert.equal(el('titlePreviewImage').src,undefined);assert.ok(!calls.some(c=>c.path==='/api/title/preview'));
assert.equal(el('title').value,'');
el('title').value='新标题';el('title').oninput();el('titleFont').value='song';el('titleFont').onchange();
el('titleColor').value='#009944';el('titleColor').oninput();
el('text').value='这是草稿口播';el('text').oninput();el('subtitleFont').value='song';el('subtitleFont').onchange();
el('subtitleColor').value='#ffaa44';el('subtitleColor').onchange();await drain();
const title=JSON.parse(el('titlePreviewImage').src),sub=JSON.parse(el('subtitlePreviewImage').src);
assert.equal(el('titlePreview').hidden,false);assert.equal(el('titlePreviewImage').hidden,false);
assert.equal(title.title,'新标题');assert.equal(title.settings.font,'song');assert.equal(title.settings.color,'#009944');
assert.equal(sub.text,'这是草稿口播');assert.equal(sub.settings.font,'song');assert.equal(sub.settings.color,'#FFAA44');
assert.equal(el('titleFont').style.color,'#009944');assert.equal(el('subtitleFont').style.color,'#FFAA44');
assert.equal(el('subtitleFontSample').style.color,'#FFAA44');assert.equal(el('subtitleColorSwatch').style.backgroundColor,'#FFAA44');assert.equal(el('subtitleColorCode').textContent,'#FFAA44');
el('subtitleColor').value='#ffffff';el('subtitleColor').oninput();assert.equal(el('subtitleFont').style.backgroundColor,'#23334C');
el('subtitleColor').value='#000000';el('subtitleColor').oninput();assert.equal(el('subtitleFont').style.backgroundColor,'#F8FAFC');assert.equal(el('stylePreview').style.backgroundColor,'#E5E9EF');
el('title').value='';el('title').oninput();await drain();
assert.equal(el('titlePreview').hidden,true);assert.equal(el('titlePreviewImage').src,undefined);
el('subtitleEnabled').checked=false;el('subtitleEnabled').onchange();await drain();
assert.equal(el('subtitlePreview').hidden,true);assert.equal(el('subtitlePreviewImage').hidden,true);assert.equal(el('subtitlePreviewImage').src,undefined);
assert.ok(calls.every(c=>c.path==='/api/jobs'||c.path==='/api/omnivoice'||c.path==='/api/music'||c.path.endsWith('/preview')));
''')

    def test_task_uses_saved_text_and_photo_and_hides_missing_title(self):
        self.run_ui(r'''
await drain();el('title').value='左侧新草稿';el('text').value='左侧文案';
job={id:'saved',title:'',text:'当前任务文案',image_version:'v1',gallery:[{preview_url:'/saved-photo.png'}]};
updateTitlePreview();updateSubtitleUI();await drain();
assert.equal(el('titlePreview').hidden,true);assert.equal(el('titlePreviewImage').src,undefined);assert.equal(job.title,'');
assert.equal(JSON.parse(el('subtitlePreviewImage').src).text,'当前任务文案');assert.equal(el('previewPhoto').src,'/saved-photo.png');assert.equal(el('previewPhoto').hidden,false);
job=null;selectedLocalPhotos=[{url:'blob:draft-photo'}];updateTitlePreview();await drain();assert.equal(el('previewPhoto').src,'blob:draft-photo');
selectedLocalPhotos=[];updateTitlePreview();await drain();assert.equal(el('previewPhoto').hidden,true);assert.equal(el('previewPhoto').src,undefined);
''')

    def test_delayed_response_cannot_overwrite_new_font_or_color(self):
        self.run_ui(r'''
el('title').value='实际标题';el('title').oninput();await drain();hold=true;el('titleColor').value='#ff0000';el('titleColor').oninput();
const fn=timers.get(titlePreviewTimer);timers.delete(titlePreviewTimer);const old=fn();
el('titleColor').value='#00ff00';el('titleFont').value='song';el('titleFont').onchange();await drain();
const expected=el('titlePreviewImage').src;assert.equal(JSON.parse(expected).settings.color,'#00FF00');
resolveOld();await old;assert.equal(el('titlePreviewImage').src,expected);
hold=true;el('titleFont').onchange();const laterFn=timers.get(titlePreviewTimer);timers.delete(titlePreviewTimer);const later=laterFn();
el('title').value='';el('title').oninput();await drain();resolveOld();await later;
assert.equal(el('titlePreview').hidden,true);assert.equal(el('titlePreviewImage').hidden,true);assert.equal(el('titlePreviewImage').src,undefined);
''')
