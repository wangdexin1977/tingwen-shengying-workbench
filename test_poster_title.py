"""参考图标题样式：隔离验证排版、接口和真实 MP4，不调用模型。"""
import io
import base64
import os
import re
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops
import app as server
import test_workbench as fixtures
from scripts.make_homepage_bg import draw_poster_title, normalize_title, title_character_count, POSTER_COLORS, POSTER_PANEL, draw_yellow_title, unified_title, YELLOW_TITLE_COLOR, normalize_title_settings

MAIN = "可信通信\n如何真正落地"
SECONDARY = "——中国方案的\n产业协同\n与应用路径"


class YellowTitleLayoutTests(unittest.TestCase):
    def test_background_is_preserved_and_yellow_glyphs_are_large(self):
        canvas = Image.new("RGB", (1920, 1080), "#245070")
        before = canvas.copy()
        layout = draw_yellow_title(canvas, MAIN)
        self.assertIsNone(layout["panel_bounds"])
        self.assertEqual(layout["horizontal_scale"], 1)
        self.assertGreaterEqual(layout["font_size"], 240)
        self.assertEqual(layout["colors"], [YELLOW_TITLE_COLOR])
        self.assertEqual(canvas.crop((0, 0, 1920, 230)).tobytes(), before.crop((0, 0, 1920, 230)).tobytes())
        self.assertEqual(canvas.crop((0, 880, 1920, 1080)).tobytes(), before.crop((0, 880, 1920, 1080)).tobytes())
        palette = dict((color, count) for count, color in canvas.getcolors(canvas.width * canvas.height))
        self.assertGreater(palette.get(tuple(bytes.fromhex(YELLOW_TITLE_COLOR[1:])), 0), 10000)
        self.assertNotIn(tuple(bytes.fromhex(POSTER_COLORS[0][1:])), palette)
        self.assertNotIn(tuple(bytes.fromhex(POSTER_PANEL[1:])), palette)

    def test_auto_wrap_and_manual_lines_preserve_text_and_use_uniform_size(self):
        for title in ["黄色", "中" * 30, "Wgj中Ａ" * 6, MAIN, "\n".join("中" * 30)]:
            layout = draw_yellow_title(Image.new("RGB", (1920, 1080)), title)
            self.assertEqual("".join(row["text"] for row in layout["lines"]), normalize_title(title).replace("\n", ""))
            self.assertEqual(len(set(row["font_size"] for row in layout["lines"])), 1)
            previous_bottom = 0
            for row in layout["lines"]:
                left, top, right, bottom = row["bounds"]
                self.assertTrue(96 <= left < right <= 1824)
                self.assertTrue(259.2 <= top < bottom <= 831.6)
                self.assertGreater(top, previous_bottom)
                previous_bottom = bottom
        self.assertEqual(unified_title(MAIN, SECONDARY), MAIN + "\n" + SECONDARY)

    def test_empty_title_adds_nothing_and_31_characters_are_rejected(self):
        canvas = Image.new("RGB", (1920, 1080), "white")
        before = canvas.tobytes()
        self.assertIsNone(draw_yellow_title(canvas, ""))
        self.assertEqual(before, canvas.tobytes())
        with self.assertRaises(ValueError):
            draw_yellow_title(canvas, "中" * 31)

    def test_user_font_size_and_color_change_actual_glyphs(self):
        images = []
        for font in ["sans-bold", "sans", "song"]:
            canvas = Image.new("RGBA", (1920, 1080))
            layout = draw_yellow_title(canvas, "标题样式", {"font": font, "size": 160, "color": "#22ee88"})
            self.assertEqual(layout["font_size"], 160)
            self.assertEqual(layout["colors"], ["#22EE88"])
            self.assertEqual(canvas.getpixel((960, 0)), (0, 0, 0, 0))
            self.assertTrue(any(color == (34, 238, 136, 255) for count, color in canvas.getcolors(1920 * 1080)))
            images.append(canvas.tobytes())
        self.assertEqual(len(set(images)), 3)
        large = draw_yellow_title(Image.new("RGB", (1920, 1080)), "标题样式", {"size": 300})
        self.assertEqual(large["font_size"], 300)

    def test_invalid_title_style_is_rejected(self):
        for settings in [[], {"font": "../../secret"}, {"color": "yellow"}, {"color": "#fff"},
                         {"size": True}, {"size": 47}, {"size": 321}, {"size": "160"}]:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                normalize_title_settings(settings)


