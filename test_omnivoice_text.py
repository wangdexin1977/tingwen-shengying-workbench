"""Run with tts/.venv/bin/python: real text normalization, without loading TTS weights."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / 'tts'))
from mlx_indextts.omnivoice_text import normalize_omnivoice_text
from mlx_indextts.generate_omnivoice import OmniVoiceTTS


class OmniVoiceTextTests(unittest.TestCase):
    def test_sm_identifiers_and_numeric_list_have_unambiguous_spoken_forms(self):
        result = normalize_omnivoice_text('SM1、SM 2、sm-3、ＳＭ４。数字1,2,3,4,5。')
        self.assertEqual(result, '艾斯艾姆一、艾斯艾姆二、艾斯艾姆三、艾斯艾姆四。数字一,二,三,四,五。')

    def test_dates_quantities_decimals_and_percentages_keep_their_values(self):
        result = normalize_omnivoice_text('2026年10月20日，128位，3.14，50%，-2.5。')
        self.assertEqual(result, '二零二六年十月二十日,一百二十八位,三点一四,百分之五十,负二点五。')

    def test_unrelated_model_names_are_not_reinterpreted_as_negative_numbers(self):
        text = 'F-35和GPT-4o、AES128、C919不改变。'
        self.assertEqual(normalize_omnivoice_text(text), text)

    def test_english_and_nondigit_text_are_unchanged(self):
        english = 'SM1 and SM2 use different algorithms. Count 1, 2, 3.'
        self.assertEqual(normalize_omnivoice_text(english, 'english'), english)
        self.assertEqual(normalize_omnivoice_text(english, 'None'), english)
        chinese = '第一段：保留中文、引号“原文”和自然语气。'
        self.assertEqual(normalize_omnivoice_text(chinese), chinese)
        self.assertEqual(normalize_omnivoice_text(''), '')

    def test_auto_chinese_and_repeated_normalization(self):
        result = normalize_omnivoice_text('算法SM1有128位密钥。', 'None')
        self.assertEqual(result, '算法艾斯艾姆一有一百二十八位密钥。')
        self.assertEqual(normalize_omnivoice_text(result), result)

    def test_inference_uses_spoken_copy_but_keeps_reference_and_sampling_controls(self):
        calls = []
        adapter = OmniVoiceTTS.__new__(OmniVoiceTTS)
        waveform = np.sin(np.linspace(0, 20, 2400)).astype(np.float32) * .1
        adapter.runtime = SimpleNamespace(generate=lambda **kwargs: calls.append(kwargs) or [SimpleNamespace(audio=waveform)])
        reference_text = '参考原文中有SM1和数字2。'
        adapter._prepare_reference_prompt = Mock(return_value=('reference-tokens', reference_text))
        original = '算法SM1、SM2，数字1、2、3、4、5。'
        with tempfile.TemporaryDirectory() as folder:
            adapter.generate(text=original, reference_audio='reference.wav', output_path=str(Path(folder) / 'test.wav'),
                             ref_text=reference_text, speed=1.1, seed=42, num_steps=32, guidance_scale=2.)
        spoken = normalize_omnivoice_text(original)
        self.assertEqual(calls[0]['text'], spoken)
        self.assertEqual(calls[0]['duration_s'], adapter._estimated_duration(spoken, 1.1))
        self.assertEqual(calls[0]['ref_text'], reference_text)
        self.assertEqual(calls[0]['ref_tokens'], 'reference-tokens')
        self.assertEqual(calls[0]['num_steps'], 32)
        self.assertEqual(calls[0]['guidance_scale'], 2.)
        adapter._prepare_reference_prompt.assert_called_once_with('reference.wav', reference_text, 10.)
        self.assertEqual(original, '算法SM1、SM2，数字1、2、3、4、5。')


if __name__ == '__main__':
    unittest.main()
