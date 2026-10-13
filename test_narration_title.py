"""文案清洗和垂直中央 50% 标题：只使用隔离素材，不调用模型。"""
import io
import re
import subprocess
import time

from PIL import Image, ImageChops
import app as server
import test_workbench as fixtures


class NarrationTitleTests(fixtures.WorkbenchTests):
    def test_cleaner_rules_preserve_content_numbers_and_normal_punctuation(self):
        page = self.client.get("/").get_data(as_text=True)
        cleaner = re.search(r"function cleanNarration\(text\)\{(.*?)\n\}", page, re.S).group(0)
        assertions = r'''
const assert=require('node:assert/strict');
const cases=[
 ['## **今天**（重点）[说明]【注意】{补充}\n* 继续朗读。','今天重点说明注意补充\n继续朗读。'],
 ['[中文正文](https://example.com/path(a))，![配图](https://example.com/a.jpg)结尾。','中文正文，结尾。'],
 ['# 数据\n增长 12.5%，温度 -3℃，日期 2026-10-10。\n2*3=6，A/B，50元。','数据\n增长 12.5%，温度 -3℃，日期 2026-10-10。\n2乘以3=6，A/B，50元。'],
 ['```text\n这里是正文。\n```\n> 引用正文。\n---\n\n\n末尾。','这里是正文。\n\n引用正文。\n\n末尾。'],
 ['<b>正文</b><!--不要朗读-->\n更多内容 https://example.com/path，结束。','正文\n更多内容 ，结束。'],
 ['《书名》：“保留中文标点！”\n_斜体_ 和 `词语`，😀。','《书名》：“保留中文标点！”\n斜体 和 词语，😀。'],
 ['  \uFEFF标题\r\n\r\n\r\n【括号里面不能丢】\t尾声  ','标题\n\n括号里面不能丢 尾声'],
 ['普通文案，保留内容。','普通文案，保留内容。'],['','']
];
for(const [input,expected] of cases){const result=cleanNarration(input);assert.equal(result,expected,input);assert.equal(cleanNarration(result),result,'cleaning not idempotent');}
const long='# **长文**（不能截断）\n'.repeat(1000);const result=cleanNarration(long);assert.equal(result,'长文不能截断\n'.repeat(1000).trim());
'''
        run = subprocess.run(["/opt/homebrew/bin/node", "-"], input=cleaner + assertions, text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_cleanup_button_count_undo_and_no_backend_submission(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="cleanText"', page)
        self.assertIn('id="undoClean"', page)
        script = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
        harness = r'''
const assert=require('node:assert/strict'),els=new Map(),calls=[];
function el(id){if(!els.has(id))els.set(id,{value:'',style:{},dataset:{},scrollHeight:145,setAttribute(){},getAttribute(){return null},replaceChildren(){},add(){},removeAttribute(){},pause(){},load(){}});return els.get(id)}
global.document={getElementById:el,querySelectorAll:()=>[],createElement:()=>({}),head:{append(){}}};
global.Audio=class{pause(){}removeAttribute(){}load(){}};global.Option=class{};
global.localStorage={getItem(){return null},setItem(){},removeItem(){}};global.window={addEventListener(){}};
global.fetch=async(path,options)=>{calls.push({path,options});return {ok:true,json:async()=>({jobs:[]})}};
'''
        assertions = r'''
const original='## **你好**（保留正文），世界！';
el('text').value=original;el('text').oninput();el('cleanText').onclick();
assert.equal(el('text').value,'你好保留正文，世界！');assert.equal(el('count').textContent,'10 字');assert.equal(el('undoClean').disabled,false);
el('cleanText').onclick();el('undoClean').onclick();assert.equal(el('text').value,original);assert.equal(el('count').textContent,Array.from(original).length+' 字');assert.equal(el('undoClean').disabled,true);
el('cleanText').onclick();el('text').value+='用户新补充';el('text').oninput();assert.equal(el('undoClean').disabled,true);el('undoClean').onclick();assert.ok(el('text').value.endsWith('用户新补充'));
el('cleanText').onclick();clearWorkspaceUI();assert.equal(lastCleanup,null);assert.equal(el('cleanStatus').textContent,'');assert.equal(el('undoClean').disabled,true);
el('cleanText').onclick();assert.ok(el('cleanStatus').textContent.includes('请先输入'));assert.equal(el('count').textContent,'0 字');
assert.ok(calls.every(call=>!call.options?.method||call.options.method==='GET'),'cleaning submitted request');
'''
        run = subprocess.run(["/opt/homebrew/bin/node", "-"], input=harness + script + assertions, text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_legacy_task_recomposition_adopts_yellow_unified_title(self):
        identifier = self.create_job(title="垂直中间标题红黄大字加粗清楚展示")
        server.update(identifier, title_style="legacy", title_secondary="")
        version = self.ready(identifier)
        path = server.image_paths(server.read_job(identifier))[0]
        Image.new("RGB", (640, 360), "black").save(path)
        self.assertEqual(self.post(f"/api/jobs/{identifier}/confirm", {"image_version": version}).status_code, 200)
        deadline = time.monotonic() + 45
        while server.ACTIVE and time.monotonic() < deadline:
            time.sleep(.05)
        job = server.read_job(identifier)
        self.assertEqual(job["video_state"], "done", job["video_message"])
        video = server.video_path(job)
        info = server.probe(video)
        self.assertLess(abs(float(info["format"]["duration"]) - job["audio_duration"]), 1.5)
        frame = subprocess.run(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-ss", str(job["audio_duration"] / 2), "-i", str(video), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True)
        with Image.open(io.BytesIO(frame.stdout)) as decoded:
            red, green, blue = decoded.convert("RGB").split()
            red_mask = ImageChops.multiply(red.point(lambda p: 255 if p > 190 else 0),
                                          ImageChops.multiply(green.point(lambda p: 255 if p < 110 else 0), blue.point(lambda p: 255 if p < 110 else 0)))
            yellow_mask = ImageChops.multiply(red.point(lambda p: 255 if p > 190 else 0),
                                             ImageChops.multiply(green.point(lambda p: 255 if p > 150 else 0), blue.point(lambda p: 255 if p < 110 else 0)))
            self.assertEqual(decoded.size, (1920, 1080))
            self.assertIsNone(red_mask.getbbox())
            for mask in (yellow_mask,):
                bounds = mask.getbbox()
                self.assertIsNotNone(bounds)
                self.assertGreaterEqual(bounds[1], 259)
                self.assertLessEqual(bounds[3], 832)
                self.assertGreaterEqual(bounds[0], 96)
                self.assertLessEqual(bounds[2], 1824)
        with self.client.get(f"/api/jobs/{identifier}/media/video?download=1") as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn('attachment', response.headers['Content-Disposition'])


for name in dir(fixtures.WorkbenchTests):
    if name.startswith("test_") and name not in NarrationTitleTests.__dict__:
        setattr(NarrationTitleTests, name, None)