class PosterLayoutTests(unittest.TestCase):
    def test_main_title_half_red_half_yellow_across_wraps(self):
        for title in ["可信通信如何真正落地", "可信通信\n如何真正落地", "一二三四五", "短"]:
            canvas = Image.new("RGB", (1920, 1080), "#245070")
            result = draw_poster_title(canvas, title, "副标题保持黄色")
            colors = [color for row in result["lines"] if row["kind"] == "main" for color in row.get("character_colors", [])]
            count = len(normalize_title(title).replace("\n", ""))
            self.assertEqual(colors, [POSTER_COLORS[0]] * ((count + 1) // 2) + [POSTER_COLORS[1]] * (count // 2))
            for row in result["lines"]:
                if row["kind"] == "secondary":
                    self.assertEqual(row["character_colors"], [POSTER_COLORS[1]] * len(row["text"]))
                for bounds, color in zip(row["characters"], row["character_colors"]):
                    cropped = canvas.crop(tuple(map(int, bounds)))
                    palette = dict((rgb, amount) for amount, rgb in cropped.getcolors(cropped.width * cropped.height))
                    self.assertGreater(palette.get(tuple(bytes.fromhex(color[1:])), 0), 0)

    def test_short_main_title_is_larger_and_bolder(self):
        result = draw_poster_title(Image.new("RGB", (1920, 1080)), "主标题")
        main = result["lines"][0]
        self.assertGreater(main["font_size"], 185)
        self.assertGreaterEqual(main["bold_radius"], main["font_size"] // 40)

    def test_reference_hierarchy_panel_colors_and_nonoverlapping_rows(self):
        canvas = Image.new("RGB", (1920, 1080), "#245070")
        result = draw_poster_title(canvas, MAIN, SECONDARY)
        self.assertEqual(result["panel_bounds"], [614, 0, 1306, 1080])
        self.assertEqual(result["outline_width"], 0)
        self.assertEqual(result["colors"], list(POSTER_COLORS))
        self.assertEqual([r["text"] for r in result["lines"]], (MAIN + "\n" + SECONDARY).splitlines())
        self.assertEqual([r["kind"] for r in result["lines"]], ["main"] * 2 + ["secondary"] * 3)
        sizes = [r["font_size"] for r in result["lines"]]
        self.assertGreater(sizes[0], sizes[1])
        self.assertGreater(sizes[1], sizes[2])
        self.assertEqual(len(set(sizes[2:])), 1)
        self.assertGreaterEqual(sizes[0], 160)
        self.assertGreaterEqual(sizes[2], 90)
        self.assertEqual(canvas.getpixel((960, 0)), tuple(bytes.fromhex(POSTER_PANEL[1:])))
        self.assertEqual(canvas.getpixel((20, 540)), (36, 80, 112))
        palette = dict((color, count) for count, color in canvas.getcolors(1920 * 1080))
        self.assertNotIn((255, 255, 255), palette)
        self.assertNotIn((0, 0, 0), palette)
        for color in POSTER_COLORS:
            self.assertGreater(palette.get(tuple(bytes.fromhex(color[1:])), 0), 0)
        last_bottom = 0
        for row in result["lines"]:
            self.assertIn("W6", row["font"])
            left, top, right, bottom = row["bounds"]
            self.assertGreater(top, last_bottom)
            last_bottom = bottom
            self.assertTrue(643.2 <= left < right <= 1276.81)
            self.assertTrue(140.4 <= top < bottom <= 853.2)
            self.assertAlmostEqual((left + right) / 2, 960)
            for a, b in zip(row["characters"], row["characters"][1:]):
                self.assertLess(a[2], b[0])

    def test_auto_wrap_optional_groups_and_limits_preserve_all_text(self):
        for main, secondary in [("中" * 30, ""), ("主" * 15, "副" * 15), ("", SECONDARY),
                                ("中英2026：新技术", "一起了解！"), ("短", ""), ("\n".join("中" * 30), "")]:
            result = draw_poster_title(Image.new("RGB", (1920, 1080)), main, secondary)
            for kind, text in [("main", main), ("secondary", secondary)]:
                self.assertEqual("".join(r["text"] for r in result["lines"] if r["kind"] == kind), normalize_title(text).replace("\n", ""))
            for row in result["lines"]:
                self.assertTrue(140.4 <= row["bounds"][1] < row["bounds"][3] <= 853.2)
        canvas = Image.new("RGB", (1920, 1080), "blue")
        before = canvas.tobytes()
        self.assertIsNone(draw_poster_title(canvas, "", ""))
        self.assertEqual(canvas.tobytes(), before)
        self.assertEqual(title_character_count(" **\n 标题 ", "副\n标题"), 7)
        with self.assertRaises(ValueError):
            draw_poster_title(canvas, "主" * 20, "副" * 11)


class PosterIntegrationTests(fixtures.WorkbenchTests):
    def test_preview_is_transparent_and_invalid_style_stops_before_external_calls(self):
        style = {"font": "song", "size": 160, "color": "#22EE88"}
        response = self.post("/api/title/preview", {"title": "标题样式", "settings": style})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["actual_size"], 160)
        image = Image.open(io.BytesIO(base64.b64decode(response.json["image"].split(",")[1])))
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.getpixel((480, 0))[3], 0)
        self.assertIsNotNone(image.getbbox())
        with patch.object(server, "image_configuration") as external, patch.object(server, "launch") as launch:
            response = self.post("/api/jobs", {"text": "文案", "title_settings": {"color": "invalid"}})
            self.assertEqual(response.status_code, 400)
            external.assert_not_called()
            launch.assert_not_called()
            self.assertEqual(list(server.JOBS.iterdir()), [])

    def test_custom_title_recomposition_changes_real_mp4_and_preserves_materials(self):
        identifier = self.create_job(title="标题样式", subtitle_settings={"enabled": False})
        version = self.ready(identifier)
        job = server.read_job(identifier)
        Image.new("RGB", (640, 360), "#245070").save(server.image_paths(job)[0])
        original_audio = (server.audio_path(server.read_job(identifier))).read_bytes()
        original_image = server.image_paths(job)[0].read_bytes()
        def confirm(style):
            response = self.post(f"/api/jobs/{identifier}/confirm", {"image_version": version, "title_settings": style})
            self.assertEqual(response.status_code, 200, response.json)
            deadline = time.monotonic() + 45
            while server.ACTIVE and time.monotonic() < deadline:
                time.sleep(.05)
            current = server.read_job(identifier)
            self.assertEqual(current["video_state"], "done", current["video_message"])
            return current
        first = confirm({"font": "sans-bold", "size": 265, "color": "#FFDA35"})
        first_video = server.video_path(first).read_bytes()
        style = {"font": "song", "size": 160, "color": "#22EE88"}
        final = confirm(style)
        video = server.video_path(final)
        self.assertEqual(final["rendered_title_settings"], style)
        self.assertNotEqual(first_video, video.read_bytes())
        self.assertEqual(original_audio, (server.audio_path(server.read_job(identifier))).read_bytes())
        self.assertEqual(original_image, server.image_paths(final)[0].read_bytes())
        frame = subprocess.run(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-ss", "1", "-i", str(video), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True)
        with Image.open(io.BytesIO(frame.stdout)).convert("RGB") as decoded:
            red, green, blue = decoded.split()
            mask = ImageChops.multiply(green.point(lambda p: 255 if p > 180 else 0), red.point(lambda p: 255 if p < 80 else 0))
            bounds = mask.getbbox()
            self.assertIsNotNone(bounds)
            self.assertLess(bounds[2] - bounds[0], 700)
            self.assertTrue(259 <= bounds[1] < bounds[3] <= 832)
        subprocess.run(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"], capture_output=True, check=True)
        unchanged = video.stat().st_mtime_ns
        with patch.object(server, "launch") as launch:
            response = self.post(f"/api/jobs/{identifier}/confirm", {"image_version": version, "title_settings": style})
            self.assertEqual(response.status_code, 200)
            launch.assert_not_called()
        self.assertEqual(video.stat().st_mtime_ns, unchanged)

    def test_dash_prefixed_titles_are_text_not_command_options(self):
        identifier = self.create_job(title="--主标题", title_secondary="--副标题")
        version = self.ready(identifier)
        self.assertEqual(self.post(f"/api/jobs/{identifier}/confirm", {"image_version": version}).status_code, 200)
        deadline = time.monotonic() + 45
        while server.ACTIVE and time.monotonic() < deadline:
            time.sleep(.05)
        job = server.read_job(identifier)
        log = server.folder(identifier) / "render.log"
        self.assertEqual(job["video_state"], "done", log.read_text() if log.exists() else job["video_message"])
        self.assertTrue(server.video_path(job).is_file())

    def test_combined_limit_validates_before_models_and_multiline_is_saved(self):
        with patch.object(server, "image_configuration") as external, patch.object(server, "launch"):
            response = self.post("/api/jobs", {"text": "文案", "prompt": "景色", "title": "主" * 15, "title_secondary": "副" * 16})
            self.assertEqual(response.status_code, 400)
            external.assert_not_called()
            self.assertEqual(list(server.JOBS.iterdir()), [])
        identifier = self.create_job(title=MAIN, title_secondary=SECONDARY)
        job = server.read_job(identifier)
        self.assertEqual(job["title"], MAIN + "\n" + SECONDARY)
        self.assertEqual(job["title_secondary"], "")
        self.assertEqual(job["title_style"], "clean-yellow")
        self.assertEqual(title_character_count(job["title"], job["title_secondary"]), 26)

    def test_ui_combined_count_submission_and_refresh(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertNotIn('id="titleSecondary"', html)
        self.assertIn('id="titleFont"', html)
        self.assertIn('id="titleColor"', html)
        self.assertIn('id="titleSize"', html)
        script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
        harness = r'''
const assert=require('node:assert/strict'),els=new Map(),calls=[];
function el(id){if(!els.has(id))els.set(id,{value:'',style:{},dataset:{},scrollHeight:145,setAttribute(){},getAttribute(){return null},replaceChildren(){},add(){},removeAttribute(){},pause(){},load(){}});return els.get(id)}
global.document={getElementById:el,querySelectorAll:()=>[],createElement:()=>({}),head:{append(){}}};
global.Audio=class{pause(){}removeAttribute(){}load(){}};global.Option=class{};
global.localStorage={getItem(){return null},setItem(){},removeItem(){}};global.window={addEventListener(){}};
global.fetch=async(path,options)=>{calls.push({path,options});return {ok:true,json:async()=>({jobs:[],id:'test',text:'测试',title:'统一标题',title_secondary:'',title_style:'clean-yellow',provider:'manual',audio_state:'queued',image_state:'ready',video_state:'idle'})}};
global.setInterval=()=>1;global.clearInterval=()=>{};
'''
        assertions = r'''
(async()=>{
el('title').value='可信通信\n如何真正落地\n——中国方案的\n产业协同\n与应用路径';el('title').oninput();
assert.equal(titleCount(),26);assert.ok(el('titleHint').textContent.includes('26 / 30'));
el('imageMode').value='url';el('photoUrl').value='https://example.com/photo.png';el('text').value='测试';
await el('generate').onclick();const submitted=calls.find(c=>c.path==='/api/jobs'&&c.options?.method==='POST');
assert.ok(submitted);const body=JSON.parse(submitted.options.body);assert.equal(body.title,el('title').value);assert.ok(!Object.hasOwn(body,'title_secondary'));
assert.equal(el('jobTitle').textContent,'统一标题');
el('title').value='字'.repeat(31);el('title').oninput();
const before=calls.length;await el('generate').onclick();assert.equal(calls.length,before);assert.ok(el('error').textContent.includes('视频标题最多 30 字'));
clearWorkspaceUI();assert.equal(el('title').value,'');assert.equal(titleCount(),0);assert.ok(el('titleHint').textContent.includes('0 / 30'));
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(["/opt/homebrew/bin/node", "-"], input=harness + script + assertions, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_mp4_unified_yellow_title_without_panel_and_download(self):
        identifier = self.create_job(title=MAIN, title_secondary=SECONDARY)
        version = self.ready(identifier)
        Image.new("RGB", (640, 360), "#245070").save(server.image_paths(server.read_job(identifier))[0])
        self.assertEqual(self.post(f"/api/jobs/{identifier}/confirm", {"image_version": version}).status_code, 200)
        deadline = time.monotonic() + 45
        while server.ACTIVE and time.monotonic() < deadline:
            time.sleep(.05)
        job = server.read_job(identifier)
        self.assertEqual(job["video_state"], "done", job["video_message"])
        video = server.video_path(job)
        info = server.probe(video)
        self.assertTrue(any(stream["codec_type"] == "audio" for stream in info["streams"]))
        self.assertLess(abs(float(info["format"]["duration"]) - job["audio_duration"]), 1.5)
        frame = subprocess.run(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-ss", str(job["audio_duration"] / 2), "-i", str(video), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True)
        with Image.open(io.BytesIO(frame.stdout)) as decoded:
            self.assertEqual(decoded.size, (1920, 1080))
            red, green, blue = decoded.convert("RGB").split()
            masks = [ImageChops.multiply(red.point(lambda p: 255 if p > 170 else 0), ImageChops.multiply(green.point(lambda p: 255 if p < 110 else 0), blue.point(lambda p: 255 if p < 110 else 0))),
                     ImageChops.multiply(red.point(lambda p: 255 if p > 190 else 0), ImageChops.multiply(green.point(lambda p: 255 if p > 150 else 0), blue.point(lambda p: 255 if p < 110 else 0)))]
            self.assertIsNone(masks[0].getbbox())
            bounds = masks[1].getbbox()
            self.assertIsNotNone(bounds)
            self.assertTrue(96 <= bounds[0] < bounds[2] <= 1824)
            self.assertTrue(259 <= bounds[1] < bounds[3] <= 832)
            center = decoded.convert("RGB").getpixel((960, 10))
            self.assertTrue(all(abs(a - b) < 6 for a, b in zip(center, (36, 80, 112))))
            side = decoded.convert("RGB").getpixel((20, 540))
            self.assertTrue(all(abs(a - b) < 6 for a, b in zip(side, (36, 80, 112))))
            if os.environ.get("WORKBENCH_QA_DIR"):
                decoded.save(Path(os.environ["WORKBENCH_QA_DIR"]) / "1.6.1实际MP4标题帧.png")
        subprocess.run(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"], capture_output=True, check=True)
        with self.client.get(f"/api/jobs/{identifier}/media/video?download=1") as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response.headers["Content-Disposition"])


for name in dir(fixtures.WorkbenchTests):
    if name.startswith("test_") and name not in PosterIntegrationTests.__dict__:
        setattr(PosterIntegrationTests, name, None)
