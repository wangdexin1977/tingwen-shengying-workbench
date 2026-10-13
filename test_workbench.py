"""离线回归：没有外部生成请求；真实 FFmpeg 合成使用独立测试素材。"""
import ast
import asyncio
import base64
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import uuid
from pathlib import Path
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

import app as server
from seedream import MODELS, SeedreamClient, SeedreamError, SubmissionUnknown
from speech import split_text, synthesize
from drawthings import DrawThingsClient, DrawThingsError
from image_sources import client_for
import photo_import
import records
from scripts.make_homepage_bg import draw_center_title, TITLE_COLORS

DRAW_OPTIONS = {"model": "sd_v1.5_f16.ckpt", "sampler": "Euler a", "steps": 12,
                "guidance_scale": 7.0, "batch_count": 1}
DRAW_SNAPSHOT = {"model": DRAW_OPTIONS["model"], "port": 7861,
                 "settings": {k: DRAW_OPTIONS[k] for k in ("steps", "sampler", "guidance_scale")}}


def image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (640, 384), (48, 112, 100)).save(output, format="PNG")
    return output.getvalue()


class PhotoTests(unittest.TestCase):
    def test_local_formats_orientation_and_metadata_privacy(self):
        for format in ["PNG", "JPEG", "WEBP", "HEIF"]:
            output = io.BytesIO()
            picture = Image.new("RGB", (30, 20), "red")
            exif = picture.getexif()
            exif[274] = 6
            exif[270] = "PRIVATE-METADATA"
            picture.save(output, format=format, exif=exif)
            raw, meta = photo_import.local_photo(base64.b64encode(output.getvalue()).decode(), "../照片." + format)
            # HEIF 编码器会归一化 EXIF 方向；其他格式保留方向标签供导入时旋转。
            self.assertEqual(meta["dimensions"], [30, 20] if format == "HEIF" else [20, 30])
            self.assertNotIn("PRIVATE-METADATA", str(raw))
            with Image.open(io.BytesIO(raw)) as converted:
                self.assertEqual(converted.format, "PNG")
                self.assertFalse(converted.getexif())
            self.assertEqual(meta["source_name"], "照片." + format)
        for raw in ["invalid", base64.b64encode(b"not-a-photo").decode()]:
            with self.assertRaises(photo_import.PhotoError):
                photo_import.local_photo(raw)
        with patch.object(photo_import, "MAX_BYTES", 20):
            with self.assertRaises(photo_import.PhotoError):
                photo_import.normalize(image_bytes(), "upload")

    def test_public_url_validation_and_pinned_transport(self):
        for address in ["127.0.0.1", "192.168.1.3", "169.254.169.254", "::1", "::ffff:127.0.0.1", "224.0.0.1", "64:ff9b::7f00:1"]:
            with patch.object(photo_import.socket, "getaddrinfo", return_value=[(0, 0, 0, "", (address, 443))]):
                with self.assertRaises(photo_import.PhotoError):
                    photo_import.public_target("https://example.com/photo.jpg")
        for url in ["file:///tmp/a.png", "https://user:pass@example.com/a.png", "http://example.com:8091/a.png", "https://example.com/a b"]:
            with self.assertRaises(photo_import.PhotoError):
                photo_import.public_target(url)
        with patch.object(photo_import.socket, "create_connection") as connect:
            transport = photo_import.PinnedHTTP("example.com", 80, "93.184.216.34")
            transport.connect()
            connect.assert_called_once_with(("93.184.216.34", 80), 20)

    def test_remote_download_redirects_html_and_private_addresses(self):
        class Response:
            status = 200
            headers = {"Content-Type": "image/png"}
            def getheader(self, key):
                return self.headers.get(key)
            def read(self, maximum):
                return image_bytes()[:maximum]
        class Connection:
            response = Response()
            calls = []
            def __init__(self, *args):
                self.target = args
            def request(self, *args, **kwargs):
                self.calls.append((self.target, args, kwargs))
            def getresponse(self):
                return self.response
            def close(self):
                pass
        def dns(host, *args, **kwargs):
            return [(0, 0, 0, "", ("127.0.0.1" if host == "localhost" else "93.184.216.34", 443))]
        with patch.object(photo_import.socket, "getaddrinfo", side_effect=dns), patch.object(photo_import, "PinnedHTTPS", Connection):
            _, meta = photo_import.download_photo("https://example.com/照片.png?token=secret")
            self.assertEqual(meta["provider"], "url")
            self.assertNotIn("secret", str(meta))
            self.assertIn("%E7%85%A7", Connection.calls[-1][1][1])
            self.assertNotIn("Authorization", Connection.calls[-1][2]["headers"])
            Connection.response.headers = {"Content-Type": "text/html"}
            with self.assertRaisesRegex(photo_import.PhotoError, "网页"):
                photo_import.download_photo("https://example.com/page")
            Connection.response.headers = {"Content-Length": str(photo_import.MAX_BYTES + 1)}
            with self.assertRaisesRegex(photo_import.PhotoError, "20 MB"):
                photo_import.download_photo("https://example.com/large.png")
            Connection.response.status = 302
            Connection.response.headers = {"Location": "https://localhost/private.png"}
            with self.assertRaisesRegex(photo_import.PhotoError, "本机"):
                photo_import.download_photo("https://example.com/redirect")


