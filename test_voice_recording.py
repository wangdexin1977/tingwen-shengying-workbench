"""浏览器录音容器的真实转换及命名保存交接。"""
import base64
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import app


class VoiceRecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = app.app.test_client()
        self.headers = {'X-Local-Token': app.TOKEN}
        app.ACTIVE.clear()

    def post(self, content, **changes):
        body = dict(name='我的录音音色', filename='录音.webm', audio_data=base64.b64encode(content).decode(), reference_text='这是我的声音。')
        return self.client.post('/api/omnivoice/voices', json={**body, **changes}, headers=self.headers)

    def test_webm_converts_to_real_wav_before_named_library_import(self):
        source = self.root / 'recording.webm'
        subprocess.run(['/opt/homebrew/bin/ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=1', '-c:a', 'libopus', str(source)], check=True)
        captured = {}
        def imported(request, directory):
            captured.update(request)
            with wave.open(request['file']) as audio:
                self.assertEqual(audio.getnchannels(), 1)
                self.assertEqual(audio.getframerate(), 24000)
                self.assertGreater(audio.getnframes(), 23000)
            return {'ok': True, 'created': True, 'voice_id': 'b' * 16}
        with patch.object(app.omnivoice_local, 'capabilities', return_value={'available': True, 'voices': []}), \
             patch.object(app.omnivoice_local, 'run', side_effect=imported):
            result = self.post(source.read_bytes())
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(captured['name'], '我的录音音色')
        self.assertEqual(captured['reference_text'], '这是我的声音。')
        self.assertEqual(Path(captured['file']).suffix, '.wav')

    def test_invalid_recording_is_rejected_before_library_write(self):
        with patch.object(app.omnivoice_local, 'capabilities', return_value={'available': True}), \
             patch.object(app.omnivoice_local, 'run') as imported:
            result = self.post(b'not audio')
        self.assertEqual(result.status_code, 400)
        self.assertIn('录音无法解码', result.json['error'])
        imported.assert_not_called()

    def test_missing_name_empty_audio_and_unauthorized_upload_rejected(self):
        with patch.object(app.omnivoice_local, 'capabilities', return_value={'available': True}), \
             patch.object(app.omnivoice_local, 'run') as imported:
            self.assertEqual(self.post(b'a', name='').status_code, 400)
            self.assertEqual(self.post(b'').status_code, 400)
            self.assertEqual(self.client.post('/api/omnivoice/voices', json={}).status_code, 403)
            imported.assert_not_called()


if __name__ == '__main__':
    unittest.main()
