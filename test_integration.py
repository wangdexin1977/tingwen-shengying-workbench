"""融合流程、命名、文件入口与发布交接的离线回归。"""
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import app
import integration
import omnivoice_local


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(app, 'JOBS', self.root)
        self.patch.start()
        self.client = app.app.test_client()
        self.headers = {'X-Local-Token': app.TOKEN}
        app.ACTIVE.clear()
        app.CONTROLS.clear()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def post(self, path, data):
        return self.client.post(path, json=data, headers=self.headers)

    def create(self, **changes):
        with patch.object(app, 'launch') as launch:
            response = self.post('/api/jobs', {'text': '甲乙丙丁' * 12, 'image_mode': 'none', 'title_enabled': True, **changes})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json, launch.call_args_list

    def test_audio_only_is_optional_and_can_continue_into_video(self):
        job, calls = self.create()
        self.assertEqual([call.args[1] for call in calls], ['audio'])
        self.assertEqual(job['image_state'], 'idle')
        raw = io.BytesIO()
        Image.new('RGB', (20, 20), 'blue').save(raw, format='PNG')
        response = self.post('/api/jobs/' + job['id'] + '/use-image',
                             {'image_mode': 'upload', 'photos': [{'photo_name': 'cover.png', 'photo_data': base64.b64encode(raw.getvalue()).decode()}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['image_state'], 'ready')

    def test_titles_and_real_audio_video_names_use_same_30_characters(self):
        job, _ = self.create()
        prefix = ('甲乙丙丁' * 12)[:30]
        self.assertEqual(job['title'], prefix)
        self.assertEqual(job['audio_filename'], prefix + '.mp3')
        self.assertEqual(job['video_filename'], prefix + '.mp4')
        self.assertEqual(app.audio_path(job).name, prefix + '.mp3')
        self.assertEqual(app.media_filename('短文', 'mp3'), '短文.mp3')
        self.assertNotIn('/', app.media_filename('标题/非法:字符', 'mp4'))

    def test_explicit_title_and_no_title_remain_available(self):
        custom, _ = self.create(title='自定义标题')
        off, _ = self.create(title_enabled=False)
        self.assertEqual(custom['title'], '自定义标题')
        self.assertEqual(off['title'], '')
        self.assertEqual(custom['audio_filename'], off['audio_filename'])
        self.assertNotEqual(app.audio_path(custom), app.audio_path(off))

    def test_audio_library_exposes_actual_named_file_and_download(self):
        job, _ = self.create()
        app.audio_path(job).write_bytes(b'test-mp3')
        app.update(job['id'], audio_state='ready')
        products = self.client.get('/api/products/audio').json['products']
        self.assertEqual(products[0]['path'], str(app.audio_path(job)))
        response = self.client.get(products[0]['url'] + '?download=1')
        self.assertEqual(response.status_code, 200)
        self.assertIn('.mp3', response.headers['Content-Disposition'])
        response.close()

    def test_publish_passes_exact_video_to_uploader_and_rejects_outside_files(self):
        job, _ = self.create()
        video = app.video_path(job)
        video.write_bytes(b'test-mp4')
        with patch.object(integration.toutiao_publish, 'start', return_value={'id': 'pending', 'state': 'starting'}) as upload:
            result = self.post('/api/videos/' + job['id'] + '/publish-toutiao', {'filename': video.name})
        self.assertEqual(result.status_code, 202)
        upload.assert_called_once_with(video.resolve(), job['text'][:30])
        with patch.object(integration.toutiao_publish, 'start') as upload:
            invalid = self.post('/api/videos/' + job['id'] + '/publish-toutiao', {'filename': '../outside.mp4'})
            self.assertEqual(invalid.status_code, 404)
            upload.assert_not_called()

    def test_publish_component_errors_and_session_guard(self):
        job, _ = self.create()
        video = app.video_path(job)
        video.write_bytes(b'test-mp4')
        with patch.object(integration.toutiao_publish, 'start', side_effect=RuntimeError('发布组件未安装')) as upload:
            endpoint = '/api/videos/' + job['id'] + '/publish-toutiao'
            self.assertEqual(self.client.post(endpoint, json={'filename': video.name}).status_code, 403)
            upload.assert_not_called()
            result = self.post(endpoint, {'filename': video.name})
            self.assertEqual(result.status_code, 409)
            self.assertIn('未安装', result.json['error'])

    def test_publish_status_endpoint_handles_missing_requests(self):
        with patch.object(integration.toutiao_publish, 'status', return_value={'state': 'waiting_login'}) as status:
            result = self.client.get('/api/toutiao-publish/request')
            self.assertEqual(result.json['state'], 'waiting_login')
            status.assert_called_once_with('request')
        self.assertEqual(self.client.get('/api/toutiao-publish/not-a-request').status_code, 404)

    def test_original_roots_are_not_used_for_mutable_state(self):
        self.assertEqual(omnivoice_local.project_root(), app.ROOT / 'tts')
        self.assertNotEqual(omnivoice_local.project_root().resolve(), Path('/Users/wdx/Documents/创意项目/mlx-indextts'))
        self.assertFalse((omnivoice_local.project_root() / 'outputs').is_symlink())
        self.assertFalse((omnivoice_local.project_root() / 'outputs/webui/voices').is_symlink())

    def test_model_parameters_reject_nonfinite_and_unknown_values(self):
        entry = {'id': 'a' * 16, 'name': 'test'}
        with patch.object(omnivoice_local, 'library_entry', return_value=entry), patch.object(omnivoice_local, 'capabilities', return_value={'available': True}):
            for parameters in [{'speed': float('nan')}, {'speed': 0}, {'shell': 'bad'}]:
                with self.assertRaises(ValueError):
                    omnivoice_local.settings({'voice_id': entry['id'], 'parameters': parameters})
            settings = omnivoice_local.settings({'voice_id': entry['id'], 'backend': 'IndexTTS 2.5', 'parameters': {'speed': 1.2}})
            self.assertEqual(settings['backend'], 'IndexTTS 2.5')
            self.assertEqual(settings['parameters']['speed'], 1.2)

    def test_expert_registry_retains_custom_output_directory(self):
        tts = self.root / 'tts'
        output = tts / 'outputs/webui'
        output.mkdir(parents=True)
        audio = self.root / '自选目录' / '作品.mp3'
        audio.parent.mkdir()
        audio.write_bytes(b'custom-output')
        (output / 'products.jsonl').write_text(json.dumps({'path': str(audio)}) + '\n')
        with patch.object(integration, 'TTS', tts):
            result = self.client.get('/api/expert/outputs').json['outputs']
            self.assertEqual(result[0]['path'], str(audio.resolve()))
            response = self.client.get(result[0]['url'])
            self.assertEqual(response.status_code, 200)
            response.close()


if __name__ == '__main__':
    unittest.main()
