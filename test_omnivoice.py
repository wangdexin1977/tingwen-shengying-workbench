"""OmniVoice 重试与音色库：隔离测试不加载实际大模型、不访问外网。"""
import asyncio
import base64
import io
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from types import SimpleNamespace
from aiohttp import WSServerHandshakeError
from PIL import Image
import app
import omnivoice_local as omni
import speech
import subtitles


class OmniVoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.jobs = self.root / 'jobs'; self.jobs.mkdir()
        self.model = self.root / 'model'; self.model.mkdir()
        for p in ['.venv/bin','mlx_indextts','models/OmniVoice-bfloat16']:(self.model/p).mkdir(parents=True,exist_ok=True)
        (self.model/'.venv/bin/python').symlink_to(app.sys.executable)
        (self.model/'mlx_indextts/webui.py').write_text('')
        self.voice_id = 'a'*16
        self.library = self.model/'outputs/webui/voices/library'/self.voice_id; self.library.mkdir(parents=True)
        (self.library/'metadata.json').write_text(json.dumps({'id':self.voice_id,'name':'测试音色','source_filename':'source.wav'}))
        (self.library/'source.wav').write_bytes(b'source'); (self.library/'preview.wav').write_bytes(b'preview')
        self.enterContext(patch.object(app,'JOBS',self.jobs));self.enterContext(patch.object(app.records,'LOG_FILE',self.root/'events.jsonl'))
        self.enterContext(patch.object(omni, 'project_root', return_value=self.model))
        self.enterContext(patch.dict(os.environ,{'INDEXTTS_PROJECT_ROOT':str(self.model)}))
        self.client=app.app.test_client();self.headers={'Host':'127.0.0.1','X-Local-Token':app.TOKEN}

    def job(self):
        photo=io.BytesIO();Image.new('RGB',(640,360),'#334455').save(photo,'PNG')
        with patch.object(app,'launch'):
            result=self.client.post('/api/jobs',headers=self.headers,json={'text':'第一句。第二句。','image_mode':'upload','photo_name':'测试.png','photo_data':base64.b64encode(photo.getvalue()).decode()})
        self.assertEqual(result.status_code,201)
        return result.json

    def test_403_stops_outer_retries_and_never_exposes_request_parameters(self):
        calls=[]
        class Forbidden:
            def __init__(self,*args):calls.append(True)
            async def stream(self):
                raise WSServerHandshakeError(SimpleNamespace(real_url='wss://speech.platform.bing.com?token=secret'),(),status=403,message='Invalid response status')
                yield {}
        error="403, message='Invalid response status', url='wss://speech.platform.bing.com/edge?Sec-MS-GEC=secret'"
        self.assertIn('OmniVoice',speech.speech_error_message(error));self.assertNotIn('secret',speech.speech_error_message(error))
        with self.assertRaisesRegex(speech.SpeechServiceError,'HTTP 403'):
            asyncio.run(speech.synthesize('测试','voice',self.root,app.Control(),lambda _:None,factory=Forbidden))
        self.assertEqual(len(calls),1);self.assertFalse((self.root/'audio.mp3').exists())

    def test_api_retry_uses_selected_voice_keeps_cloud_parts_and_real_mp3(self):
        j=self.job();identifier=j['id'];directory=app.folder(identifier)
        app.update(identifier,audio_state='failed',audio_message="403, message='Invalid response status', url='wss://speech.platform.bing.com?token=secret'")
        old=directory/'part-0000.mp3';old.write_bytes(b'cloud-part')
        before=app.read_job(identifier)
        view=self.client.get('/api/jobs/'+identifier,headers=self.headers).json
        self.assertNotIn('secret',view['audio_message']);self.assertEqual(app.read_job(identifier)['audio_message'],before['audio_message'])
        bad=self.client.post('/api/jobs/'+identifier+'/retry-audio',headers=self.headers,json={'audio_provider':'omnivoice','omnivoice_settings':{'voice_id':'../escape'}})
        self.assertEqual(bad.status_code,400);self.assertEqual(app.read_job(identifier),before)
        selected={'voice_id':self.voice_id,'expression':'follow_reference'}
        with patch.object(app,'launch') as launch:
            result=self.client.post('/api/jobs/'+identifier+'/retry-audio',headers=self.headers,json={'audio_provider':'omnivoice','omnivoice_settings':selected})
            self.assertEqual(result.status_code,200);launch.assert_called_once_with(identifier,'audio')
        self.assertEqual(result.json['omnivoice_settings']['voice_id'],self.voice_id)
        self.assertEqual(result.json['image_version'],j['image_version']);self.assertEqual(old.read_bytes(),b'cloud-part')
        wave=directory/'omnivoice/output.wav';wave.parent.mkdir()
        subprocess.run(['/opt/homebrew/bin/ffmpeg','-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=1',str(wave)],check=True)
        with patch.object(omni,'run',return_value={'ok':True,'audio_path':str(wave)}),patch.object(app,'synthesize',side_effect=AssertionError('cloud called')):
            app.audio_worker(identifier,app.Control())
        ready=app.read_job(identifier);self.assertEqual(ready['audio_state'],'ready');self.assertGreater(ready['audio_duration'],.9)
        self.assertEqual(old.read_bytes(),b'cloud-part')
        timeline=subtitles.build_timeline(directory,j['text'],'omnivoice:'+self.voice_id,app.probe,force_estimated=True,
                                          audio_file=app.audio_path(ready))
        self.assertEqual(timeline['mode'],'estimated');self.assertAlmostEqual(timeline['captions'][-1]['end'],ready['audio_duration'])
        with self.client.get('/api/jobs/'+identifier+'/media/audio',headers=self.headers) as result:self.assertEqual(result.status_code,200)

    def test_voice_list_preview_and_import_validation(self):
        data=self.client.get('/api/omnivoice',headers=self.headers).json
        self.assertTrue(data['available']);self.assertEqual(data['voices'][0]['id'],self.voice_id)
        with self.client.get('/api/omnivoice/voices/'+self.voice_id+'/preview') as result:self.assertEqual(result.data,b'preview')
        self.assertEqual(self.client.get('/api/omnivoice/voices/not-a-voice/preview').status_code,404)
        for body in [{'name':'声','filename':'a.exe','audio_data':'YQ=='},{'name':'','filename':'a.wav','audio_data':'YQ=='},{'name':'声','filename':'a.wav','audio_data':'@'}]:
            self.assertEqual(self.client.post('/api/omnivoice/voices',json=body,headers=self.headers).status_code,400)
        with patch.object(omni,'run',return_value={'ok':True,'created':True,'voice_id':self.voice_id}) as run:
            result=self.client.post('/api/omnivoice/voices',json={'name':'声','filename':'a.wav','audio_data':'YQ==','reference_text':'参考原文'},headers=self.headers)
        self.assertEqual(result.status_code,200);self.assertEqual(run.call_args.args[0]['reference_text'],'参考原文')
        (self.library/'preview.wav').unlink();(self.library/'preview.wav').symlink_to(self.root/'outside.wav');(self.root/'outside.wav').write_bytes(b'private')
        self.assertEqual(self.client.get('/api/omnivoice/voices/'+self.voice_id+'/preview').status_code,404)

    def test_cancel_subprocess_does_not_publish_partial_audio(self):
        (self.model/'mlx_indextts/webui.py').write_text('''import threading,time\n_generation_cancelled=threading.Event()\nDEFAULT_SETTINGS={}\nDEFAULT_OMNIVOICE_SETTINGS={}\nDEFAULT_FISH_S2_SETTINGS={}\nDEFAULT_COSYVOICE3_SETTINGS={}\ndef read_user_config():return {}\ndef synthesize(**kwargs):\n while not _generation_cancelled.is_set():time.sleep(.02)\n return None,'已取消',''\n''')
        text=self.root/'narration.txt';text.write_text('测试')
        control=app.Control();threading.Timer(.2,control.cancelled.set).start()
        started=time.monotonic()
        with self.assertRaises(speech.Cancelled):
            omni.run({'text_file':str(text),'voice_id':self.voice_id,'expression':'follow_reference','output_directory':str(self.root)},self.root,control)
        self.assertLess(time.monotonic()-started,3);self.assertFalse((self.root/'audio.mp3').exists())


if __name__=='__main__':unittest.main()
