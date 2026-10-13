import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import preferences
import background_music


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'settings/preferences.json'
        self.patch = patch.object(preferences, 'PATH', self.path)
        self.patch.start()
        self.client = app.app.test_client()
        self.headers = {'X-Local-Token': app.TOKEN}

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_first_use_defaults_off_and_no_configuration(self):
        self.assertIsNone(self.client.get('/api/preferences').json['preferences'])
        html = self.client.get('/').get_data(as_text=True)
        for identifier in ['showTitle', 'musicEnabled']:
            tag = re.search(r'<input id="' + identifier + r'"[^>]*>', html).group()
            self.assertNotIn('checked', tag)
        self.assertFalse(background_music.DEFAULTS['enabled'])

    def test_settings_persist_across_clients_and_reopening(self):
        value = {'controls': {'showTitle': False, 'musicEnabled': False, 'audioOnly': True,
                             'localBackend': 'IndexTTS 2.5', 'audioProvider': 'omnivoice',
                             'drawthingsPort': 7890, 'model': 'model-choice', 'musicVolume': 22,
                             'titleColor': '#FFCC33', 'subtitlePosition': 75},
                 'mode': 'long', 'speech_profiles': {'IndexTTS 2.5': {'speed': 1.2, 'seed': 17}}}
        result = self.client.post('/api/preferences', json=value, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(json.loads(self.path.read_text()), value)
        other_browser = app.app.test_client()
        self.assertEqual(other_browser.get('/api/preferences').json['preferences'], value)
        value['controls']['showTitle'] = True
        value['controls']['musicEnabled'] = True
        self.client.post('/api/preferences', json=value, headers=self.headers)
        self.assertEqual(other_browser.get('/api/preferences').json['preferences'], value)
        self.assertFalse(self.path.with_suffix('.pending').exists())

    def test_credentials_drafts_payment_and_invalid_settings_are_rejected(self):
        for field in ['api_key', 'text', 'payment_consent', 'filename']:
            result = self.client.post('/api/preferences', json={'controls': {field: 'do-not-store'}}, headers=self.headers)
            self.assertEqual(result.status_code, 400)
        for value in [{'controls': {'showTitle': 'yes'}}, {'controls': {'speechSpeed': float('nan')}},
                      {'controls': {'drawthingsPort': 1}}, {'mode': 'invalid'},
                      {'speech_profiles': {'OmniVoice': {'unsupported': 3}}}]:
            self.assertEqual(self.client.post('/api/preferences', json=value, headers=self.headers).status_code, 400)
        self.assertFalse(self.path.exists())

    def test_unauthorized_writes_do_not_change_file(self):
        response = self.client.post('/api/preferences', json={'controls': {'musicEnabled': True}})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.path.exists())

    def test_corrupt_file_preserved_and_reported(self):
        self.path.parent.mkdir()
        self.path.write_text('broken-json')
        result = self.client.get('/api/preferences')
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.json['preferences'])
        self.assertIn('warning', result.json)
        self.assertEqual(self.path.read_text(), 'broken-json')


if __name__ == '__main__':
    unittest.main()
