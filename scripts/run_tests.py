"""回归使用独立队列目录，不读取或改变正在制作的用户队列。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import production_queue

MODULES = ['test_production_queue', 'test_preferences', 'test_integration', 'test_workbench',
           'test_local_photos', 'test_audio_import', 'test_background_music', 'test_subtitles',
           'test_poster_title', 'test_narration_title', 'test_speech_timeout', 'test_edge_compat',
           'test_style_preferences']

with tempfile.TemporaryDirectory(prefix='shengying-regression-') as directory:
    with patch.object(production_queue, 'ROOT', Path(directory) / 'queue'):
        suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
        result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
