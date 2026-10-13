"""刷新、清空及新页面恢复样式偏好，不恢复工作区内容。"""
import json
import re
import subprocess
import unittest
import app as server


class StylePreferenceTests(unittest.TestCase):
    def run_ui(self, assertions):
        html = server.app.test_client().get('/').get_data(as_text=True)
        script = re.search(r'<script>(.*?)</script>', html, re.S).group(1)
        harness = r'''
const vm=require('node:vm'),assert=require('node:assert/strict'),values=new Map();let denyStorage=false;
function load(){
 const els=new Map(),handlers={},calls=[];
 function el(id){if(!els.has(id))els.set(id,{value:'',style:{},dataset:{},scrollHeight:145,options:[],attributes:{},setAttribute(k,v){this.attributes[k]=v},getAttribute(k){return this.attributes[k]||null},removeAttribute(k){delete this.attributes[k]},replaceChildren(){},add(){},pause(){},load(){}});return els.get(id)}
 const context={document:{getElementById:el,querySelectorAll:()=>[],createElement:()=>({}),head:{append(){}}},Audio:class{pause(){}removeAttribute(){}load(){}},Option:class{},clearInterval(){},setInterval(){return 1},setTimeout(){return 1},clearTimeout(){},
  localStorage:{getItem(k){if(denyStorage)throw Error('denied');return values.get(k)||null},setItem(k,v){if(denyStorage)throw Error('denied');values.set(k,v)},removeItem(k){values.delete(k)}},
  window:{addEventListener(event,fn){handlers[event]=fn}},
  fetch:async(path,options)=>{const body=options?.body?JSON.parse(options.body):null;calls.push({path,body});return {ok:true,json:async()=>path==='/api/jobs'&&body?{id:'fresh',text:body.text,title:body.title,title_settings:body.title_settings,subtitle_settings:body.subtitle_settings,title_style:'clean-yellow',provider:'manual',audio_state:'queued',image_state:'ready',video_state:'idle'}:{jobs:[]}}}
 };
 vm.createContext(context);vm.runInContext(SCRIPT,context);
 return {el,handlers,calls,run:code=>vm.runInContext(code,context),settings:()=>JSON.parse(vm.runInContext('JSON.stringify({title:titleSettings(),subtitle:subtitleSettings()})',context))};
}
'''
        result = subprocess.run(['/opt/homebrew/bin/node', '-'], input='const SCRIPT=' + json.dumps(script) + ';\n' + harness + '\n(async()=>{\n' + assertions + '\n})().catch(e=>{console.error(e);process.exitCode=1});', text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_clear_pageshow_reload_and_new_task_reuse_last_settings(self):
        self.run_ui(r'''
const first=load();
first.el('titleFont').value='song';first.el('titleColor').value='#ffcc33';first.el('titleSize').value='180';first.el('titleSize').oninput();
first.el('subtitleFont').value='hei';first.el('subtitleColor').value='#44ddff';first.el('subtitleSize').value='72';first.el('subtitlePosition').value='65';first.el('subtitleEnabled').checked=false;first.el('subtitleFont').onchange();
const expected=first.settings();
assert.equal(JSON.parse(values.get('shengying-workbench-subtitle-style')).position,65);
first.el('title').value='草稿标题';first.el('text').value='草稿文案';first.run('clearWorkspaceUI()');
assert.deepEqual(first.settings(),expected);assert.equal(first.el('title').value,'');assert.equal(first.el('text').value,'');
first.handlers.pageshow();assert.deepEqual(first.settings(),expected);
const reopened=load();assert.deepEqual(reopened.settings(),expected);assert.equal(reopened.el('text').value,'');
reopened.el('subtitleBottom').dataset.position='90';reopened.el('subtitleBottom').onclick();assert.equal(JSON.parse(values.get('shengying-workbench-subtitle-style')).position,90);
reopened.el('imageMode').value='url';reopened.el('photoUrl').value='https://example.com/photo.png';reopened.el('text').value='新文案';await reopened.el('generate').onclick();
const created=reopened.calls.find(c=>c.path==='/api/jobs'&&c.body);assert.ok(created);assert.deepEqual(created.body.title_settings,expected.title);assert.equal(created.body.subtitle_settings.color,'#44DDFF');assert.equal(created.body.subtitle_settings.position,90);assert.equal(created.body.subtitle_settings.enabled,false);
''')

    def test_viewing_history_does_not_replace_user_preferences(self):
        self.run_ui(r'''
const title={font:'song',color:'#FFCC33',size:180},subtitle={enabled:true,font:'hei',color:'#44DDFF',size:72,position:65};
values.set('shengying-workbench-title-style',JSON.stringify(title));values.set('shengying-workbench-subtitle-style',JSON.stringify(subtitle));
const page=load();const before=[...values.entries()];
page.run("render({id:'history',title:'历史标题',text:'历史文案',prompt:'',provider:'manual',audio_state:'ready',image_state:'ready',video_state:'idle',title_style:'clean-yellow',title_settings:{font:'sans-bold',color:'#FFDA35',size:265},subtitle_settings:{enabled:true,font:'song',color:'#FFFFFF',size:36,position:10}})");
assert.equal(page.settings().subtitle.color,'#FFFFFF');assert.deepEqual([...values.entries()],before);
page.run('clearWorkspaceUI()');assert.deepEqual(page.settings(),{title,subtitle});assert.equal(page.el('task').hidden,true);
''')

    def test_invalid_saved_values_and_unavailable_storage_are_handled(self):
        self.run_ui(r'''
values.set('shengying-workbench-title-style','not-json');values.set('shengying-workbench-subtitle-style',JSON.stringify({enabled:'yes',font:'uninstalled',color:'#00aa88',size:999,position:-1,unused:'ignored'}));
const page=load();assert.equal(page.settings().title.font,'sans-bold');assert.equal(page.settings().subtitle.font,'sans');assert.equal(page.settings().subtitle.color,'#00AA88');assert.equal(page.settings().subtitle.size,48);assert.equal(page.settings().subtitle.position,90);assert.equal(page.settings().subtitle.enabled,true);
values.set('shengying-workbench-title-style',JSON.stringify({font:'song',color:'invalid',size:180}));const partial=load();assert.equal(partial.settings().title.font,'song');assert.equal(partial.settings().title.color,'#FFDA35');assert.equal(partial.settings().title.size,180);
denyStorage=true;const blocked=load();blocked.el('subtitleColor').value='#44ddff';blocked.el('subtitleColor').oninput();assert.equal(blocked.settings().subtitle.color,'#44DDFF');assert.ok(blocked.el('stylePreferencesHint').textContent.includes('无法保存'));
''')