class TitleLayoutTests(unittest.TestCase):
    def test_large_bold_tricolor_stays_centered_without_overlap(self):
        for title in ["山", "山水", "今日风景", "发现中国山水之间的故事", "中" * 30, "Wgj中Ａ" * 6]:
            canvas = Image.new("RGB", (1920, 1080), "black")
            result = draw_center_title(canvas, title)
            self.assertGreaterEqual(result["font_size"], 90)
            self.assertIn("W6", result["font"])
            self.assertGreaterEqual(result["bold_radius"], 1)
            self.assertEqual(result["safe_region"], [96, 270, 1824, 810])
            self.assertEqual("".join(row["text"] for row in result["lines"]), title)
            previous_bottom = None
            for row in result["lines"]:
                left, top, right, bottom = row["bounds"]
                self.assertGreaterEqual(left, 96)
                self.assertLessEqual(right, 1824)
                self.assertGreaterEqual(top, 270)
                self.assertLessEqual(bottom, 810)
                self.assertAlmostEqual((left + right) / 2, 960)
                if previous_bottom is not None:
                    self.assertGreater(top, previous_bottom)
                previous_bottom = bottom
                for previous, following in zip(row["characters"], row["characters"][1:]):
                    self.assertLess(previous[2], following[0])
            actual = canvas.getbbox()
            self.assertTrue(96 <= actual[0] < actual[2] <= 1824)
            self.assertTrue(270 <= actual[1] < actual[3] <= 810)
            if len(title) >= 10:
                self.assertGreater(result["font_size"], 110)
            if len(title) >= 3:
                palette = dict((color, count) for count, color in canvas.getcolors(1920 * 1080))
                for color in TITLE_COLORS:
                    rgb = tuple(bytes.fromhex(color[1:]))
                    self.assertGreater(palette.get(rgb, 0), 0)

    def test_title_uses_wider_horizontal_space_not_old_sixty_percent_box(self):
        canvas = Image.new("RGB", (1920, 1080), "black")
        result = draw_center_title(canvas, "中" * 30)
        self.assertGreater(max(row["bounds"][2] - row["bounds"][0] for row in result["lines"]), 1920 * .6)
        self.assertAlmostEqual((result["lines"][0]["bounds"][1] + result["lines"][-1]["bounds"][3]) / 2, 540)

    def test_empty_title_unchanged_and_over_limit_rejected(self):
        canvas = Image.new("RGB", (1920, 1080), "blue")
        before = canvas.tobytes()
        self.assertIsNone(draw_center_title(canvas, ""))
        self.assertEqual(canvas.tobytes(), before)
        with self.assertRaises(ValueError):
            draw_center_title(canvas, "长" * 31)


class DrawThingsTests(unittest.TestCase):
    def test_protocol_over_real_loopback_http(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                calls.append((self.path, None, dict(self.headers)))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(DRAW_OPTIONS).encode())
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append((self.path, body, dict(self.headers)))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"images": [base64.b64encode(image_bytes()).decode()]}).encode())
        http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            client = DrawThingsClient(http.server_port, DRAW_SNAPSHOT["settings"])
            raw, meta = client.generate("公开的测试山水", DRAW_OPTIONS["model"])
            self.assertEqual(meta["dimensions"], [640, 384])
            self.assertTrue(meta["local"])
            self.assertGreater(len(raw), 0)
            self.assertEqual([c[0] for c in calls], ["/sdapi/v1/options", "/sdapi/v1/txt2img"])
            body = calls[1][1]
            self.assertEqual((body["width"], body["height"]), (768, 512))
            self.assertEqual(body["batch_count"], 1)
            self.assertEqual(body["steps"], 12)
            self.assertFalse(any("Authorization" in c[2] for c in calls))
            self.assertNotIn("api_key", body)
        finally:
            http.shutdown()
            http.server_close()
            thread.join(2)

    def test_unknown_service_never_receives_prompt(self):
        calls = []
        def transport(path, body=None):
            calls.append(path)
            return {"other_service": True}
        with self.assertRaises(DrawThingsError):
            DrawThingsClient(transport=transport).generate("private text", DRAW_OPTIONS["model"])
        self.assertEqual(calls, ["/sdapi/v1/options"])

    def test_invalid_ports_models_and_images(self):
        for port in [True, 0, 1023, 65536, "https://example.com", "7861/path"]:
            with self.assertRaises(DrawThingsError):
                DrawThingsClient(port)
        with self.assertRaises(DrawThingsError):
            DrawThingsClient(transport=lambda *_: {**DRAW_OPTIONS, "model": None}).inspect()
        for result in [{}, {"images": ["invalid"]}, {"images": ["a", "b"]}]:
            def transport(path, body=None):
                return DRAW_OPTIONS if body is None else result
            with self.assertRaises(DrawThingsError):
                DrawThingsClient(transport=transport).generate("test", DRAW_OPTIONS["model"])

    def test_no_cloud_fallback_or_post_retry(self):
        calls = []
        def transport(path, body=None):
            calls.append(path)
            if body is None:
                return DRAW_OPTIONS
            raise DrawThingsError("timeout")
        with patch.object(SeedreamClient, "generate") as paid:
            with self.assertRaises(DrawThingsError):
                DrawThingsClient(transport=transport).generate("test", DRAW_OPTIONS["model"])
            paid.assert_not_called()
        self.assertEqual(calls.count("/sdapi/v1/txt2img"), 1)
        with self.assertRaises(ValueError):
            client_for({"provider": "unknown"})


