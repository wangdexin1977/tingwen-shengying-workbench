import base64
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import app
import production_queue


class ProductionQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'jobs').mkdir()
        self.patches = [patch.object(production_queue, 'ROOT', self.root / 'queue'),
                        patch.object(app, 'JOBS', self.root / 'jobs'),
                        patch.object(app.records, 'LOG_FILE', self.root / 'events.jsonl'),
                        patch.object(app.production_queue, 'awake')]
        for item in self.patches:
            item.start()
        self.manager = app.production_queue
        self.manager.thread = None
        app.ACTIVE.clear()
        app.CONTROLS.clear()
        self.client = app.app.test_client()
        self.headers = {'X-Local-Token': app.TOKEN}
        buffer = io.BytesIO()
        Image.new('RGB', (32, 20), 'blue').save(buffer, 'PNG')
        self.photos = [{'photo_name': 'cover.png', 'photo_data': base64.b64encode(buffer.getvalue()).decode()}]

    def tearDown(self):
        if self.manager.thread:
            self.manager.set_state(running=False)
            self.manager.thread.join(timeout=5)
        deadline = time.time() + 5
        while app.ACTIVE and time.time() < deadline:
            time.sleep(.02)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def post(self, path, data):
        return self.client.post(path, json=data, headers=self.headers)

    def save(self, texts, **changes):
        result = self.post('/api/queue/projects', {'projects': [{'text': text} for text in texts],
            'settings': {'title_enabled': False, 'audio_provider': 'edge-tts',
                         'music_settings': {'enabled': False}, 'subtitle_settings': {'enabled': False}},
            'photos': self.photos, **changes})
        self.assertEqual(result.status_code, 201, result.json)
        return result.json['ids']

    def workers(self, calls, failures=()):
        def audio(identifier, control):
            job = app.read_job(identifier)
            calls.append(('audio', job['text']))
            if job['text'] in failures:
                raise RuntimeError('预设失败项目')
            app.audio_path(job).write_bytes(b'completed-audio')
            app.update(identifier, audio_state='ready', audio_duration=3)
        def video(identifier, control):
            job = app.read_job(identifier)
            calls.append(('video', job['text']))
            app.video_path(job).write_bytes(b'completed-video')
            app.update(identifier, video_state='done')
        return patch.dict(app.WORKERS, {'audio': audio, 'video': video})

    def finish(self):
        self.manager.thread.join(timeout=10)
        self.assertFalse(self.manager.thread.is_alive())

    def test_saved_projects_freeze_text_images_settings_and_order(self):
        identifiers = self.save(['第一章正文', '第二章正文'])
        first = self.manager.project(identifiers[0])
        self.assertEqual(first['text'], '第一章正文')
        self.assertFalse(first['settings']['music_settings']['enabled'])
        self.assertTrue((self.manager.folder(first['id']) / first['images'][0]['filename']).is_file())
        self.assertFalse(first['job_id'])
        other_client = app.app.test_client()
        self.assertEqual([p['id'] for p in other_client.get('/api/queue').json['projects']], identifiers)
        result = self.post('/api/queue/reorder', {'order': identifiers[::-1]})
        self.assertEqual(result.json['order'], identifiers[::-1])

    def test_one_click_runs_each_audio_then_video_in_saved_order(self):
        ids = self.save(['第一章', '第二章'])
        calls = []
        with self.workers(calls):
            self.assertEqual(self.post('/api/queue/start', {'output': 'mp4'}).status_code, 200)
            self.finish()
        self.assertEqual(calls, [('audio', '第一章'), ('video', '第一章'), ('audio', '第二章'), ('video', '第二章')])
        self.assertEqual([self.manager.project(identifier)['state'] for identifier in ids], ['done', 'done'])
        self.assertNotEqual(self.manager.project(ids[0])['job_id'], self.manager.project(ids[1])['job_id'])
        self.assertEqual(self.post('/api/queue/start', {'output': 'mp4'}).status_code, 409)

    def test_mp3_stage_then_mp4_reuses_audio(self):
        ids = self.save(['分步制作'])
        calls = []
        with self.workers(calls):
            self.post('/api/queue/start', {'output': 'mp3'})
            self.finish()
            project = self.manager.project(ids[0])
            self.assertEqual(project['state'], 'audio_ready')
            audio = app.audio_path(app.read_job(project['job_id']))
            original = audio.read_bytes()
            self.post('/api/queue/start', {'output': 'mp4'})
            self.finish()
        self.assertEqual(calls, [('audio', '分步制作'), ('video', '分步制作')])
        self.assertEqual(audio.read_bytes(), original)

    def test_failure_preserved_and_next_project_completes(self):
        ids = self.save(['失败项目', '继续下一项'])
        calls = []
        with self.workers(calls, {'失败项目'}):
            self.post('/api/queue/start', {'output': 'mp4'})
            self.finish()
        self.assertEqual(self.manager.project(ids[0])['state'], 'failed')
        self.assertIn('预设失败', self.manager.project(ids[0])['error'])
        self.assertEqual(self.manager.project(ids[1])['state'], 'done')
        self.assertEqual(calls, [('audio', '失败项目'), ('audio', '继续下一项'), ('video', '继续下一项')])

    def test_scheduled_time_persists_and_cancel_keeps_projects(self):
        ids = self.save(['夜间项目'])
        when = time.time() + 3600
        result = self.post('/api/queue/schedule', {'scheduled_at': when, 'output': 'mp4'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(app.app.test_client().get('/api/queue').json['scheduled_at'], when)
        cancelled = self.post('/api/queue/cancel-schedule', {})
        self.assertIsNone(cancelled.json['scheduled_at'])
        self.assertEqual(cancelled.json['order'], ids)
        self.assertEqual(self.post('/api/queue/schedule', {'scheduled_at': time.time() - 1}).status_code, 400)

    def test_missing_picture_rejected_for_mp4_but_allowed_for_audio_draft(self):
        payload = {'projects': [{'text': '正文'}], 'settings': {'audio_provider': 'edge-tts'}}
        self.assertEqual(self.post('/api/queue/projects', payload).status_code, 400)
        saved = self.post('/api/queue/projects', {**payload, 'audio_only': True})
        self.assertEqual(saved.status_code, 201)
        self.assertEqual(saved.json['queue']['projects'][0]['images'], 0)

    def test_only_requested_current_project_runs_and_removal_keeps_assets(self):
        ids = self.save(['等待以后', '当前一键项目'])
        calls = []
        with self.workers(calls):
            self.post('/api/queue/start', {'output': 'mp4', 'ids': [ids[1]]})
            self.finish()
        self.assertEqual(self.manager.project(ids[0])['state'], 'waiting')
        self.assertEqual(calls, [('audio', '当前一键项目'), ('video', '当前一键项目')])
        folder = self.manager.folder(ids[0])
        removed = self.post('/api/queue/remove', {'id': ids[0]})
        self.assertNotIn(ids[0], removed.json['order'])
        self.assertTrue((folder / 'project.json').exists())

    def test_engine_profile_is_frozen_when_project_is_saved(self):
        tts = self.root / 'tts'
        config = tts / 'outputs/webui/user_settings.json'
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({'model_profiles': {'OmniVoice': {'omnivoice_num_steps': 16, 'seed': 77}}}))
        voice = {'id': 'a' * 16, 'name': '测试音色'}
        with patch.object(app.omnivoice_local, 'project_root', return_value=tts), \
             patch.object(app.omnivoice_local, 'library_entry', return_value=voice), \
             patch.object(app.omnivoice_local, 'capabilities', return_value={'available': True}):
            ids = self.save(['保存参数'], settings={'audio_provider': 'omnivoice',
                'omnivoice_settings': {'voice_id': voice['id'], 'backend': 'OmniVoice'}})
        config.write_text(json.dumps({'model_profiles': {'OmniVoice': {'omnivoice_num_steps': 32, 'seed': 88}}}))
        snapshot = self.manager.project(ids[0])['settings']['omnivoice_settings']['profile_snapshot']
        self.assertEqual(snapshot, {'omnivoice_num_steps': 16, 'seed': 77})

    def test_pause_resume_stop_and_queue_lock_preserve_projects(self):
        ids = self.save(['暂停保留的项目'])
        self.manager.set_state(running=True)
        blocked = self.post('/api/jobs', {'text': '其他独立稿件', 'image_mode': 'none'})
        self.assertEqual(blocked.status_code, 409)
        self.assertTrue(self.post('/api/queue/pause', {}).json['paused'])
        self.assertFalse(self.post('/api/queue/resume', {}).json['paused'])
        stopped = self.post('/api/queue/stop', {})
        self.assertFalse(stopped.json['running'])
        self.assertEqual(stopped.json['order'], ids)
        self.assertTrue((self.manager.folder(ids[0]) / 'project.json').exists())


if __name__ == '__main__':
    unittest.main()
