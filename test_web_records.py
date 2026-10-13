"""网址/照片筛选/持久化记录回归，网络仅使用模拟，不调用收费模型。"""
import hashlib
import io
import json
import subprocess
import threading
import time
import uuid
import gzip
import photo_import
from urllib.parse import urlsplit
import asyncio
import re
from speech import synthesize
from unittest.mock import patch

from PIL import Image
import app as server
import records
from photo_import import PhotoError
import test_workbench as fixtures


def png(color):
    output = io.BytesIO()
    Image.new("RGB", (640, 360), color).save(output, "PNG")
    return output.getvalue()


class WebRecordTests(fixtures.WorkbenchTests):
    # 只运行新用例，原用例在 test_workbench 中单独执行。
    def gallery_source(self, colors=("red", "green", "blue")):
        # 历史多图仍应能筛选和合成；不再依赖已撤下的网页导入接口。
        directory = server.JOBS.parent / str(uuid.uuid4())
        directory.mkdir()
        items = []
        for i, color in enumerate(colors):
            path = directory / f"{i}.png"
            path.write_bytes(png(color))
            items.append((path, {"id": uuid.uuid4().hex,
                                "metadata": {"provider": "webpage", "source_name": "example.com", "dimensions": [640, 360]}}))
        return items

    def test_compressed_public_html_is_decoded_with_bounded_size(self):
        html = b'<article><p>public narration</p><img src="/photo.png"></article>'
        class Response:
            status = 200
            def getheader(self, key):
                return {"Content-Type": "text/html", "Content-Encoding": "gzip"}.get(key)
            def read(self, limit):
                return gzip.compress(html)
        class Connection:
            def __init__(self, *args): pass
            def request(self, *args, **kwargs): pass
            def getresponse(self): return Response()
            def close(self): pass
        with patch.object(photo_import, "public_target", return_value=(urlsplit("https://example.com/"), "example.com", 443, "93.184.216.34")), patch.object(photo_import, "PinnedHTTPS", Connection):
            raw, _, _ = photo_import.fetch_public("https://example.com/", accept="text/html", max_bytes=1000)
            self.assertEqual(raw, html)
            with self.assertRaises(PhotoError):
                photo_import.fetch_public("https://example.com/", accept="text/html", max_bytes=60)

    def test_public_transport_prefers_ipv4_only_after_all_addresses_are_safe(self):
        answers = [(0, 0, 0, "", ("2a04:4e42:d000::223", 443)), (0, 0, 0, "", ("167.82.8.223", 443))]
        with patch.object(photo_import.socket, "getaddrinfo", return_value=answers):
            self.assertEqual(photo_import.public_target("https://www.python.org/")[-1], "167.82.8.223")
        with patch.object(photo_import.socket, "getaddrinfo", return_value=answers + [(0, 0, 0, "", ("127.0.0.1", 443))]):
            with self.assertRaises(PhotoError):
                photo_import.public_target("https://www.python.org/")

    def test_real_segment_progress_and_encoding_progress_not_timer_estimates(self):
        identifier = self.create_job()
        class Voice:
            def __init__(self, *args): pass
            async def stream(self):
                yield {"type": "audio", "data": b"segment"}
        events = []
        asyncio.run(synthesize("文" * 6000, "voice", server.folder(identifier), server.Control(), lambda _: None,
                               factory=Voice, on_progress=lambda *args: events.append(args)))
        self.assertEqual(events, [(0, 3, "语音分段"), (1, 3, "语音分段"), (2, 3, "语音分段"), (3, 3, "语音分段"), (None, 3, "合并与验证")])
        path = server.folder(identifier) / "encode-progress.txt"
        path.write_text("out_time_us=1000000\nprogress=continue\nout_time_us=5000000\nprogress=continue\n")
        self.assertEqual(server.encoding_progress(path, 10)["percent"], 50)
        path.write_text("out_time_us=12000000\nprogress=end\n")
        self.assertEqual(server.encoding_progress(path, 10)["percent"], 99)
        self.assertIsNone(server.encoding_progress(path, 0))
        path.write_text("waiting")
        self.assertIsNone(server.encoding_progress(path, 10))

    def test_status_colors_and_progress_ui_behaviour(self):
        script = re.search(r"<script>(.*?)</script>", self.client.get("/").get_data(as_text=True), re.S).group(1)
        harness = '''
const els=new Map();function el(id){if(!els.has(id))els.set(id,{value:'',style:{},dataset:{},setAttribute(){},getAttribute(){return null},replaceChildren(){},add(){},removeAttribute(){this.indeterminate=true}});return els.get(id)}
global.document={getElementById:el,querySelectorAll:()=>[],head:{append(){}}};global.Audio=class{};global.localStorage={getItem(){return null},setItem(){}};global.Option=class{};global.fetch=async()=>({ok:true,json:async()=>({jobs:[]})});
'''
        assertions = '''
renderState({audio_state:'working',audio_progress:{stage:'语音分段',completed:2,total:4,percent:50}},'audio');
if(!el('audioState').className.includes('state-running')||el('audioProgress').value!==50||!el('audioProgressHint').textContent.includes('50%'))throw Error('running not red or no real percent');
renderState({audio_state:'ready'},'audio');if(!el('audioState').className.includes('state-complete')||el('audioProgress').value!==100)throw Error('completion not green');
renderState({video_state:'working',paused:true,video_progress:{stage:'视频编码',percent:43}},'video');if(!el('videoState').className.includes('state-paused')||el('videoProgress').value!==43)throw Error('paused progress lost');
renderState({video_state:'working',video_progress:{stage:'验证',percent:null}},'video');if(!el('videoProgress').indeterminate||el('videoProgressHint').textContent.includes('%'))throw Error('unknown progress invents percentage');
renderState({video_state:'failed'},'video');if(!el('videoState').className.includes('state-failed'))throw Error('failure not distinguished');
'''
        run = subprocess.run(["/opt/homebrew/bin/node", "-"], input=harness + script + assertions, text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_article_endpoints_removed_and_mode_rejected(self):
        identifier = self.create_job()
        for url in ["/api/web-imports", f"/api/web-imports/{identifier}/cancel"]:
            self.assertEqual(self.post(url).status_code, 404)
        self.assertEqual(self.client.get(f"/api/web-imports/{identifier}").status_code, 404)
        self.assertEqual(self.post("/api/jobs", {"text": "文案", "image_mode": "webpage"}).status_code, 400)
        self.assertEqual(self.post(f"/api/jobs/{identifier}/use-image", {"image_mode": "webpage"}).status_code, 400)
        page = self.client.get("/").get_data(as_text=True)
        self.assertNotIn('id="articleUrl"', page)
        self.assertNotIn('value="webpage"', page)

    def test_create_gallery_and_selection_preserve_audio_old_files(self):
        items = self.gallery_source()
        identifier = self.create_job()
        server.store_gallery(identifier, items)
        job = server.read_job(identifier)
        self.assertEqual(job["image_state"], "ready")
        self.assertEqual([x["source_id"] for x in job["image_gallery"]], [item["id"] for _, item in items])
        original_paths = server.image_paths(job)
        version = job["image_version"]
        audio = server.folder(job["id"]) / "audio.mp3"
        audio.write_bytes(b"unchanged-audio")
        digest = hashlib.sha256(audio.read_bytes()).hexdigest()
        server.update(job["id"], audio_state="ready", video_state="done", confirmed_version=version)
        response = self.post(f"/api/jobs/{job['id']}/select-images", {"image_version": version, "selected_indices": [0, 2]})
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(len(result["gallery"]), 2)
        self.assertEqual(result["video_state"], "idle")
        self.assertEqual(result["confirmed_version"], "")
        self.assertTrue(all(p.exists() for p in original_paths))
        self.assertEqual(hashlib.sha256(audio.read_bytes()).hexdigest(), digest)
        self.assertEqual(self.post(f"/api/jobs/{job['id']}/confirm", {"image_version": version}).status_code, 409)
        self.assertEqual(self.post(f"/api/jobs/{job['id']}/select-images", {"image_version": result["image_version"], "selected_indices": []}).status_code, 400)
        with self.client.get(f"/api/jobs/{job['id']}/media/image") as r:
            self.assertEqual(r.status_code, 200)

    def test_confirm_locks_all_images_and_real_equal_time_video(self):
        items = self.gallery_source()
        identifier = self.create_job(title="", text="三张照片平均播放的真实合成验证")
        self.ready(identifier)
        server.store_gallery(identifier, items)
        job = server.read_job(identifier)
        with patch.object(server, "launch"):
            self.assertEqual(self.post(f"/api/jobs/{identifier}/confirm", {"image_version": job["image_version"]}).status_code, 200)
        confirmed = server.read_job(identifier)
        second = server.image_paths(confirmed)[1]
        old = second.read_bytes()
        second.write_bytes(png("yellow"))
        with self.assertRaisesRegex(RuntimeError, "发生变化"):
            server.video_worker(identifier, server.Control())
        second.write_bytes(old)
        server.video_worker(identifier, server.Control())
        result = server.read_job(identifier)
        self.assertEqual(result["video_state"], "done")
        self.assertEqual(len(result["image_durations"]), 3)
        self.assertAlmostEqual(sum(result["image_durations"]), result["audio_duration"])
        video = server.video_path(result)
        run = subprocess.run(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"], capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        for i, color in enumerate(((255, 0, 0), (0, 128, 0), (0, 0, 255))):
            at = result["audio_duration"] / 3 * (i + .5)
            frame = subprocess.run(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-ss", str(at), "-i", str(video), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True)
            with Image.open(io.BytesIO(frame.stdout)) as picture:
                pixel = picture.convert("RGB").getpixel((960, 540))
            self.assertTrue(all(abs(a - b) < 12 for a, b in zip(pixel, color)), (i, pixel))

    def test_model_records_are_persistent_safe_paginated_and_exportable(self):
        records.append("model_call", status="started", provider="seedream", model="test-model", api_key="PRIVATE", prompt="SECRET", url="https://secret")
        records.append("model_call", status="failed", provider="seedream", model="test-model", duration_ms=17)
        records.append("service", status="started")
        data = self.client.get("/api/records?kind=models").get_json()
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["releases"][0]["version"], records.VERSION)
        self.assertNotIn("PRIVATE", records.LOG_FILE.read_text())
        self.assertNotIn("SECRET", records.LOG_FILE.read_text())
        self.assertNotIn("https://secret", records.LOG_FILE.read_text())
        self.assertEqual(len(records.entries(limit=1)["entries"]), 1)
        self.assertEqual(records.entries(limit=1)["next_offset"], 1)
        with self.client.get("/api/records/export") as export:
            self.assertEqual(export.status_code, 200)
            self.assertIn("attachment", export.headers["Content-Disposition"])
            self.assertEqual(len(export.get_json()["entries"]), 3)
        self.assertEqual(self.client.get("/api/records?offset=x").status_code, 400)

    def test_actual_worker_writes_model_lifecycle_not_sensitive_content(self):
        identifier = self.create_job()
        def fake_audio(job_id, control):
            server.update(job_id, audio_state="ready")
        with patch.dict(server.WORKERS, {"audio": fake_audio}):
            server.launch(identifier, "audio")
            deadline = time.monotonic() + 3
            while server.ACTIVE and time.monotonic() < deadline:
                time.sleep(.02)
        rows = records.entries("models")["entries"]
        self.assertEqual([r["status"] for r in rows], ["completed", "started"])
        self.assertEqual(rows[0]["provider"], "edge-tts")
        self.assertEqual(rows[0]["model"], "zh-CN-YunxiNeural")
        self.assertEqual(rows[0]["call_id"], rows[1]["call_id"])
        self.assertNotIn("TEST-SECRET", records.LOG_FILE.read_text())


# 避免继承的 43 项旧回归重复执行，复用其测试夹具/帮助方法。
for name in dir(fixtures.WorkbenchTests):
    if name.startswith("test_") and name not in WebRecordTests.__dict__:
        setattr(WebRecordTests, name, None)
