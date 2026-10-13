"""已有音频直接合成：真实媒体、无模型调用、原文件与日志、失败和重试。"""
import base64
import hashlib
import io
import json
import random
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from werkzeug.datastructures import FileStorage
from PIL import Image
import app
import audio_import
from scripts.compose_video import FFMPEG


class AudioImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.jobs=self.root/'jobs';self.jobs.mkdir()
        self.enterContext(patch.object(app,'JOBS',self.jobs))
        self.enterContext(patch.object(app.records,'LOG_FILE',self.root/'events.jsonl'))
        self.enterContext(patch.object(app,'synthesize',side_effect=AssertionError('unexpected cloud speech')))
        self.enterContext(patch.object(app.omnivoice_local,'run',side_effect=AssertionError('unexpected local model')))
        self.client=app.app.test_client();self.headers={'Host':'127.0.0.1','X-Local-Token':app.TOKEN}
        self.photo=io.BytesIO();Image.new('RGB',(640,360),'#334455').save(self.photo,'PNG')
        self.sources={}
        for suffix in ['mp3','wav','m4a']:
            path=self.root/('source.'+suffix)
            subprocess.run([FFMPEG,'-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=3',str(path)],check=True)
            self.sources[suffix]=path.read_bytes()

    def payload(self,raw=None,filename='已有旁白.mp3',metadata=None):
        settings={'photos':[{'photo_name':'照片.png','photo_data':base64.b64encode(self.photo.getvalue()).decode()}],
                  'music_settings':{'enabled':True,'track_id':'vaporware','volume':15,'loop_seconds':180}}
        settings.update(metadata or {})
        return {'metadata':json.dumps(settings),'audio':(io.BytesIO(raw if raw is not None else self.sources['mp3']),filename)}

    def wait(self,identifier,part):
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            job=app.read_job(identifier)
            if job[part+'_state'] not in {'queued','working'} and (identifier,part) not in app.ACTIVE:return job
            time.sleep(.03)
        self.fail('worker did not finish')

    def test_existing_mp3_no_script_to_actual_mp4_original_and_logs(self):
        result=self.client.post('/api/jobs/import-audio',data=self.payload(metadata={'subtitle_settings':{'enabled':True}}),headers=self.headers)
        self.assertEqual(result.status_code,201,result.json);j=result.json;identifier=j['id']
        ready=self.wait(identifier,'audio');self.assertEqual(ready['audio_state'],'ready',ready['audio_message'])
        self.assertEqual(ready['audio_provider'],'upload');self.assertEqual(ready['text'],'')
        self.assertFalse(ready['subtitle_settings']['enabled']);self.assertEqual(ready['video_filename'],'已有旁白.mp4')
        self.assertEqual((app.folder(identifier)/ready['imported_audio']['source_filename']).read_bytes(),self.sources['mp3'])
        digest=hashlib.sha256((app.audio_path(app.read_job(identifier))).read_bytes()).hexdigest()
        result=self.client.post(f'/api/jobs/{identifier}/confirm',json={'image_version':ready['image_version']},headers=self.headers)
        self.assertEqual(result.status_code,200,result.json)
        done=self.wait(identifier,'video');self.assertEqual(done['video_state'],'done',done['video_message'])
        self.assertEqual(done['subtitle_timing'],'off');self.assertAlmostEqual(done['video_duration'],ready['audio_duration'],delta=.2)
        self.assertEqual(hashlib.sha256((app.audio_path(app.read_job(identifier))).read_bytes()).hexdigest(),digest)
        subprocess.run([FFMPEG,'-v','error','-i',str(app.video_path(done)),'-f','null','-'],check=True)
        with self.client.get(f'/api/jobs/{identifier}/media/video?download=1') as media:self.assertEqual(media.status_code,200)
        logs=self.client.get('/api/records?kind=all').json['entries']
        imports=[r for r in logs if r['event']=='audio_import'];self.assertEqual(len(imports),1)
        self.assertEqual(imports[0]['size_bytes'],len(self.sources['mp3']));self.assertGreater(imports[0]['audio_duration'],2.9)
        self.assertEqual(self.client.get('/api/records?kind=models').json['total'],0)
        self.assertNotIn('已有旁白',json.dumps(logs,ensure_ascii=False))
        app.CONTROLS.pop(identifier,None)

    def test_wav_m4a_and_estimated_subtitles_use_full_imported_duration(self):
        for suffix in ['wav','m4a']:
            result=self.client.post('/api/jobs/import-audio',data=self.payload(raw=self.sources[suffix],filename='audio.'+suffix,
                metadata={'text':'第一句旁白。第二句旁白。','subtitle_settings':{'enabled':True}}),headers=self.headers)
            self.assertEqual(result.status_code,201,result.json)
            ready=self.wait(result.json['id'],'audio');self.assertEqual(ready['audio_state'],'ready',ready['audio_message'])
            self.assertAlmostEqual(ready['audio_duration'],3,delta=.1)
            if suffix=='wav':
                result=self.client.post(f"/api/jobs/{ready['id']}/confirm",json={'image_version':ready['image_version']},headers=self.headers)
                self.assertEqual(result.status_code,200)
                done=self.wait(ready['id'],'video');self.assertEqual(done['video_state'],'done',done['video_message']);self.assertEqual(done['subtitle_timing'],'estimated')
                timeline=json.loads((app.folder(ready['id'])/'subtitles.json').read_text())
                self.assertAlmostEqual(timeline['captions'][-1]['end'],ready['audio_duration'])
            app.CONTROLS.pop(ready['id'],None)

    def test_auth_invalid_inputs_size_failure_and_retry_never_generates_speech(self):
        self.assertEqual(self.client.post('/api/jobs/import-audio',data=self.payload()).status_code,403)
        for payload in [self.payload(filename='a.exe'),self.payload(raw=b''),{**self.payload(),'metadata':'bad json'},self.payload(metadata={'photos':[]}),self.payload(metadata={'title':'长'*31})]:
            result=self.client.post('/api/jobs/import-audio',data=payload,headers=self.headers)
            self.assertEqual(result.status_code,400,result.json)
            self.assertEqual(list(self.jobs.iterdir()),[])
        with patch.object(audio_import,'MAX_BYTES',10):
            result=self.client.post('/api/jobs/import-audio',data=self.payload(),headers=self.headers)
            self.assertEqual(result.status_code,400);self.assertEqual(list(self.jobs.iterdir()),[])
        result=self.client.post('/api/jobs/import-audio',data=self.payload(raw=b'invalid audio'),headers=self.headers)
        failed=self.wait(result.json['id'],'audio');self.assertEqual(failed['audio_state'],'failed');self.assertIn('无法完整解码',failed['audio_message'])
        self.assertFalse((app.folder(failed['id'])/'audio.mp3').exists())
        source=audio_import.source_path(app.folder(failed['id']),failed['imported_audio']);source.write_bytes(self.sources['mp3'])
        result=self.client.post(f"/api/jobs/{failed['id']}/retry-audio",json={'audio_provider':'edge-tts'},headers=self.headers)
        self.assertEqual(result.status_code,400)
        result=self.client.post(f"/api/jobs/{failed['id']}/retry-audio",json={},headers=self.headers)
        self.assertEqual(result.status_code,200)
        ready=self.wait(failed['id'],'audio');self.assertEqual(ready['audio_state'],'ready',ready['audio_message'])
        self.assertEqual(self.client.get('/api/records?kind=models').json['total'],0)
        app.CONTROLS.pop(failed['id'],None)

    def test_small_audio_and_24_mb_photo_form_field_to_real_mp4(self):
        # 旧版本的 metadata 字段 >500000 字节，即便文件很小也返回 413。
        buffer=io.BytesIO()
        Image.frombytes('RGB',(900,900),random.Random(12).randbytes(900*900*3)).save(buffer,'PNG')
        photo=buffer.getvalue()
        self.assertGreater(len(photo),2_400_000)
        wave=self.root/'small-24mb.wav'
        subprocess.run([FFMPEG,'-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=14','-ac','2',str(wave)],check=True)
        raw=wave.read_bytes();self.assertLess(len(raw),3*1024*1024)
        payload=self.payload(raw=raw,filename='2.4MB已有音频.wav',metadata={
            'photos':[{'photo_name':'2.4MB照片.png','photo_data':base64.b64encode(photo).decode()}]})
        self.assertGreater(len(payload['metadata']),500000)
        result=self.client.post('/api/jobs/import-audio',data=payload,headers=self.headers)
        self.assertEqual(result.status_code,201,result.json)
        ready=self.wait(result.json['id'],'audio');self.assertEqual(ready['audio_state'],'ready',ready['audio_message'])
        self.assertEqual((app.folder(ready['id'])/ready['imported_audio']['source_filename']).read_bytes(),raw)
        result=self.client.post(f"/api/jobs/{ready['id']}/confirm",json={'image_version':ready['image_version']},headers=self.headers)
        self.assertEqual(result.status_code,200,result.json)
        done=self.wait(ready['id'],'video');self.assertEqual(done['video_state'],'done',done['video_message'])
        self.assertAlmostEqual(done['video_duration'],14,delta=.2)
        subprocess.run([FFMPEG,'-v','error','-i',str(app.video_path(done)),'-f','null','-'],check=True)
        app.CONTROLS.pop(ready['id'],None)

    def test_audio_limit_inclusive_500mb_and_unrelated_routes_unchanged(self):
        self.assertEqual(audio_import.MAX_BYTES,500*1024*1024)
        # 缩小阈值验证恰好等于上限可保存、超出一个字节被拒绝；不分配 500 MB 测试垃圾。
        with patch.object(audio_import,'MAX_BYTES',10):
            path,metadata=audio_import.save_upload(FileStorage(stream=io.BytesIO(b'a'*10),filename='audio.mp3'),self.root)
            self.assertEqual(path.stat().st_size,10)
            with self.assertRaisesRegex(ValueError,'500 MB'):
                audio_import.save_upload(FileStorage(stream=io.BytesIO(b'a'*11),filename='audio.mp3'),self.root)
        with app.app.test_request_context('/api/jobs/import-audio',headers={'Host':'127.0.0.1'}):
            app.guard()
            self.assertEqual(app.request.max_content_length,560*1024*1024)
            self.assertEqual(app.request.max_form_memory_size,64*1024*1024)
        with app.app.test_request_context('/api/jobs',headers={'Host':'127.0.0.1'}):
            app.guard()
            self.assertEqual(app.request.max_content_length,64*1024*1024)
            self.assertEqual(app.request.max_form_memory_size,500000)

    def test_source_path_rejects_escape_and_symlinks(self):
        for metadata in [{'source_filename':'../source-audio.mp3'},{'source_filename':'source-audio.exe'}]:
            with self.assertRaises(ValueError):audio_import.source_path(self.root,metadata)
        (self.root/'source-audio.mp3').symlink_to(self.root/'source.mp3')
        with self.assertRaises(ValueError):audio_import.source_path(self.root,{'source_filename':'source-audio.mp3'})

if __name__=='__main__':unittest.main()
