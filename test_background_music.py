"""隔离音乐库和任务，验证实际混音、循环、导入与重复合成。"""
import array
import base64
import hashlib
import io
import json
import math
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import app
import background_music as music
from scripts.compose_video import FFMPEG, FFPROBE, prepare_music_cycle


def tone(path, frequency, duration):
    subprocess.run([FFMPEG, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    f'sine=frequency={frequency}:duration={duration}', str(path)], check=True)


def amplitude(path, frequency, start=2):
    raw = subprocess.check_output([FFMPEG, '-v', 'error', '-ss', str(start), '-i', str(path),
        '-t', '0.5', '-map', '0:a:0', '-ar', '8000', '-ac', '1', '-f', 'f32le', '-'])
    samples = array.array('f', raw)
    phase = 2 * math.pi * frequency / 8000
    return abs(sum(value * complex(math.cos(i * phase), math.sin(i * phase)) for i, value in enumerate(samples))) * 2 / len(samples)


class BackgroundMusicTests(unittest.TestCase):
    def test_bundled_tracks_decode_sources_and_safe_paths(self):
        tracks = music.catalog()
        builtins = [t for t in tracks if t['builtin']]
        self.assertEqual({t['id'] for t in builtins}, {'vaporware', 'first-light', 'emotional-piano',
                                                    'news-jingle', 'world-anticipation', 'military-war-theme',
                                                    'decisive-hours', 'hearts-of-iron', 'the-hunting', 'acceleration', 'i-spy'})
        client = app.app.test_client()
        for t in builtins:
            path = music.entry(t['id'])['path']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), t['sha256'])
            self.assertIn(t['license'], ('CC0 1.0', 'CC BY 3.0', 'CC BY 4.0'))
            if t['license'].startswith('CC BY'):
                self.assertIn(t['author'].split(' (')[0], t['attribution'])
                self.assertIn(t['license_url'], t['attribution'])
            self.assertTrue(t['source_url'].startswith(('https://opengameart.org/content/', 'https://www.free-stock-music.com/')))
            subprocess.run([FFMPEG, '-v', 'error', '-i', str(path), '-f', 'null', '-'], check=True)
            with client.get(t['preview_url'], headers={'Range': 'bytes=0-9'}) as response:
                self.assertEqual(response.status_code, 206)
                self.assertEqual(len(response.data), 10)
        for identifier in ['../app.py', '/tmp/a', 'user-xxx', 'missing']:
            with self.assertRaises(ValueError):
                music.entry(identifier)
        for settings in [{'volume': 101}, {'volume': float('nan')}, {'volume': True}, {'loop_seconds': 90}, {'enabled': 'true'}, {'track_id': '../x'}]:
            with self.assertRaises(ValueError):
                music.normalize_settings(settings)

    def test_import_auth_validation_persistence_and_duplicate(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(music, 'UPLOADS', Path(directory)/'uploads'):
            path = Path(directory)/'custom.wav'
            tone(path, 880, 1)
            data = {'filename':'我的钢琴.wav','audio_data':base64.b64encode(path.read_bytes()).decode()}
            client = app.app.test_client()
            headers = {'Host':'127.0.0.1','X-Local-Token':app.TOKEN}
            self.assertEqual(client.post('/api/music', json=data).status_code, 403)
            result = client.post('/api/music', json=data, headers=headers)
            self.assertEqual(result.status_code, 201, result.json)
            track = result.json['track']
            self.assertFalse(track['builtin'])
            self.assertEqual(client.post('/api/music',json=data,headers=headers).json['track']['id'],track['id'])
            self.assertIn(track['id'],[t['id'] for t in client.get('/api/music').json['tracks']])
            with client.get(track['preview_url'], headers={'Range':'bytes=0-9'}) as response:
                self.assertEqual(response.status_code,206)
                self.assertEqual(len(response.data),10)
            for bad in [{**data,'filename':'a.exe'},{**data,'audio_data':'%%%%'},{**data,'audio_data':base64.b64encode(b'bad audio').decode()}]:
                self.assertEqual(client.post('/api/music',json=bad,headers=headers).status_code,400)
            self.assertEqual(len(list(music.UPLOADS.glob('user-*.json'))),1)

    def test_real_mp4_volume_voice_preservation_and_two_minute_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new('RGB',(320,180),'#244466').save(root/'cover.png')
            tone(root/'voice.wav',440,124)
            tone(root/'music.wav',880,5)
            def compose(name,volume=None):
                cmd=[sys.executable,str(app.ROOT/'scripts/compose_video.py'),'--bg',str(root/'cover.png'),'--audio',str(root/'voice.wav'),'--out',str(root/name),'--no-subtitle']
                if volume is not None: cmd+=['--music',str(root/'music.wav'),'--music-loop-seconds','120','--music-volume',str(volume)]
                result=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                return root/name
            dry=compose('dry.mp4')
            low=compose('low.mp4',15)
            high=compose('high.mp4',60)
            zero=compose('zero.mp4',0)
            self.assertLess(abs(amplitude(low,440)/amplitude(dry,440)-1),.03)
            self.assertGreater(amplitude(low,880),amplitude(dry,880)*100)
            self.assertAlmostEqual(amplitude(high,880)/amplitude(low,880),4,delta=.15)
            self.assertAlmostEqual(amplitude(zero,440),amplitude(dry,440),delta=.0001)
            self.assertLess(amplitude(zero,880),.0001)
            for second in [2,17,117,122]:
                self.assertGreater(amplitude(low,880,second),.01)
            # 交界处确有柔和淡入，随后恢复音乐，视频和朗读都持续完整。
            self.assertLess(amplitude(low,880,120),amplitude(low,880,117)*.4)
            info=app.probe(low)
            self.assertAlmostEqual(float(info['format']['duration']),124,delta=.2)
            self.assertEqual((info['streams'][0]['width'],info['streams'][0]['height']),(320,180))
            subprocess.run([FFMPEG,'-v','error','-i',str(low),'-f','null','-'],check=True)
            for seconds in [180,300]:
                output=prepare_music_cycle(root/'music.wav',seconds,root/'cycle.wav')
                self.assertAlmostEqual(float(app.probe(output)['format']['duration']),seconds,delta=.05)

    def test_api_multiphoto_mix_recompose_noop_and_legacy_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); jobs=root/'jobs';jobs.mkdir()
            with patch.object(app,'JOBS',jobs), patch.object(app.records,'LOG_FILE',root/'events.jsonl'):
                client=app.app.test_client();headers={'Host':'127.0.0.1','X-Local-Token':app.TOKEN}
                photos=[]
                for color in ['red','green','blue']:
                    buffer=io.BytesIO();Image.new('RGB',(640,360),color).save(buffer,'PNG')
                    photos.append({'photo_name':color+'.png','photo_data':base64.b64encode(buffer.getvalue()).decode()})
                payload={'text':'背景音乐测试。','image_mode':'upload','photos':photos,'subtitle_settings':{'enabled':False},'music_settings':dict(music.DEFAULTS, enabled=True)}
                with patch.object(app,'launch'):
                    response=client.post('/api/jobs',json=payload,headers=headers)
                self.assertEqual(response.status_code,201,response.json)
                job=response.json;identifier=job['id'];path=app.audio_path(app.read_job(identifier));tone(path,440,3)
                app.update(identifier,audio_state='ready',audio_duration=float(app.probe(path)['format']['duration']))
                audio_digest=hashlib.sha256(path.read_bytes()).hexdigest()
                def confirm(settings):
                    response=client.post(f'/api/jobs/{identifier}/confirm',json={'image_version':job['image_version'],'music_settings':settings},headers=headers)
                    self.assertEqual(response.status_code,200,response.json)
                    deadline=time.monotonic()+60
                    while app.ACTIVE and time.monotonic()<deadline:time.sleep(.05)
                    current=app.read_job(identifier)
                    self.assertEqual(current['video_state'],'done',current['video_message'])
                    return current
                first=confirm(dict(music.DEFAULTS, enabled=True))
                first_hash=hashlib.sha256(app.video_path(first).read_bytes()).hexdigest()
                confirm(dict(music.DEFAULTS, enabled=True))
                self.assertEqual(hashlib.sha256(app.video_path(first).read_bytes()).hexdigest(),first_hash)
                second=confirm({**music.DEFAULTS,'volume':45,'loop_seconds':300})
                self.assertNotEqual(hashlib.sha256(app.video_path(second).read_bytes()).hexdigest(),first_hash)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),audio_digest)
                self.assertEqual(second['rendered_music_settings']['volume'],45)
                confirm({**music.DEFAULTS,'enabled':False})
                self.assertLess(amplitude(app.video_path(second),880,.1),.0001)
                # 去掉音乐字段模拟旧任务，确认相同设置不会改写已完成的无配乐视频。
                current=app.read_job(identifier);current.pop('music_settings');current.pop('rendered_music_settings');app.save(current)
                with patch.object(app,'launch') as launch:
                    response=client.post(f'/api/jobs/{identifier}/confirm',json={'image_version':job['image_version']},headers=headers)
                    self.assertEqual(response.status_code,200)
                    launch.assert_not_called()
                with client.get(response.json['video_url']) as video:self.assertEqual(video.status_code,200)
                app.CONTROLS.pop(identifier,None)

    def test_frontend_script_syntax(self):
        html=app.app.test_client().get('/').get_data(as_text=True)
        script=re.search(r'<script>(.*?)</script>',html,re.S).group(1)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'ui.js';path.write_text(script)
            subprocess.run(['/opt/homebrew/bin/node','--check',str(path)],check=True)


