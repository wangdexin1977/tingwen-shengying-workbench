"""MP3 elapsed time measures the full attempt, survives refresh and resets on retry."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class AudioTimingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        jobs = root / 'jobs'
        jobs.mkdir()
        self.enterContext(patch.object(app, 'JOBS', jobs))
        self.enterContext(patch.object(app.records, 'LOG_FILE', root / 'events.jsonl'))
        self.client = app.app.test_client()
        self.headers = {'X-Local-Token': app.TOKEN}
        with patch.object(app, 'launch'):
            response = self.client.post('/api/jobs', json={'text': '声音克隆计时测试。', 'image_mode': 'none'}, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.json)
        self.identifier = response.json['id']

    def test_complete_attempt_is_saved_independent_of_audio_playback_length(self):
        app.update(self.identifier, audio_elapsed_seconds=999)

        def complete(job_id, _control):
            running = app.read_job(job_id)
            self.assertIsNone(running['audio_elapsed_seconds'])
            self.assertIsNone(running['audio_finished_at'])
            app.update(job_id, audio_state='ready', audio_duration=300)

        with patch.object(app, '_audio_worker_impl', side_effect=complete), patch.object(app.time, 'monotonic', side_effect=[100, 112.5]):
            app.audio_worker(self.identifier, app.Control())
        for _ in range(2):
            result = self.client.get('/api/jobs/' + self.identifier).json
            self.assertEqual(result['audio_elapsed_seconds'], 12.5)
            self.assertEqual(result['audio_duration'], 300)
            self.assertIsInstance(result['audio_finished_at'], float)

    def test_running_api_returns_elapsed_time_without_overwriting_saved_final_value(self):
        app.update(self.identifier, audio_state='working', audio_started_at=1000, audio_elapsed_seconds=None)
        with patch.object(app.time, 'time', return_value=1045.25):
            result = self.client.get('/api/jobs/' + self.identifier).json
        self.assertEqual(result['audio_elapsed_seconds'], 45.25)
        self.assertIsNone(app.read_job(self.identifier)['audio_elapsed_seconds'])

    def test_failed_and_cancelled_attempts_also_record_elapsed_time(self):
        for error in [RuntimeError('推理失败'), app.Cancelled()]:
            with self.subTest(error=type(error).__name__):
                with patch.object(app, '_audio_worker_impl', side_effect=error), patch.object(app.time, 'monotonic', side_effect=[100, 107.75]):
                    with self.assertRaises(type(error)):
                        app.audio_worker(self.identifier, app.Control())
                self.assertEqual(app.read_job(self.identifier)['audio_elapsed_seconds'], 7.75)

    def test_retry_clears_previous_attempt_time(self):
        app.update(self.identifier, audio_state='failed', audio_started_at=1000, audio_finished_at=1010, audio_elapsed_seconds=10)
        with patch.object(app, 'launch'):
            response = self.client.post('/api/jobs/' + self.identifier + '/retry-audio', json={}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertIsNone(response.json['audio_elapsed_seconds'])
        self.assertIsNone(response.json['audio_started_at'])
        self.assertIsNone(response.json['audio_finished_at'])


if __name__ == '__main__':
    unittest.main()
