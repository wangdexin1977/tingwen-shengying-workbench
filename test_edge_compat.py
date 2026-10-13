"""接口迁移及 HTTP 日期回归；不请求外网，不修改依赖目录。"""
import locale
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

from edge_tts import communicate, constants
from edge_tts.drm import DRM
from edge_compat import configure_edge_tts, parse_http_date


class EdgeCompatibilityTests(unittest.TestCase):
    def test_legacy_endpoint_migrates_once_and_voice_list_stays_unchanged(self):
        legacy='wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1?TrustedClientToken=test'
        voice_url=constants.VOICE_LIST
        date_parser=DRM.parse_rfc2616_date
        with patch.object(communicate,'WSS_URL',legacy),patch.object(constants,'WSS_URL',legacy),patch.object(DRM,'parse_rfc2616_date',date_parser):
            self.assertTrue(configure_edge_tts())
            url=urlsplit(communicate.WSS_URL)
            self.assertEqual(url.scheme,'wss');self.assertEqual(url.hostname,'api.msedgeservices.com')
            self.assertEqual(url.path,'/tts/cognitiveservices/websocket/v1')
            self.assertEqual(parse_qs(url.query)['Ocp-Apim-Subscription-Key'],[constants.TRUSTED_CLIENT_TOKEN])
            self.assertEqual(constants.WSS_URL,communicate.WSS_URL)
            self.assertEqual(constants.VOICE_LIST,voice_url)
            self.assertFalse(configure_edge_tts())
            self.assertEqual(DRM.parse_rfc2616_date('Mon, 12 Oct 2026 03:16:09 +0000'),1791774969.0)

    def test_date_parser_handles_locales_timezones_and_bad_headers(self):
        original=locale.setlocale(locale.LC_TIME)
        try:
            for language in ['C','fr_FR.UTF-8','de_DE.UTF-8']:
                try: locale.setlocale(locale.LC_TIME,language)
                except locale.Error: continue
                with self.subTest(language=language):
                    self.assertEqual(parse_http_date('Mon, 12 Oct 2026 03:16:09 GMT'),1791774969.0)
                    self.assertEqual(parse_http_date('Mon, 12 Oct 2026 03:16:09 +0000'),1791774969.0)
                    self.assertEqual(parse_http_date('Mon, 12 Oct 2026 05:16:09 +0200'),1791774969.0)
                    self.assertEqual(parse_http_date('Mon, 12 Oct 2026 03:16:09'),1791774969.0)
        finally: locale.setlocale(locale.LC_TIME,original)
        for value in ['',None,'not a date',123]:
            with self.subTest(value=value):self.assertIsNone(parse_http_date(value))

    def test_new_upstream_endpoint_is_not_overridden(self):
        future='wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1?future=1'
        parser=DRM.parse_rfc2616_date
        with patch.object(communicate,'WSS_URL',future):
            self.assertFalse(configure_edge_tts())
            self.assertEqual(communicate.WSS_URL,future)
            self.assertIs(DRM.parse_rfc2616_date,parser)


if __name__=='__main__':unittest.main()
