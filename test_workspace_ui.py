"""清空工作区、折叠音色、长文与全部视频回归；只用隔离临时任务。"""
import hashlib
import os
import re
import subprocess
import uuid
from unittest.mock import patch

import app as server
import test_workbench as fixtures


class WorkspaceUITests(fixtures.WorkbenchTests):
    def test_archive_lists_preserved_videos_independent_of_job_state(self):
        identifier = self.create_job()
        directory = server.folder(identifier)
        for name in ("旧版成片.mp4", "新版成片.mp4", "video.pending.mp4", "空文件.mp4"):
            (directory / name).write_bytes(b"" if name == "空文件.mp4" else b"test-video")
        # 换图后视频状态 idle，不应让仍存在的旧成片消失。
        server.update(identifier, video_state="idle")
        orphan = server.JOBS / str(uuid.uuid4())
        orphan.mkdir()
        (orphan / "无任务元数据.mp4").write_bytes(b"old")
        invalid = server.JOBS / "not-a-job"
        invalid.mkdir()
        (invalid / "ignored.mp4").write_bytes(b"ignored")
        (directory / "链接.mp4").symlink_to(orphan / "无任务元数据.mp4")
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.glob("*.mp4")}
        data = self.client.get("/api/videos").get_json()
        self.assertEqual(data["total"], 3)
        self.assertEqual({v["filename"] for v in data["videos"]}, {"旧版成片.mp4", "新版成片.mp4", "无任务元数据.mp4"})
        self.assertEqual(data["root_folder"], str(server.JOBS))
        for item in data["videos"]:
            with self.client.get(item["media_url"]) as response:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, "video/mp4")
            with self.client.get(item["media_url"] + "&download=1") as response:
                self.assertIn("attachment", response.headers["Content-Disposition"])
            with self.client.get(item["media_url"], headers={"Range": "bytes=0-2"}) as response:
                self.assertEqual(response.status_code, 206)
                self.assertEqual(len(response.data), 3)
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.glob("*.mp4")})

    def test_archive_reveal_is_authenticated_and_path_scoped(self):
        identifier = self.create_job()
        path = server.folder(identifier) / "成片.mp4"
        path.write_bytes(b"media")
        endpoint = f"/api/videos/{identifier}/reveal"
        self.assertEqual(self.client.post(endpoint, json={"filename": path.name}).status_code, 403)
        with patch.object(server.subprocess, "Popen") as opened:
            self.assertEqual(self.post(endpoint, {"filename": path.name}).status_code, 200)
            opened.assert_called_once_with(["open", "-R", str(path)])
            self.assertEqual(self.post("/api/videos/reveal-root").status_code, 200)
            self.assertEqual(opened.call_args.args[0], ["open", str(server.JOBS)])
            for name in ["../app.py", "video.pending.mp4", "不存在.mp4", "x\\\\y.mp4", None, "成片.mp4/"]:
                self.assertEqual(self.post(endpoint, {"filename": name}).status_code, 404)
            self.assertEqual(opened.call_count, 2)
        path.unlink()
        self.assertEqual(self.post(endpoint, {"filename": path.name}).status_code, 404)

    def test_full_long_text_is_saved_for_conversion(self):
        text = "这是超过一千字的完整口播文案。" * 400
        identifier = self.create_job(text=text)
        self.assertEqual(server.read_job(identifier)["text"], text)
        self.assertGreater(len(text), 1000)

    def test_task_buttons_keep_access_to_saved_mp4_after_state_reset(self):
        identifier = self.create_job()
        path = server.video_path(server.read_job(identifier))
        path.write_bytes(b"saved-mp4")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        for state in ["idle", "queued", "working", "failed", "cancelled", "done"]:
            server.update(identifier, video_state=state)
            data = self.client.get(f"/api/jobs/{identifier}").get_json()
            self.assertEqual(data["video_state"], state)
            self.assertEqual(data["video_path"], str(path))
            with self.client.get(data["video_url"] + "&download=1") as download:
                self.assertEqual(download.status_code, 200)
                self.assertIn("attachment", download.headers["Content-Disposition"])
            with patch.object(server.subprocess, "Popen") as opened:
                self.assertEqual(self.post(f"/api/jobs/{identifier}/reveal", {"kind": "video"}).status_code, 200)
                opened.assert_called_once_with(["open", "-R", str(path)])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        path.unlink()
        data = self.client.get(f"/api/jobs/{identifier}").get_json()
        self.assertEqual(data["video_path"], "")
        self.assertEqual(data["video_url"], "")
        with patch.object(server.subprocess, "Popen") as opened:
            self.assertEqual(self.post(f"/api/jobs/{identifier}/reveal", {"kind": "video"}).status_code, 409)
            opened.assert_not_called()

    def test_video_controls_are_outside_the_clearable_task(self):
        from html.parser import HTMLParser
        parents = {}
        attrs_by_id = {}
        order = []
        class ControlsParser(HTMLParser):
            stack = []
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if "id" in attrs:
                    parents[attrs["id"]] = [identifier for _, identifier in self.stack]
                    attrs_by_id[attrs["id"]] = attrs
                    order.append(attrs["id"])
                if tag not in {"input", "img", "br", "meta", "link", "hr"}:
                    self.stack.append((tag, attrs.get("id")))
            def handle_endtag(self, tag):
                if self.stack and self.stack[-1][0] == tag:
                    self.stack.pop()
        ControlsParser().feed(self.client.get("/").get_data(as_text=True).split("<script>")[0])
        self.assertIn("compositionActions", parents["confirm"])
        self.assertNotIn("task", parents["confirm"])
        self.assertLess(order.index("confirm"), order.index("audio"))
        self.assertEqual(order.count("confirm"), 1)
        self.assertNotIn("open", attrs_by_id["imageAdjustments"])
        for identifier in ["replacePhoto", "replacePhotoUrl", "regeneratePanel", "changeSource"]:
            self.assertIn("imageAdjustments", parents[identifier])
        for identifier in ["showVideoFiles", "openVideoFolder"]:
            self.assertIn("mp4Tools", parents[identifier])
            self.assertNotIn("task", parents[identifier])

    def test_workspace_frontend_refresh_voice_and_scroll(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertNotIn('maxlength=', re.search(r'<textarea[^>]*id="text"[^>]*>', page).group(0))
        self.assertIn("max-height:420px", page)
        self.assertIn("overflow-y:auto", page)
        script = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
        harness = """
const els=new Map();function el(id){if(!els.has(id))els.set(id,{value:'',hidden:true,style:{},dataset:{},scrollHeight:145,attributes:{},setAttribute(k,v){this.attributes[k]=v},getAttribute(k){return this.attributes[k]||null},replaceChildren(){this.children=[]},add(){},removeAttribute(k){delete this.attributes[k]},pause(){this.stopped=true},load(){},scrollIntoView(){}});return els.get(id)}
const cards=['zh-CN-YunjianNeural','zh-CN-XiaoxiaoNeural'].map((id,i)=>({dataset:{voice:id},childNodes:[{textContent:i?'晓晓':'云健'}],classList:{toggle(){}},setAttribute(){}}));
const values=new Map([['shengying-workbench-voice','zh-CN-XiaoxiaoNeural'],['shengying-workbench-image-mode','upload'],['shengying-workbench-image-provider','seedream'],['shengying-workbench-last','old-id'],['tingwen-web-import-last','old-import'],['unrelated','keep']]);
global.localStorage={getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
global.document={getElementById:el,querySelectorAll:()=>cards,createElement:()=>({}),head:{append(){}}};
global.Audio=class{pause(){}play(){this.played=true;return Promise.resolve()}removeAttribute(){}load(){}};
global.Option=class{};
const calls=[];global.fetch=async(path,options)=>{calls.push([path,options]);return {ok:true,json:async()=>({jobs:[]})}};
global.window={addEventListener(){},location:{reload(){global.reloaded=true}}};
"""
        assertions = """
if(!el('voicePanel').hidden||!el('currentVoice').textContent.includes('晓晓'))throw Error('voice not remembered/collapsed');
if(el('imageMode').value!=='upload'||el('provider').value!=='seedream'||el('localPhotoSettings').hidden||!el('aiPhotoSettings').hidden||el('consent').checked)throw Error('image preferences/paid guard not restored');
el('imageMode').value='url';el('imageMode').onchange();if(values.get('shengying-workbench-image-mode')!=='url'||el('urlPhotoSettings').hidden)throw Error('image choice not remembered');
el('provider').value='drawthings';el('provider').onchange();if(values.get('shengying-workbench-image-provider')!=='drawthings')throw Error('image source not remembered');
el('imageMode').value='generate';el('provider').value='seedream';restoreImagePreferences();if(el('imageMode').value!=='url'||el('provider').value!=='drawthings')throw Error('reload resets image preferences');
el('toggleVoices').onclick();if(el('voicePanel').hidden)throw Error('cannot expand voices');
cards[0].onclick();if(!el('voicePanel').hidden||values.get('shengying-workbench-voice')!=='zh-CN-YunjianNeural'||!preview.played)throw Error('selection must play, remember and collapse');
el('text').value='中文😀\\n A';el('text').oninput();if(el('count').textContent!=='6 字')throw Error('Unicode count splits emoji');
el('text').value='文'.repeat(5000);el('text').scrollHeight=2400;el('text').oninput();
if(el('text').style.height!=='420px'||el('text').value.length!==5000||el('count').textContent!=='5,000 字'||!el('textHint').textContent.includes('转换使用全文'))throw Error('long text clipped or unbounded');
el('text').value='短文';el('text').scrollHeight=145;el('text').oninput();if(el('text').style.height!=='145px')throw Error('short text cannot shrink');
selectedId='old';job={id:'old'};el('audio').attributes.src='old.mp3';el('video').attributes.src='old.mp4';el('task').hidden=false;
clearWorkspaceUI();
if(selectedId||job||el('text').value||!el('task').hidden||el('empty').hidden||el('audio').getAttribute('src')||el('video').getAttribute('src'))throw Error('refresh retains content');
if(values.has('shengying-workbench-last')||values.has('tingwen-web-import-last')||!values.has('shengying-workbench-voice')||values.get('unrelated')!=='keep')throw Error('wrong cache keys cleared');
if(el('imageMode').value!=='url'||el('provider').value!=='drawthings'||!el('confirm').disabled||!el('confirm').textContent.includes('生成 MP4'))throw Error('clear lost preferences or MP4 controls');
render({id:'test',title:'旧文件',text:'口播',prompt:'山水',provider:'manual',audio_state:'ready',image_state:'ready',video_state:'idle',video_path:'/saved.mp4',video_url:'/api/videos/test/media?filename=old.mp4',image_version:'v1'});
if(el('imageAdjustments').open||el('confirm').disabled)throw Error('new task not collapsed or ready MP4 disabled');
el('imageAdjustments').open=true;render({...job});if(!el('imageAdjustments').open)throw Error('poll closes optional settings');
render({...job,id:'another',audio_state:'working'});if(el('imageAdjustments').open||!el('confirm').disabled)throw Error('switch not collapsed or unfinished audio can compose');
if(el('videoDownload').hidden||el('revealVideo').hidden||el('video').hidden||!el('videoDownload').href.includes('download=1')||!el('videoMessage').textContent.includes('上次已生成'))throw Error('preserved MP4 buttons missing or not labelled');
el('imageAdjustments').open=true;clearWorkspaceUI();if(el('imageAdjustments').open)throw Error('clear keeps optional settings expanded');
if(calls.some(([path,options])=>options?.method==='POST'||path.includes('old')))throw Error('refresh mutated/restored backend task');
el('clearWorkspace').onclick();if(!global.reloaded)throw Error('refresh button missing');
(async()=>{el('file').files=[{name:'文案.txt',text:async()=>'文字😀\\n'}];await el('file').onchange();if(el('count').textContent!=='4 字')throw Error('import count stale');clearWorkspaceUI();if(el('count').textContent!=='0 字')throw Error('clear count stale');el('title').value='画面标题';el('title').oninput();if(!el('titleHint').textContent.includes('取文案前 30 字')||!el('titleHint').textContent.includes('已有音频未填文案时取音频文件名'))throw Error('filename hint stale');})().catch(e=>{console.error(e);process.exitCode=1});
"""
        result = subprocess.run(["/opt/homebrew/bin/node", "-"], input=harness+script+assertions,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_red_character_count_is_beside_narration_label(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertEqual(page.count('id="count"'), 1)
        self.assertRegex(page, r'<label for="text">朗读文案\s*<span id="count"[^>]*>0 字</span></label>')
        self.assertIn('aria-describedby="count textHint"', page)
        self.assertRegex(page, r'#count\{color:#b91c1c;font-size:18px;font-weight:800;')
        self.assertNotRegex(page, r'<div class="row"><button id="import"[^<]*</button>\s*<(?:small|span) id="count"')


for name in dir(fixtures.WorkbenchTests):
    if name.startswith("test_") and name not in WorkspaceUITests.__dict__:
        setattr(WorkspaceUITests, name, None)
