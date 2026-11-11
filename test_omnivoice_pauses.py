"""OmniVoice 自然分句、参考语速和停顿拼接；用 TTS 环境运行。"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent / 'tts'))
from mlx_indextts.generate_omnivoice import OmniVoiceTTS, SAMPLE_RATE


def tone(seconds):
    return (.1 * np.sin(np.arange(round(seconds * SAMPLE_RATE)) * 2 * np.pi * 180 / SAMPLE_RATE)).astype(np.float32)


class OmniVoicePauseTests(unittest.TestCase):
    def test_packs_complete_sentences_before_splitting_a_clause(self):
        first = '这是一段完整说明。'
        second = '后面这一句话还没有结束，我们需要把前因后果交代清楚，然后再自然地结束。'
        pieces = OmniVoiceTTS.split_text(first + second, 40)
        self.assertEqual(pieces, [first, second])
        self.assertEqual(''.join(pieces), first + second)

    def test_long_sentence_stays_bounded_and_preserves_every_character(self):
        text = '我们先解释背景，' * 70 + '最后得出结论。'
        pieces = OmniVoiceTTS.split_text(text, 120)
        self.assertTrue(all(len(piece) <= 120 for piece in pieces))
        self.assertEqual(''.join(pieces), text)
        self.assertTrue(all(any(char.isalnum() for char in piece) for piece in pieces))

    def test_join_tops_up_silence_instead_of_adding_a_second_pause(self):
        left = np.r_[tone(.2), np.zeros(round(.15*SAMPLE_RATE), np.float32)]
        right = np.r_[np.zeros(round(.1*SAMPLE_RATE), np.float32), tone(.2)]
        result = OmniVoiceTTS._join_audio([left, right], 250)
        self.assertLessEqual(abs(len(result) - len(left) - len(right)), round(.02*SAMPLE_RATE))
        no_padding = OmniVoiceTTS._join_audio([tone(.2), tone(.2)], 250)
        self.assertAlmostEqual(len(no_padding)/SAMPLE_RATE, .65, delta=.02)

    def test_postprocess_removes_only_abnormally_long_silence(self):
        long = np.r_[tone(.5), np.zeros(2*SAMPLE_RATE, np.float32), tone(.5)]
        cleaned = OmniVoiceTTS._postprocess_audio(long)
        self.assertLess(len(cleaned)/SAMPLE_RATE, 2.1)
        natural = np.r_[tone(.5), np.zeros(round(.3*SAMPLE_RATE), np.float32), tone(.5)]
        kept = OmniVoiceTTS._postprocess_audio(natural)
        self.assertAlmostEqual(len(kept)/SAMPLE_RATE, 1.3, delta=.02)

    def test_clone_duration_uses_exact_reference_tokens_and_text(self):
        calls = []
        adapter = OmniVoiceTTS.__new__(OmniVoiceTTS)
        adapter.runtime = SimpleNamespace(generate=lambda **kwargs: calls.append(kwargs) or [SimpleNamespace(audio=tone(.5))])
        reference = '这是一段参考人声。'
        tokens = np.zeros((100, 8), np.int32)
        adapter._prepare_reference_prompt = Mock(return_value=(tokens, reference))
        with tempfile.TemporaryDirectory() as directory:
            adapter.generate(text=reference, reference_audio='reference.wav', output_path=str(Path(directory)/'result.wav'))
        self.assertAlmostEqual(calls[0]['duration_s'], 4.0, places=3)
        self.assertEqual(calls[0]['ref_text'], reference)
        self.assertIs(calls[0]['ref_tokens'], tokens)
        self.assertEqual(calls[0]['num_steps'], 32)

    def test_outer_batches_use_the_same_minimum_gap_without_affecting_other_models(self):
        from mlx_indextts.webui import _concatenate_wav_batches
        left = np.r_[tone(.2), np.zeros(round(.15*SAMPLE_RATE), np.float32)]
        right = np.r_[np.zeros(round(.1*SAMPLE_RATE), np.float32), tone(.2)]
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory)/name for name in ['left.wav', 'right.wav']]
            sf.write(paths[0], left, SAMPLE_RATE);sf.write(paths[1], right, SAMPLE_RATE)
            result = Path(directory)/'joined.wav'
            _concatenate_wav_batches(paths, result, 250, gap_is_minimum=True)
            self.assertAlmostEqual(sf.info(result).duration, .65, delta=.02)
            _concatenate_wav_batches(paths, result, 250)
            self.assertAlmostEqual(sf.info(result).duration, .9, delta=.02)


if __name__ == '__main__':
    unittest.main()
