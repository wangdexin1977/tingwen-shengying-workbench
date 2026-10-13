"""本机多选照片：隔离任务、无收费模型、真实 MP4 编码和帧内容检查。"""
import base64
import hashlib
import io
import re
import shutil
import subprocess
import time
from unittest.mock import patch

from PIL import Image
import app as server
import photo_import
import records
import test_workbench as fixtures


def batch(colors=("red", "green", "blue")):
    photos = []
    for index, color in enumerate(colors):
        output = io.BytesIO()
        Image.new("RGB", (640, 360), color).save(output, "PNG")
        photos.append({"photo_name": f"照片{index + 1}.png", "photo_data": base64.b64encode(output.getvalue()).decode()})
    return {"image_mode": "upload", "photos": photos}


class LocalPhotoTests(fixtures.WorkbenchTests):
    def test_batch_create_saves_all_photos_in_order_without_ai(self):
        with patch.object(server, "launch") as launch, patch.object(server, "image_configuration") as ai:
            response = self.post("/api/jobs", {"text": "多图测试", **batch()})
        self.assertEqual(response.status_code, 201, response.json)
        job = response.json
        self.assertEqual(len(job["gallery"]), 3)
        self.assertEqual(job["image_state"], "ready")
        self.assertEqual(job["video_state"], "idle")
        self.assertEqual(job["provider"], "manual")
        self.assertFalse(job["confirmed_version"])
        ai.assert_not_called()
        launch.assert_called_once_with(job["id"], "audio")
        self.assertEqual([p["metadata"]["source_name"] for p in job["gallery"]], ["照片1.png", "照片2.png", "照片3.png"])
        self.assertEqual(records.entries()["entries"][0]["count"], 3)
        for item, color in zip(job["gallery"], ((255, 0, 0), (0, 128, 0), (0, 0, 255))):
            with self.client.get(item["preview_url"]) as picture:
                self.assertEqual(picture.status_code, 200)
                with Image.open(io.BytesIO(picture.data)) as decoded:
                    self.assertEqual(decoded.getpixel((320, 180)), color)
                    self.assertFalse(decoded.getexif())

    def test_one_photo_batch_keeps_single_image_contract(self):
        identifier = self.create_job(**batch(("red",)))
        job = server.read_job(identifier)
        self.assertEqual(job["image_gallery"], [])
        self.assertEqual(len(server.image_paths(job)), 1)
        with self.client.get(f"/api/jobs/{identifier}/media/image") as response:
            self.assertEqual(response.status_code, 200)

    def test_invalid_batch_does_not_create_partial_task(self):
        payloads = [{"photos": []}, {"photos": "invalid"}, {"photos": [None]},
                    {"photos": [{}]}, {"photos": [{}] * 51},
                    {**batch(), "photos": [batch()["photos"][0], {"photo_data": "invalid"}]}]
        for payload in payloads:
            with patch.object(server, "launch") as launch:
                response = self.post("/api/jobs", {"text": "校验", "image_mode": "upload", **payload})
            self.assertEqual(response.status_code, 400, response.json)
            self.assertEqual(list(server.JOBS.iterdir()), [])
            launch.assert_not_called()

    def test_batch_total_size_and_single_size_limits(self):
        with patch.object(photo_import, "MAX_BATCH_BYTES", 100), patch.object(photo_import, "local_photo") as decode:
            with self.assertRaisesRegex(photo_import.PhotoError, "40 MB"):
                photo_import.local_photos(batch())
            decode.assert_not_called()
        with patch.object(photo_import, "MAX_BYTES", 100):
            with self.assertRaisesRegex(photo_import.PhotoError, "第 1 张"):
                photo_import.local_photos(batch())

    def test_replacing_and_filtering_preserve_audio_and_old_media(self):
        identifier = self.create_job()
        version = self.ready(identifier)
        original = server.read_job(identifier)
        old_image = server.image_paths(original)[0]
        video = server.video_path(original)
        video.write_bytes(b"saved-video")
        audio = server.audio_path(server.read_job(identifier))
        digest = hashlib.sha256(audio.read_bytes()).hexdigest()
        server.update(identifier, video_state="done", confirmed_version=version, submitting=True)
        with patch.object(server, "client_for") as ai:
            response = self.post(f"/api/jobs/{identifier}/use-image", batch())
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(len(response.json["gallery"]), 3)
        self.assertEqual(response.json["video_state"], "idle")
        self.assertFalse(response.json["confirmed_version"])
        self.assertTrue(response.json["submitting"])
        ai.assert_not_called()
        self.assertTrue(old_image.exists())
        self.assertEqual(video.read_bytes(), b"saved-video")
        self.assertEqual(hashlib.sha256(audio.read_bytes()).hexdigest(), digest)
        self.assertEqual(self.post(f"/api/jobs/{identifier}/confirm", {"image_version": version}).status_code, 409)
        gallery_paths = server.image_paths(server.read_job(identifier))
        fresh = response.json["image_version"]
        response = self.post(f"/api/jobs/{identifier}/select-images", {"image_version": fresh, "selected_indices": [0, 2]})
        self.assertEqual(len(response.json["gallery"]), 2)
        self.assertTrue(all(path.exists() for path in gallery_paths))
        self.assertEqual([p["metadata"]["source_name"] for p in response.json["gallery"]], ["照片1.png", "照片3.png"])
        self.assertEqual(hashlib.sha256(audio.read_bytes()).hexdigest(), digest)

    def test_bad_replacement_preserves_whole_existing_gallery(self):
        identifier = self.create_job(**batch())
        old = server.read_job(identifier)
        old_files = {path: path.read_bytes() for path in server.image_paths(old)}
        data = batch()
        data["photos"][1]["photo_data"] = "broken"
        response = self.post(f"/api/jobs/{identifier}/use-image", data)
        self.assertEqual(response.status_code, 400)
        self.assertIn("第 2 张", response.json["error"])
        self.assertEqual(server.read_job(identifier), old)
        self.assertEqual({path: path.read_bytes() for path in server.image_paths(old)}, old_files)
        self.assertFalse(server.ACTIVE)

    def test_real_api_pipeline_three_uploaded_photos_equal_time_and_order(self):
        def cached_audio(job_id, control):
            path = server.audio_path(server.read_job(job_id))
            shutil.copyfile(server.ROOT / "assets/previews/zh-CN-YunxiNeural.mp3", path)
            server.update(job_id, audio_state="ready", audio_duration=float(server.probe(path)["format"]["duration"]))
        with patch.dict(server.WORKERS, {"audio": cached_audio}), patch.object(server, "client_for") as ai:
            response = self.post("/api/jobs", {"text": "本机三图均分验证", **batch()})
            self.assertEqual(response.status_code, 201, response.json)
            identifier = response.json["id"]
            deadline = time.monotonic() + 10
            while server.ACTIVE and time.monotonic() < deadline:
                time.sleep(.02)
            job = server.read_job(identifier)
            self.assertEqual(job["audio_state"], "ready")
            response = self.post(f"/api/jobs/{identifier}/confirm", {"image_version": job["image_version"]})
            self.assertEqual(response.status_code, 200, response.json)
            deadline = time.monotonic() + 40
            while server.ACTIVE and time.monotonic() < deadline:
                time.sleep(.05)
            result = server.read_job(identifier)
            self.assertEqual(result["video_state"], "done", result.get("video_message"))
            ai.assert_not_called()
        self.assertEqual(result["image_durations"], [result["audio_duration"] / 3] * 3)
        video = server.video_path(result)
        info = server.probe(video)
        self.assertLess(abs(float(info["format"]["duration"]) - result["audio_duration"]), .5)
        stream = next(s for s in info["streams"] if s["codec_type"] == "video")
        self.assertEqual((stream["width"], stream["height"]), (1920, 1080))
        decoded = subprocess.run(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"], capture_output=True)
        self.assertEqual(decoded.returncode, 0, decoded.stderr)
        for index, color in enumerate(((255, 0, 0), (0, 128, 0), (0, 0, 255))):
            at = result["audio_duration"] / 3 * (index + .5)
            frame = subprocess.run(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-ss", str(at), "-i", str(video), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True)
            with Image.open(io.BytesIO(frame.stdout)) as picture:
                pixel = picture.convert("RGB").getpixel((960, 540))
            self.assertTrue(all(abs(a - b) < 12 for a, b in zip(pixel, color)), (index, pixel))
        with self.client.get(f"/api/jobs/{identifier}/media/video?download=1") as download:
            self.assertEqual(download.status_code, 200)
            self.assertIn("attachment", download.headers["Content-Disposition"])
        # 删除中间照片后重新合成，剩余两张各占完整音频的一半。
        response = self.post(f"/api/jobs/{identifier}/select-images", {"image_version": result["image_version"], "selected_indices": [0, 2]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.post(f"/api/jobs/{identifier}/confirm", {"image_version": response.json["image_version"]}).status_code, 200)
        deadline = time.monotonic() + 40
        while server.ACTIVE and time.monotonic() < deadline:
            time.sleep(.05)
        result = server.read_job(identifier)
        self.assertEqual(result["video_state"], "done")
        self.assertEqual(result["image_durations"], [result["audio_duration"] / 2] * 2)
        for index, color in enumerate(((255, 0, 0), (0, 0, 255))):
            at = result["audio_duration"] / 2 * (index + .5)
            frame = subprocess.run(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-ss", str(at), "-i", str(video), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True)
            with Image.open(io.BytesIO(frame.stdout)) as picture:
                pixel = picture.convert("RGB").getpixel((960, 540))
            self.assertTrue(all(abs(a - b) < 12 for a, b in zip(pixel, color)), (index, pixel))

    def test_frontend_multiselect_preview_remove_upload_and_clear(self):
        page = self.client.get("/").get_data(as_text=True)
        for identifier in ["localPhoto", "replacePhoto"]:
            self.assertRegex(page, rf'<input id="{identifier}"[^>]* multiple')
        script = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
        harness = r'''
const assert=require('node:assert/strict'),els=new Map();
function element(){return {value:'',files:[],style:{},dataset:{},scrollHeight:145,attributes:{},children:[],append(...items){this.children.push(...items)},replaceChildren(...items){this.children=items},setAttribute(k,v){this.attributes[k]=v},getAttribute(k){return this.attributes[k]||null},removeAttribute(k){delete this.attributes[k]},add(){},pause(){},load(){}}}
function el(id){if(!els.has(id))els.set(id,element());return els.get(id)}
global.document={getElementById:el,querySelectorAll:()=>[],createElement:element,head:{append(){}}};
global.Audio=class{pause(){}removeAttribute(){}load(){}};global.Option=class{};
global.localStorage={getItem(){return null},setItem(){},removeItem(){}};
global.window={addEventListener(){},location:{reload(){}}};
const revoked=[];let urlCount=0;
global.URL={createObjectURL:f=>'blob:test-'+(++urlCount),revokeObjectURL:url=>revoked.push(url)};
global.DataTransfer=class{constructor(){this.files=[];this.items={add:file=>this.files.push(file)}}};
global.FileReader=class{readAsDataURL(file){this.result='data:image/png;base64,'+file.bytes;this.onload()}};
const calls=[];global.fetch=async(path,options)=>{calls.push([path,options]);return {ok:true,status:200,json:async()=>({jobs:[]})}};
'''
        assertions = r'''
async function main(){
 const files=[{name:'1.png',size:100,bytes:'QQ=='},{name:'2.png',size:200,bytes:'Qg=='},{name:'3.png',size:300,bytes:'Qw=='}];
 el('localPhoto').files=files;el('localPhoto').onchange();
 assert.equal(selectedLocalPhotos.length,3);assert.equal(el('localPhotoPreview').children.length,3);assert.equal(el('localPhoto').files.length,3);
 assert.equal(el('localPhotoPreview').children[0].children[0].href,'blob:test-1');
 el('localPhotoPreview').children[1].children[2].onclick();
 assert.equal(selectedLocalPhotos.length,2);assert.equal(revoked.length,1);assert.equal(el('localPhoto').files.length,2);
 const payload=await readPhotos(selectedLocalPhotos.map(p=>p.file));
 assert.deepEqual(payload.photos.map(p=>p.photo_name),['1.png','3.png']);
 let submitted;api=async(path,data)=>{if(path==='/api/jobs'&&data){submitted=data;return {id:'test'}};return {jobs:[]}};
 select=()=>{};render=()=>{};history=async()=>{};
 el('imageMode').value='upload';el('text').value='测试文案';await el('generate').onclick();
 assert.equal(submitted.photos.length,2);assert.equal(submitted.image_mode,'upload');assert.ok(!submitted.photo_data);
 assert.equal(selectedLocalPhotos.length,2);assert.equal(el('localPhoto').disabled,false);
 await assert.rejects(readPhotos([]));await assert.rejects(readPhotos(Array(51).fill(files[0])));
 await assert.rejects(readPhotos([{name:'large',size:21*1024*1024}]));
 await assert.rejects(readPhotos(Array(3).fill({name:'large',size:15*1024*1024})));
 el('localPhoto').files=[{name:'too large',size:22*1024*1024}];el('localPhoto').onchange();assert.equal(selectedLocalPhotos.length,2);
 job={id:'test'};usePhoto=async(load)=>{submitted=await load()};el('replacePhoto').files=files;await el('replacePhoto').onchange();assert.equal(submitted.photos.length,3);
 clearWorkspaceUI();assert.equal(selectedLocalPhotos.length,0);assert.equal(el('localPhotoPreview').children.length,0);assert.equal(revoked.length,3);
}
main().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(["/opt/homebrew/bin/node", "-"], input=harness + script + assertions,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


for name in dir(fixtures.WorkbenchTests):
    if name.startswith("test_") and name not in LocalPhotoTests.__dict__:
        setattr(LocalPhotoTests, name, None)