class MusicPreferenceTests(unittest.TestCase):
    def test_ui_remembers_music_and_reenables_completed_video(self):
        from test_style_preferences import StylePreferenceTests
        StylePreferenceTests().run_ui(r'''
const first=load();
first.el('musicTrack').value='first-light';first.el('musicVolume').value='35';first.el('musicLoop').value='300';first.el('musicEnabled').checked=true;first.el('musicVolume').oninput();
const expected=JSON.parse(first.run('JSON.stringify(musicSettings())'));
first.run('clearWorkspaceUI()');assert.deepEqual(JSON.parse(first.run('JSON.stringify(musicSettings())')),expected);
const reopened=load();assert.deepEqual(JSON.parse(reopened.run('JSON.stringify(musicSettings())')),expected);
reopened.run("job={id:'old',audio_state:'ready',image_state:'ready',video_state:'done',title_style:'clean-yellow',title_settings:titleSettings(),subtitle_settings:subtitleSettings()};renderMusic(job);updateSubtitleButton()");
assert.equal(reopened.el('musicEnabled').checked,false);assert.equal(reopened.el('confirm').disabled,true);
reopened.el('musicEnabled').checked=true;reopened.el('musicVolume').value='15';reopened.el('musicEnabled').onchange();assert.equal(reopened.el('confirm').disabled,false);
reopened.run("job.rendered_music_settings=musicSettings();updateSubtitleButton()");assert.equal(reopened.el('confirm').disabled,true);
reopened.el('musicVolume').value='45';reopened.el('musicVolume').oninput();assert.equal(reopened.el('confirm').disabled,false);
reopened.run("job.video_state='working';renderMusic(job);updateSubtitleButton()");assert.equal(reopened.el('musicVolume').disabled,true);assert.equal(reopened.el('confirm').disabled,true);
''')

if __name__=='__main__':unittest.main()
