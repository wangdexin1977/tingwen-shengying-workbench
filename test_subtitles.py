"""字幕同步、参数校验和真实 MP4 的像素验证，不请求收费服务。"""
import asyncio
import base64
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import app
import speech
import subtitles


class SubtitleTests(unittest.TestCase):
    def test_api_validation_generation_restyle_download_and_duplicate_confirm(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            jobs.mkdir()
            output = io.BytesIO()
            Image.new("RGB", (640, 360), "#23334c").save(output, "PNG")
            client = app.app.test_client()
            headers = {"Host": "127.0.0.1", "X-Local-Token": app.TOKEN}
            payload = {"text": "第一句口播字幕。第二句字幕切换。", "image_mode": "upload",
                       "photo_name": "测试照片.png", "photo_data": base64.b64encode(output.getvalue()).decode()}
            with patch.object(app, "JOBS", jobs), patch.object(app.records, "LOG_FILE", Path(directory) / "events.jsonl"):
                bad = client.post("/api/jobs", json={**payload, "subtitle_settings": {"font": "/tmp/a.ttf"}}, headers=headers)
                self.assertEqual(bad.status_code, 400)
                self.assertEqual(list(jobs.iterdir()), [])
                with patch.object(app, "launch"):
                    response = client.post("/api/jobs", json=payload, headers=headers)
                self.assertEqual(response.status_code, 201)
                job = response.json
                self.assertTrue(job["subtitle_settings"]["enabled"])
                audio = app.audio_path(job)
                subprocess.run(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                                "sine=frequency=440:duration=2", str(audio)], check=True)
                app.update(job["id"], audio_state="ready", audio_duration=float(app.probe(audio)["format"]["duration"]))
                url = f"/api/jobs/{job['id']}/confirm"
                data = {"image_version": job["image_version"]}

                def confirm(settings):
                    result = client.post(url, json={**data, "subtitle_settings": settings}, headers=headers)
                    self.assertEqual(result.status_code, 200)
                    deadline = time.monotonic() + 30
                    while app.ACTIVE and time.monotonic() < deadline:
                        time.sleep(.05)
                    current = app.read_job(job["id"])
                    self.assertEqual(current["video_state"], "done", current["video_message"])
                    return current

                style = subtitles.normalize_style({"color": "#00FF00", "position": 10})
                first = confirm(style)
                self.assertEqual(first["subtitle_timing"], "estimated")
                self.assertEqual(first["rendered_subtitle_settings"], style)
                digest = hashlib.sha256(app.video_path(first).read_bytes()).hexdigest()
                confirm(style)
                self.assertEqual(hashlib.sha256(app.video_path(first).read_bytes()).hexdigest(), digest)
                second = confirm({**style, "enabled": False})
                self.assertEqual(second["subtitle_timing"], "off")
                self.assertNotEqual(hashlib.sha256(app.video_path(second).read_bytes()).hexdigest(), digest)
                with client.get(f"/api/jobs/{job['id']}/media/video?download=1", headers=headers) as download:
                    self.assertEqual(download.status_code, 200)
                    self.assertIn("attachment", download.headers["Content-Disposition"])
                self.assertTrue(audio.is_file())
                self.assertTrue(app.image_paths(second)[0].is_file())
                app.CONTROLS.pop(job["id"], None)

    def test_validation_rejects_paths_invalid_colors_and_out_of_range(self):
        for style in [{"font": "/tmp/font.ttf"}, {"color": "red"}, {"size": 999},
                      {"position": -1}, {"enabled": "false"}, {"size": True}, []]:
            with self.subTest(style=style), self.assertRaises(ValueError):
                subtitles.normalize_style(style)

    def test_source_punctuation_and_english_words_survive_timing_mapping(self):
        text = "今天，测试 PC 字幕。然后调整颜色！"
        words = ["今天", "测试", "PC", "字幕", "然后", "调整", "颜色"]
        events = [{"text": word, "start": i * .5, "end": i * .5 + .4} for i, word in enumerate(words)]
        captions = subtitles.timed_captions(text, events, 4)
        self.assertIsNotNone(captions)
        self.assertEqual("".join(c["text"] for c in captions), text)
        self.assertEqual(captions[1]["start"], 2)
        self.assertIsNone(subtitles.timed_captions(text, events[:2], 4))

    def test_long_unpunctuated_script_and_legacy_audio_are_fully_covered(self):
        text = "这是一段没有标点的长文字幕" * 20
        self.assertEqual(subtitles.split_captions("A" * 50), ["A" * 24, "A" * 24, "AA"])
        captions = subtitles.estimated_captions(text, 30)
        self.assertEqual("".join(c["text"] for c in captions), text)
        self.assertLessEqual(max(len(c["text"]) for c in captions), 24)
        self.assertAlmostEqual(captions[-1]["end"], 30)
        with tempfile.TemporaryDirectory() as directory:
            result = subtitles.build_timeline(Path(directory), text, "voice", lambda _: {"format": {"duration": "30"}})
            self.assertEqual(result["mode"], "estimated")

    def test_voice_time_information_saved_and_reused_without_network_call(self):
        class Response:
            def __init__(self, *args):
                pass

            async def stream(self):
                yield {"type": "audio", "data": b"test-audio"}
                yield {"type": "WordBoundary", "text": "你好", "offset": 1_000_000, "duration": 5_000_000}

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            asyncio.run(speech.synthesize("你好。", "voice", folder, app.Control(), lambda _: None, factory=Response))
            timing = json.loads((folder / "part-0000.timing.json").read_text())
            self.assertEqual(timing["boundaries"][0]["start"], .1)
            with patch.object(speech.edge_tts, "Communicate", side_effect=AssertionError("should resume")):
                asyncio.run(speech.synthesize("你好。", "voice", folder, app.Control(), lambda _: None))
            result = subtitles.build_timeline(folder, "你好。", "voice", lambda _: {"format": {"duration": "1"}})
            self.assertEqual(result["mode"], "speech")
            self.assertEqual(result["captions"][0]["text"], "你好。")

    def test_font_and_color_are_used_by_same_renderer_as_preview(self):
        pixels = []
        for font in subtitles.FONTS:
            image = subtitles.render_strip("中文字幕 ABC", subtitles.normalize_style({"font": font, "color": "#00FF00"}))
            self.assertTrue(any(g > 200 and r < 50 and b < 50 and a > 200 for r, g, b, a in image.getdata()))
            pixels.append(image.tobytes())
        self.assertEqual(len(set(pixels)), len(subtitles.FONTS))

    def test_real_multiphoto_mp4_subtitle_color_location_and_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            images = []
            for i, color in enumerate(["#243448", "#482434", "#344824"]):
                path = folder / f"background-{i}.png"
                Image.new("RGB", (1920, 1080), color).save(path)
                images.append(path)
            subprocess.run(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=3", str(folder / "audio.mp3")], check=True)
            style = subtitles.normalize_style({"color": "#00FF00", "font": "song", "size": 60, "position": 10})
            timeline = {"mode": "speech", "style": style, "captions": [
                {"text": "绿色宋体字幕", "start": .2, "end": 1.4},
                {"text": "第二句字幕", "start": 1.4, "end": 2.8}]}
            (folder / "timeline.json").write_text(json.dumps(timeline))
            command = [sys.executable, str(app.ROOT / "scripts/compose_video.py")]
            for path in images:
                command += ["--bg", str(path)]
            command += ["--audio", str(folder / "audio.mp3"), "--out", str(folder / "out.mp4"),
                        "--subtitle-timeline", str(folder / "timeline.json")]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            info = app.probe(folder / "out.mp4")
            video = next(s for s in info["streams"] if s["codec_type"] == "video")
            self.assertEqual((video["width"], video["height"]), (1920, 1080))
            self.assertLess(abs(float(info["format"]["duration"]) - 3), .2)
            frames = []
            for second in [.05, .5, 1.7, 2.85]:
                frame = folder / f"frame-{second}.png"
                subprocess.run(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-y", "-ss", str(second),
                                "-i", str(folder / "out.mp4"), "-frames:v", "1", str(frame)], check=True)
                with Image.open(frame) as image:
                    hits = [(i % image.width, i // image.width) for i, (r, g, b) in enumerate(image.convert("RGB").getdata())
                            if g > 180 and r < 90 and b < 90]
                frames.append(hits)
            self.assertFalse(frames[0])
            self.assertGreater(len(frames[1]), 100)
            self.assertGreater(len(frames[2]), 100)
            self.assertLess(max(y for _, y in frames[1]), 200)
            self.assertFalse(frames[3])
            self.assertNotEqual(frames[1], frames[2])


if __name__ == "__main__":
    unittest.main()