class SeedreamTests(unittest.TestCase):
    def test_single_post_and_local_image(self):
        raw = image_bytes()
        calls = []
        def transport(body):
            calls.append(body)
            self.assertEqual(body["model"], MODELS[0])
            self.assertEqual(body["size"], "2K")
            self.assertTrue(body["watermark"])
            self.assertEqual(body["response_format"], "b64_json")
            self.assertNotIn("stream", body)
            return {"data": [{"b64_json": base64.b64encode(raw).decode()}]}
        returned, metadata = SeedreamClient("TEST-KEY", transport).generate("中文山水", MODELS[0])
        self.assertEqual(metadata["dimensions"], [640, 384])
        self.assertGreater(len(returned), 0)
        self.assertEqual(len(calls), 1)

    def test_errors_and_invalid_images_rejected(self):
        for image in [{"error": {"code": "rejected"}}, {"b64_json": "not-an-image"}, {"url": "https://example.com"}]:
            with self.assertRaises(SubmissionUnknown):
                SeedreamClient("TEST-KEY", lambda _: {"data": [image]}).generate("landscape", MODELS[0])

    def test_no_automatic_paid_retry(self):
        calls = []
        def transport(body):
            calls.append(body)
            raise SubmissionUnknown("timeout")
        with self.assertRaises(SubmissionUnknown):
            SeedreamClient("TEST-KEY", transport).generate("landscape", MODELS[0])
        self.assertEqual(len(calls), 1)

    def test_http_failures_hide_credentials_and_do_not_retry(self):
        for code, expected in [(401, SeedreamError), (403, SeedreamError), (429, SeedreamError), (500, SubmissionUnknown)]:
            error = urllib.error.HTTPError("https://ark.cn-beijing.volces.com", code, "TEST-SECRET", {}, None)
            with patch("seedream.urllib.request.urlopen", side_effect=error) as request:
                with self.assertRaises(expected) as caught:
                    SeedreamClient("TEST-SECRET").generate("中文图片", MODELS[0])
                self.assertNotIn("TEST-SECRET", str(caught.exception))
                self.assertEqual(request.call_count, 1)

    def test_bearer_auth_and_timeout(self):
        with patch("seedream.urllib.request.urlopen", side_effect=TimeoutError("TEST-SECRET")) as request:
            with self.assertRaises(SubmissionUnknown):
                SeedreamClient("TEST-SECRET").generate("中文图片", MODELS[0])
            sent = request.call_args.args[0]
            self.assertEqual(sent.get_header("Authorization"), "Bearer TEST-SECRET")
            self.assertEqual(sent.full_url, "https://ark.cn-beijing.volces.com/api/v3/images/generations")
            self.assertEqual(request.call_count, 1)

    def test_429_explains_provider_code_and_hides_key(self):
        body = io.BytesIO(json.dumps({"error": {"code": "AccountOverdueError", "message": "Account overdue TEST-SECRET"}}).encode())
        error = urllib.error.HTTPError("https://ark.cn-beijing.volces.com", 429, "rate limit", {}, body)
        with patch("seedream.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(SeedreamError) as caught:
                SeedreamClient("TEST-SECRET").generate("landscape", MODELS[0])
        self.assertIn("AccountOverdueError", str(caught.exception))
        self.assertNotIn("TEST-SECRET", str(caught.exception))

    def test_safe_experience_limit_has_specific_action(self):
        body = io.BytesIO(json.dumps({"error": {"code": "SetLimitExceeded", "message": "Safe Experience Mode"}}).encode())
        error = urllib.error.HTTPError("https://ark.cn-beijing.volces.com", 429, "limit", {}, body)
        with patch("seedream.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(SeedreamError) as caught:
                SeedreamClient("TEST-KEY").generate("landscape", MODELS[0])
        self.assertIn("安心体验限额", str(caught.exception))
        self.assertIn("开通管理", str(caught.exception))


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="shengying-workbench-tests-")
        self.old_jobs = server.JOBS
        server.JOBS = Path(self.tmp.name) / "jobs"
        server.JOBS.mkdir()
        self.old_log = records.LOG_FILE
        records.LOG_FILE = Path(self.tmp.name) / "records" / "events.jsonl"
        self.client = server.app.test_client()
        self.headers = {"X-Local-Token": server.TOKEN}
        server.ACTIVE.clear()
        server.CONTROLS.clear()

    def tearDown(self):
        deadline = time.monotonic() + 5
        while server.ACTIVE and time.monotonic() < deadline:
            time.sleep(.05)
        server.JOBS = self.old_jobs
        records.LOG_FILE = self.old_log
        self.tmp.cleanup()

    def post(self, path, data=None):
        return self.client.post(path, json=data or {}, headers=self.headers)

    def create_job(self, **kwargs):
        data = {"text": "这是一段独立产品的测试文案。", "prompt": "mountain landscape",
                "voice": "zh-CN-YunxiNeural", "model": MODELS[0], "title": "独立测试",
                "api_key": "TEST-SECRET", "payment_consent": True, **kwargs}
        with patch.object(server, "launch"):
            response = self.post("/api/jobs", data)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["id"]

    def ready(self, identifier):
        directory = server.folder(identifier)
        shutil.copyfile(server.ROOT / "assets/previews/zh-CN-YunxiNeural.mp3", server.audio_path(server.read_job(identifier)))
        version = uuid.uuid4().hex
        (directory / f"image-{version}.png").write_bytes(image_bytes())
        duration = float(server.probe(server.audio_path(server.read_job(identifier)))["format"]["duration"])
        server.update(identifier, audio_state="ready", image_state="ready", image_version=version, audio_duration=duration)
        return version

    def test_security_and_consent(self):
        response = self.client.post("/api/jobs", json={})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/", headers={"Host": "evil.example"}).status_code, 403)
        response = self.post("/api/jobs", {"text": "hello", "prompt": "mountains"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("API Key", response.get_json()["error"])
        response = self.post("/api/jobs", {"text": "hello", "prompt": "mountains", "api_key": "TEST-KEY"})
        self.assertIn("收费", response.get_json()["error"])

    def test_manual_create_without_prompt_or_cloud_credentials(self):
        with patch.object(server, "launch") as launch, patch.object(server, "image_configuration") as ai:
            response = self.post("/api/jobs", {"text": "照片测试", "image_mode": "upload",
                                "photo_data": base64.b64encode(image_bytes()).decode(), "photo_name": "本机照片.png"})
            self.assertEqual(response.status_code, 201, response.get_json())
            job = response.get_json()
            ai.assert_not_called()
            launch.assert_called_once_with(job["id"], "audio")
            self.assertEqual(job["provider"], "manual")
            self.assertEqual(job["image_state"], "ready")
            self.assertEqual(job["image_metadata"]["provider"], "upload")

    def test_use_photo_preserves_audio_old_picture_and_requires_reconfirmation(self):
        identifier = self.create_job()
        old = self.ready(identifier)
        directory = server.folder(identifier)
        digest = hashlib.sha256((server.audio_path(server.read_job(identifier))).read_bytes()).hexdigest()
        server.update(identifier, video_state="done", confirmed_version=old, submitting=True)
        response = self.post(f"/api/jobs/{identifier}/use-image", {"image_mode": "upload",
                             "photo_data": base64.b64encode(image_bytes()).decode(), "photo_name": "自选.png"})
        self.assertEqual(response.status_code, 200, response.get_json())
        job = response.get_json()
        self.assertEqual(job["confirmed_version"], "")
        self.assertEqual(job["video_state"], "idle")
        self.assertTrue(job["submitting"])
        self.assertTrue((directory / f"image-{old}.png").exists())
        self.assertEqual(hashlib.sha256((server.audio_path(server.read_job(identifier))).read_bytes()).hexdigest(), digest)
        self.assertEqual(self.post(f"/api/jobs/{identifier}/confirm", {"image_version": old}).status_code, 409)
        response = self.post(f"/api/jobs/{identifier}/confirm", {"image_version": job["image_version"]})
        self.assertEqual(response.status_code, 200)
        deadline = time.monotonic() + 45
        while server.ACTIVE and time.monotonic() < deadline:
            time.sleep(.1)
        job = server.read_job(identifier)
        self.assertEqual(job["video_state"], "done", job["video_message"])
        result = subprocess.run(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-i", str(server.video_path(job)), "-f", "null", "-"], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_remote_photo_failure_preserves_existing_materials_and_blocks_overlap(self):
        identifier = self.create_job()
        old = self.ready(identifier)
        before = (server.folder(identifier) / "job.json").read_bytes()
        with patch.object(server, "download_photo", side_effect=photo_import.PhotoError("网页地址")):
            response = self.post(f"/api/jobs/{identifier}/use-image", {"image_mode": "url", "photo_url": "https://example.com/page"})
            self.assertEqual(response.status_code, 400)
        self.assertEqual((server.folder(identifier) / "job.json").read_bytes(), before)
        server.ACTIVE.add((identifier, "image-import"))
        try:
            self.assertEqual(self.post(f"/api/jobs/{identifier}/confirm", {"image_version": old}).status_code, 409)
            self.assertEqual(self.post(f"/api/jobs/{identifier}/use-image", {"image_mode": "upload"}).status_code, 409)
        finally:
            server.ACTIVE.clear()
        with patch.object(server, "download_photo", return_value=(image_bytes(), {"provider": "url", "source_name": "example.com", "dimensions": [640, 384]})), patch.object(server, "client_for") as ai:
            response = self.post(f"/api/jobs/{identifier}/use-image", {"image_mode": "url", "photo_url": "https://example.com/picture.png"})
            self.assertEqual(response.status_code, 200)
            ai.assert_not_called()
        self.assertEqual(response.get_json()["image_metadata"]["provider"], "url")

    def test_separate_file_locations_and_finder_reveal(self):
        identifier = self.create_job()
        with patch.object(server.subprocess, "Popen") as open_file:
            self.assertEqual(self.post(f"/api/jobs/{identifier}/reveal", {"kind": "audio"}).status_code, 409)
            open_file.assert_not_called()
        self.ready(identifier)
        directory = server.folder(identifier)
        server.video_path(server.read_job(identifier)).write_bytes(b"test-placeholder")
        server.update(identifier, video_state="done")
        result = self.client.get(f"/api/jobs/{identifier}").get_json()
        for kind, extension in [("audio", "mp3"), ("video", "mp4")]:
            path = str(server.audio_path(server.read_job(identifier)) if kind == "audio" else server.video_path(server.read_job(identifier)))
            self.assertEqual(result[f"{kind}_path"], path)
            with patch.object(server.subprocess, "Popen") as open_file:
                response = self.post(f"/api/jobs/{identifier}/reveal", {"kind": kind})
                self.assertEqual(response.status_code, 200)
                open_file.assert_called_once_with(["open", "-R", path])
        with patch.object(server.subprocess, "Popen") as open_file:
            self.assertEqual(self.post(f"/api/jobs/{identifier}/reveal", {"kind": "../../secret"}).status_code, 400)
            open_file.assert_not_called()
        html = self.client.get("/").get_data(as_text=True)
        for marker in ['id="revealAudio"', 'id="revealVideo"', 'id="audioPath"', 'id="videoPath"', 'id="replacePhoto"', 'id="usePhotoUrl"']:
            self.assertIn(marker, html)

    def test_key_not_saved(self):
        identifier = self.create_job(api_key="TEST-SECRET-DO-NOT-SAVE")
        text = (server.folder(identifier) / "job.json").read_text()
        self.assertNotIn("TEST-SECRET", text)
        self.assertEqual(server.read_job(identifier)["provider"], "seedream")

    def test_drawthings_needs_no_payment_and_does_not_receive_key(self):
        with patch.object(DrawThingsClient, "inspect", return_value=DRAW_SNAPSHOT), patch.object(server, "launch"):
            identifier = self.create_job(provider="drawthings", payment_consent=False)
        job = server.read_job(identifier)
        self.assertEqual(job["model"], DRAW_OPTIONS["model"])
        self.assertEqual(job["image_settings"], DRAW_SNAPSHOT["settings"])
        self.assertEqual(server.CONTROLS[identifier].key, "")
        self.assertNotIn("TEST-SECRET", (server.folder(identifier) / "job.json").read_text())
        with patch.object(DrawThingsClient, "generate", return_value=(image_bytes(), {"model": job["model"], "dimensions": [640, 384]})), patch.object(SeedreamClient, "generate") as paid:
            server.image_worker(identifier, server.CONTROLS[identifier])
            paid.assert_not_called()
        self.assertEqual(server.read_job(identifier)["image_metadata"]["provider"], "drawthings")
        self.assertEqual(server.read_job(identifier)["video_state"], "idle")

    def test_change_source_preserves_audio_and_requires_explicit_paid_consent(self):
        identifier = self.create_job()
        version = self.ready(identifier)
        server.update(identifier, confirmed_version=version, video_state="done", submitting=True)
        path = server.audio_path(server.read_job(identifier))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with patch.object(DrawThingsClient, "inspect", return_value=DRAW_SNAPSHOT), patch.object(server, "launch") as launch:
            response = self.post(f"/api/jobs/{identifier}/change-source", {"provider": "drawthings", "prompt": "local landscape"})
            self.assertEqual(response.status_code, 200)
            launch.assert_called_once_with(identifier, "image")
        job = server.read_job(identifier)
        self.assertEqual(job["provider"], "drawthings")
        self.assertEqual(job["audio_state"], "ready")
        self.assertEqual(job["confirmed_version"], "")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.assertEqual(server.CONTROLS[identifier].key, "")
        with patch.object(server, "launch") as launch:
            response = self.post(f"/api/jobs/{identifier}/change-source", {"provider": "seedream", "prompt": "paid image", "api_key": "TEST-KEY"})
            self.assertEqual(response.status_code, 400)
            launch.assert_not_called()
        self.assertEqual(server.read_job(identifier)["provider"], "drawthings")

    def test_drawthings_retry_uses_saved_model_and_settings(self):
        with patch.object(DrawThingsClient, "inspect", return_value=DRAW_SNAPSHOT):
            identifier = self.create_job(provider="drawthings")
        with patch.object(DrawThingsClient, "inspect", return_value={**DRAW_SNAPSHOT, "model": "different.ckpt"}), patch.object(server, "launch"):
            response = self.post(f"/api/jobs/{identifier}/regenerate", {"prompt": "new local image"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(server.read_job(identifier)["model"], DRAW_OPTIONS["model"])

    def test_drawthings_connection_error_prevents_partial_job_creation(self):
        with patch.object(DrawThingsClient, "inspect", side_effect=DrawThingsError("请启动 Draw Things")), patch.object(server, "launch") as launch:
            response = self.post("/api/jobs", {"text": "test", "prompt": "image", "provider": "drawthings"})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(list(server.JOBS.iterdir()), [])
            launch.assert_not_called()

    def test_regenerate_produces_new_version_then_real_mp4_after_reconfirmation(self):
        with patch.object(DrawThingsClient, "inspect", return_value=DRAW_SNAPSHOT):
            identifier = self.create_job(provider="drawthings", payment_consent=False)
        old_version = self.ready(identifier)
        directory = server.folder(identifier)
        audio_digest = hashlib.sha256((server.audio_path(server.read_job(identifier))).read_bytes()).hexdigest()
        server.update(identifier, confirmed_version=old_version, confirmed_digest="old-confirmation", video_state="done")
        with patch.object(DrawThingsClient, "inspect", return_value=DRAW_SNAPSHOT), patch.object(DrawThingsClient, "generate", return_value=(image_bytes(), {"model": DRAW_OPTIONS["model"], "dimensions": [640, 384]})) as generate:
            response = self.post(f"/api/jobs/{identifier}/regenerate", {"prompt": "a quiet lake at sunset"})
            self.assertEqual(response.status_code, 200)
            deadline = time.monotonic() + 5
            while server.ACTIVE and time.monotonic() < deadline:
                time.sleep(.05)
            generate.assert_called_once_with("a quiet lake at sunset", DRAW_OPTIONS["model"])
        job = server.read_job(identifier)
        self.assertEqual(job["image_state"], "ready")
        self.assertNotEqual(job["image_version"], old_version)
        self.assertEqual(job["confirmed_version"], "")
        self.assertEqual(job["confirmed_digest"], "")
        self.assertEqual(job["video_state"], "idle")
        self.assertIn("重新确认", job["video_message"])
        self.assertTrue((directory / f"image-{old_version}.png").exists())
        self.assertEqual(hashlib.sha256((server.audio_path(server.read_job(identifier))).read_bytes()).hexdigest(), audio_digest)
        self.assertEqual(self.post(f"/api/jobs/{identifier}/confirm", {"image_version": old_version}).status_code, 409)
        self.assertFalse(server.video_path(job).exists())
        response = self.post(f"/api/jobs/{identifier}/confirm", {"image_version": job["image_version"]})
        self.assertEqual(response.status_code, 200)
        deadline = time.monotonic() + 45
        while server.ACTIVE and time.monotonic() < deadline:
            time.sleep(.1)
        job = server.read_job(identifier)
        self.assertEqual(job["video_state"], "done", job["video_message"])
        video = server.video_path(job)
        info = server.probe(video)
        self.assertTrue(any(s["codec_type"] == "video" and s["width"] == 1920 and s["height"] == 1080 for s in info["streams"]))
        self.assertLess(abs(float(info["format"]["duration"]) - job["audio_duration"]), 1.5)
        result = subprocess.run(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_regeneration_refuses_payment_or_duplicate_and_preserves_ready_materials(self):
        identifier = self.create_job()
        version = self.ready(identifier)
        before = (server.folder(identifier) / "job.json").read_bytes()
        with patch.object(server, "launch") as launch:
            response = self.post(f"/api/jobs/{identifier}/regenerate", {"prompt": "new image", "api_key": "TEST-KEY"})
            self.assertEqual(response.status_code, 400)
            launch.assert_not_called()
        self.assertEqual((server.folder(identifier) / "job.json").read_bytes(), before)
        server.ACTIVE.add((identifier, "image"))
        try:
            with patch.object(server, "launch") as launch:
                response = self.post(f"/api/jobs/{identifier}/regenerate", {"prompt": "another image", "api_key": "TEST-KEY", "payment_consent": True})
                self.assertEqual(response.status_code, 409)
                launch.assert_not_called()
        finally:
            server.ACTIVE.clear()
        self.assertEqual(server.read_job(identifier)["image_version"], version)

    def test_regenerate_button_is_not_hidden_in_details_and_blocks_double_click(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('<div id="regeneratePanel" hidden>', html)
        self.assertNotIn('<details id="regeneratePanel"', html)
        self.assertIn('id="regenerate" class="primary links">重新生成图片</button>', html)
        script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
        # 无浏览器的界面行为回归：模拟 DOM 与延迟请求，验证快速重复点击只提交一次。
        harness = r'''
const elements=new Map();
function el(id){if(!elements.has(id))elements.set(id,{id,value:'',style:{},scrollHeight:145,checked:false,hidden:false,disabled:false,textContent:'',dataset:{},setAttribute(){},getAttribute(){return null},removeAttribute(){},replaceChildren(){},add(){},play(){return Promise.resolve()},pause(){}});return elements.get(id)}
global.document={getElementById:el,querySelectorAll:()=>[],activeElement:null,head:{append(){}}};
global.Audio=class{play(){return Promise.resolve()}pause(){}};
global.localStorage={getItem(){return null},setItem(){}};
global.Option=class{};
global.window={confirm(){return true}};
global.fetch=async()=>({ok:true,json:async()=>({jobs:[]})});
'''
        assertions = r'''
(async()=>{
job={id:'test',provider:'drawthings',model:'local.ckpt',prompt:'mountains',text:'hello',audio_state:'ready',image_state:'ready',video_state:'idle'};
let release,calls=0;api=async()=>{calls++;await new Promise(r=>{release=r});return {...job,image_state:'queued'}};history=async()=>{};
const first=imageAct('regenerate','drawthings',{prompt:'mountains'});const second=imageAct('regenerate','drawthings',{prompt:'mountains'});
if(calls!==1||!el('regenerate').disabled)throw Error('duplicate request or no immediate lock');
release();await Promise.all([first,second]);
if(!el('regenerate').disabled)throw Error('queued image should remain locked');
job.image_state='ready';job.provider='seedream';el('consent').checked=false;
await imageAct('regenerate','seedream',{prompt:'mountains'});
if(calls!==1||el('provider').value!=='seedream'||el('seedreamSettings').hidden)throw Error('paid consent guard missing');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(["/opt/homebrew/bin/node", "-"], input=harness + script + assertions, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unconfirmed_or_stale_image_cannot_render(self):
        identifier = self.create_job()
        self.assertEqual(self.post(f"/api/jobs/{identifier}/confirm").status_code, 409)
        self.ready(identifier)
        self.assertEqual(self.post(f"/api/jobs/{identifier}/confirm", {"image_version": "old"}).status_code, 409)
        self.assertFalse((server.folder(identifier) / "video.mp4").exists())

    def test_real_video_and_duplicate_confirm(self):
        identifier = self.create_job()
        version = self.ready(identifier)
        response = self.post(f"/api/jobs/{identifier}/confirm", {"image_version": version})
        self.assertEqual(response.status_code, 200)
        deadline = time.monotonic() + 45
        while server.ACTIVE and time.monotonic() < deadline:
            time.sleep(.1)
        job = server.read_job(identifier)
        self.assertEqual(job["video_state"], "done", job["video_message"])
        path = server.video_path(job)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.post(f"/api/jobs/{identifier}/confirm", {"image_version": version})
        self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
        info = server.probe(path)
        self.assertLess(abs(float(info["format"]["duration"]) - job["audio_duration"]), 1.5)
        result = subprocess.run(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.client.get(f"/api/jobs/{identifier}/media/video?download=1") as response:
            self.assertEqual(response.status_code, 200)

    def test_regenerate_keeps_audio_invalidates_confirmation(self):
        identifier = self.create_job()
        version = self.ready(identifier)
        server.update(identifier, confirmed_version=version, video_state="done")
        digest = hashlib.sha256((server.audio_path(server.read_job(identifier))).read_bytes()).hexdigest()
        with patch.object(server, "launch"):
            response = self.post(f"/api/jobs/{identifier}/regenerate", {"prompt": "new mountain", "api_key": "TEST-KEY", "payment_consent": True})
        self.assertEqual(response.status_code, 200)
        job = server.read_job(identifier)
        self.assertEqual(job["confirmed_version"], "")
        self.assertEqual(job["audio_state"], "ready")
        self.assertEqual(digest, hashlib.sha256((server.audio_path(server.read_job(identifier))).read_bytes()).hexdigest())

    def test_restart_preserves_remote_id(self):
        identifier = self.create_job()
        remote = str(uuid.uuid4())
        server.update(identifier, remote_id=remote, image_state="working")
        server.recover()
        job = server.read_job(identifier)
        self.assertEqual(job["remote_id"], remote)
        self.assertEqual(job["image_state"], "interrupted")
        with patch.object(server, "launch"):
            self.assertEqual(self.post(f"/api/jobs/{identifier}/retry-image", {"api_key": "TEST-KEY", "payment_consent": True}).status_code, 200)
        self.assertEqual(server.read_job(identifier)["remote_id"], remote)

    def test_pause_resume_cancel(self):
        identifier = self.create_job()
        self.post(f"/api/jobs/{identifier}/pause")
        self.assertTrue(server.CONTROLS[identifier].paused.is_set())
        self.post(f"/api/jobs/{identifier}/resume")
        self.assertFalse(server.CONTROLS[identifier].paused.is_set())
        self.post(f"/api/jobs/{identifier}/cancel")
        self.assertTrue(server.CONTROLS[identifier].cancelled.is_set())
        self.assertEqual(server.read_job(identifier)["audio_state"], "cancelled")

    def test_unknown_submission_requires_acknowledgement(self):
        identifier = self.create_job()
        server.update(identifier, image_state="failed", submitting=True)
        response = self.post(f"/api/jobs/{identifier}/retry-image", {"api_key": "TEST-KEY", "payment_consent": True})
        self.assertEqual(response.status_code, 409)
        with patch.object(server, "launch"):
            response = self.post(f"/api/jobs/{identifier}/retry-image", {"api_key": "TEST-KEY", "payment_consent": True, "confirm_resubmit": True})
        self.assertEqual(response.status_code, 200)

    def test_title_limit_and_default_voice_validate_before_external_requests(self):
        with patch.object(server, "image_configuration") as external, patch.object(server, "launch"):
            response = self.post("/api/jobs", {"text": "文案", "prompt": "山水", "title": "长" * 31})
            self.assertEqual(response.status_code, 400)
            external.assert_not_called()
            self.assertEqual(list(server.JOBS.iterdir()), [])
        identifier = self.create_job(title="中" * 30)
        self.assertEqual(server.read_job(identifier)["title"], "中" * 30)
        with patch.object(server, "launch"):
            response = self.post("/api/jobs", {"text": "测试", "image_mode": "upload", "photo_data": base64.b64encode(image_bytes()).decode()})
        self.assertEqual(response.get_json()["voice"], "zh-CN-YunjianNeural")

    def test_actual_mp4_and_download_use_first_thirty_text_characters_even_with_title(self):
        text = "这是一个用于测试文案前面三十个字作为视频文件名称的完整口播文本，后面的文字不应出现在文件名中。"
        identifier = self.create_job(title="画面标题不作为文件名", text=text)
        self.assertEqual(server.read_job(identifier)["video_filename"], text[:30] + ".mp4")
        version = self.ready(identifier)
        self.assertEqual(self.post(f"/api/jobs/{identifier}/confirm", {"image_version": version}).status_code, 200)
        deadline = time.monotonic() + 45
        while server.ACTIVE and time.monotonic() < deadline:
            time.sleep(.1)
        job = server.read_job(identifier)
        self.assertEqual(job["video_state"], "done", job["video_message"])
        video = server.video_path(job)
        self.assertEqual(video.name, text[:30] + ".mp4")
        self.assertTrue(video.is_file())
        info = server.probe(video)
        self.assertTrue(any(s["codec_type"] == "audio" for s in info["streams"]))
        with self.client.get(f"/api/jobs/{identifier}/media/video?download=1") as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn("filename*=UTF-8''", response.headers["Content-Disposition"])
            from urllib.parse import unquote
            self.assertIn(video.name, unquote(response.headers["Content-Disposition"]))
        result = self.client.get(f"/api/jobs/{identifier}").get_json()
        self.assertEqual(result["video_path"], str(video))
        with self.client.get(result["video_url"] + "&download=1") as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn(video.name, unquote(response.headers["Content-Disposition"]))
        for text in ['../不安全:文件名?*"<>|', "\n\t\r", ""]:
            name = server.video_filename("", text)
            self.assertEqual(Path(name).name, name)
            self.assertNotIn("/", name)
        legacy = dict(job)
        legacy.pop("video_filename")
        self.assertEqual(server.video_path(legacy), server.folder(identifier) / "video.mp4")

    def test_text_filename_boundaries_and_unicode_ignore_screen_title(self):
        for text in ["短文案", "字" * 30, "字" * 31, "😀" * 29 + "中" + "不保留", "A" * 35]:
            expected = text[:30] + ".mp4"
            self.assertEqual(server.video_filename("不同的画面标题", text), expected)
            self.assertEqual(server.video_filename("", text), expected)
        self.assertEqual(server.video_filename("仅有标题", ""), "听文视频.mp4")
        self.assertEqual(server.video_filename("标题", '文/案:*?"<>|\n'), "文_案" + "_" * 7 + ".mp4")

    def test_legacy_mp4_download_preserves_saved_name_in_both_routes(self):
        identifier = self.create_job(text="新版命名不应修改旧成片" * 4)
        server.update(identifier, video_filename="历史原文件名.mp4", video_state="done")
        path = server.video_path(server.read_job(identifier))
        path.write_bytes(b"legacy-saved-video")
        data = self.client.get(f"/api/jobs/{identifier}").json
        from urllib.parse import unquote
        for url in [f"/api/jobs/{identifier}/media/video?download=1", data["video_url"] + "&download=1"]:
            with self.client.get(url) as response:
                self.assertEqual(response.status_code, 200)
                self.assertIn(path.name, unquote(response.headers["Content-Disposition"]))
        self.assertEqual(path.read_bytes(), b"legacy-saved-video")

    def test_voice_memory_restores_without_autoplay_and_handles_stale_values(self):
        html = self.client.get("/").get_data(as_text=True)
        script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
        harness = r'''
const vm=require('vm');
function load(saved){
 const store={...(saved?{'shengying-workbench-voice':saved}:{})},elements=new Map();
 function el(id){if(!elements.has(id))elements.set(id,{value:'',style:{},scrollHeight:145,childNodes:[{textContent:id}],checked:false,dataset:{},setAttribute(){},getAttribute(){return null},removeAttribute(){},replaceChildren(){},add(){},classList:{toggle(){}}});return elements.get(id)}
 const buttons=['zh-CN-YunjianNeural','zh-CN-YunxiNeural'].map(id=>({...el(id),dataset:{voice:id}}));
 let plays=0;
 const context={clearInterval(){},clearTimeout(){},setTimeout(){return 1},document:{getElementById:el,querySelectorAll:()=>buttons,createElement:()=>({}),head:{append(){}}},Audio:class{play(){plays++;return Promise.resolve()}pause(){}},localStorage:{getItem:k=>store[k]||null,setItem:(k,v)=>store[k]=v},Option:class{},fetch:async()=>({ok:true,json:async()=>({jobs:[]})})};
 vm.createContext(context);vm.runInContext(SCRIPT,context);
 return {context,store,buttons,voice:()=>vm.runInContext('voice',context),plays:()=>plays};
}
const first=load(null);if(first.voice()!=='zh-CN-YunjianNeural'||first.plays()!==0)throw Error('first voice/default autoplay');
first.buttons[1].onclick();if(first.store['shengying-workbench-voice']!=='zh-CN-YunxiNeural'||first.plays()!==1)throw Error('selection not saved');
const restored=load(first.store['shengying-workbench-voice']);if(restored.voice()!=='zh-CN-YunxiNeural'||restored.plays()!==0)throw Error('restore/autoplay');
if(load('unknown-voice').voice()!=='zh-CN-YunjianNeural')throw Error('stale voice not handled');
'''
        result = subprocess.run(["/opt/homebrew/bin/node", "-"], input="const SCRIPT=" + json.dumps(script) + ";\n" + harness,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_old_horde_task_never_calls_horde(self):
        identifier = self.create_job()
        server.update(identifier, provider="horde", image_state="failed")
        with patch.object(server, "launch") as launch:
            response = self.post(f"/api/jobs/{identifier}/retry-image", {"api_key": "TEST-KEY", "payment_consent": True})
            self.assertEqual(response.status_code, 400)
            launch.assert_not_called()

    def test_cancel_after_submission_preserves_paid_image(self):
        identifier = self.create_job()
        control = server.CONTROLS[identifier]
        def generated(*_):
            control.cancelled.set()
            return image_bytes(), {"dimensions": [640, 384], "model": MODELS[0]}
        with patch.object(SeedreamClient, "generate", generated):
            server.image_worker(identifier, control)
        job = server.read_job(identifier)
        self.assertEqual(job["image_state"], "ready")
        self.assertTrue((server.folder(identifier) / f"image-{job['image_version']}.png").exists())
        self.post(f"/api/jobs/{identifier}/resume")
        self.assertFalse(server.CONTROLS[identifier].cancelled.is_set())

    def test_previews_and_javascript_syntax(self):
        for voice, _, _ in server.VOICES:
            with self.client.get("/api/preview/" + voice) as response:
                self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/preview/not-a-voice").status_code, 404)
        html = self.client.get("/").get_data(as_text=True)
        script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
        path = Path(self.tmp.name) / "ui.js"
        path.write_text(script)
        result = subprocess.run(["/opt/homebrew/bin/node", "--check", str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in server.ROOT.glob("*.py"):
            ast.parse(path.read_text())

    def test_long_text_and_no_partial_success(self):
        text = "长文" * 5000
        self.assertEqual("".join(split_text(text)), text)
        self.assertTrue(all(len(s) <= 2000 for s in split_text(text)))
        calls = []
        class Communicator:
            def __init__(self, text, voice):
                calls.append(text)
                self.fail = len(calls) == 1
            async def stream(self):
                yield {"type": "audio", "data": b"broken" if self.fail else b"complete"}
                if self.fail:
                    raise RuntimeError("network interrupted")
        directory = Path(self.tmp.name)
        asyncio.run(synthesize("测试", "voice", directory, server.Control(), lambda _: None, factory=Communicator))
        self.assertEqual((directory / "audio.mp3").read_bytes(), b"complete")
        self.assertEqual(len(calls), 2)

    def test_actual_process_can_pause_and_cancel(self):
        identifier = self.create_job()
        control = server.Control()
        control.paused.set()
        caught = []
        def run():
            try:
                server.run_process([sys.executable, "-c", "import time; time.sleep(15)"], identifier, control)
            except Exception as exc:
                caught.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(.4)
        self.assertTrue(thread.is_alive())
        control.cancelled.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(caught[0], server.Cancelled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
