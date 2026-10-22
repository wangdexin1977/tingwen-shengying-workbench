"""Fish parallel decoding safety; run with tts/.venv/bin/python."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import mlx.core as mx
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent / 'tts'))
from mlx_indextts.generate_fish_s2 import FishS2ProTTS
from mlx_indextts.generate_v2 import GenerationCancelled


def adapter_for(runtime):
    adapter = FishS2ProTTS.__new__(FishS2ProTTS)
    adapter.runtime = runtime
    adapter.sample_rate = 44100
    adapter.cache = {}
    adapter.wired_memory_limit = 18 * 1024 ** 3
    adapter.split_text = lambda *_: ['第一段。', '第二段。']
    return adapter


def result(index, token_count=20):
    return SimpleNamespace(sequence_idx=index, token_count=token_count,
                           audio=mx.full((4410,), (index + 1) / 10))


class FishBatchTests(unittest.TestCase):
    def test_batch_size_obeys_device_memory_padded_text_and_speed(self):
        adapter = adapter_for(SimpleNamespace(batch_generate=lambda **_: []))
        self.assertEqual([len(b) for b in adapter._generation_batches(['字' * 38] * 4, 1)], [4])
        self.assertEqual([len(b) for b in adapter._generation_batches(['字' * 60] * 4, 1)], [2, 2])
        self.assertEqual([len(b) for b in adapter._generation_batches(['字' * 38] * 4, .5)], [2, 2])
        adapter.wired_memory_limit = 11 * 1024 ** 3
        self.assertEqual([len(b) for b in adapter._generation_batches(['字'] * 4, 1)], [2, 2])
        adapter.wired_memory_limit = 0
        self.assertEqual([len(b) for b in adapter._generation_batches(['字'] * 4, 1)], [1, 1, 1, 1])

    def test_parallel_results_keep_text_order_and_sampling_parameters(self):
        calls = []

        def generate(**kwargs):
            calls.append(kwargs)
            return [result(1), result(0)]

        adapter = adapter_for(SimpleNamespace(batch_generate=generate))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'audio.wav'
            audio = adapter.generate(text='两段文稿', reference_audio=None, fish_mode='auto', output_path=str(target))
            self.assertAlmostEqual(float(audio[100]), .1)
            self.assertAlmostEqual(float(audio[-100]), .2)
            self.assertAlmostEqual(sf.info(target).duration, .45, places=3)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['texts'], ['第一段。', '第二段。'])
        self.assertEqual((calls[0]['temperature'], calls[0]['top_p'], calls[0]['top_k']), (.7, .7, 30))

    def test_capped_batch_is_retried_in_full_before_saving(self):
        limits = []

        def generate(**kwargs):
            limits.append(kwargs['max_tokens'])
            return [result(0, kwargs['max_tokens'] if len(limits) == 1 else 20), result(1)]

        adapter = adapter_for(SimpleNamespace(batch_generate=generate))
        with tempfile.TemporaryDirectory() as directory:
            adapter.generate(text='两段文稿', reference_audio=None, fish_mode='auto', output_path=str(Path(directory) / 'audio.wav'))
        self.assertEqual(limits, [1024, 2048])

    def test_cancel_after_first_saved_piece_keeps_partial_audio(self):
        adapter = adapter_for(SimpleNamespace(batch_generate=lambda **_: [result(0), result(1)]))
        cancelled = [False]

        def progress(current, *_):
            if current == 1:
                cancelled[0] = True

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'audio.wav'
            with self.assertRaises(GenerationCancelled):
                adapter.generate(text='两段文稿', reference_audio=None, fish_mode='auto', output_path=str(target),
                                 progress_callback=progress, cancel_requested=lambda: cancelled[0])
            self.assertFalse(target.exists())
            self.assertAlmostEqual(sf.info(target.with_name('audio.partial.wav')).duration, .1, places=3)

    def test_codec_decodes_one_waveform_at_a_time_and_restores_runtime(self):
        decoded = []

        class Runtime:
            def _decode_codes(self, codes):
                decoded.append(codes)
                return mx.ones(10)

            def _decode_codes_batch(self, _):
                raise AssertionError('Padded batch codec should not run')

            def batch_generate(self, **_):
                return self._decode_codes_batch([1, 2])

        runtime = Runtime()
        original = runtime._decode_codes_batch
        adapter = adapter_for(runtime)
        self.assertEqual(len(adapter._generate_results(batch_texts=['甲', '乙'])), 2)
        self.assertEqual(decoded, [1, 2])
        self.assertEqual(runtime._decode_codes_batch, original)

    def test_token_level_cancellation_restores_runtime(self):
        class Runtime:
            def _sample_semantic_batch(self, **_):
                return mx.array([1, 2])

            def batch_generate(self, **_):
                self._sample_semantic_batch()
                raise AssertionError('Cancellation should stop decoding')

        runtime = Runtime()
        original = runtime._sample_semantic_batch
        adapter = adapter_for(runtime)
        checks = [0]

        def check():
            checks[0] += 1
            raise GenerationCancelled('已取消')

        with self.assertRaises(GenerationCancelled):
            adapter._generate_results(batch_texts=['甲', '乙'], control_check=check)
        self.assertEqual(checks[0], 1)
        self.assertEqual(runtime._sample_semantic_batch, original)


if __name__ == '__main__':
    unittest.main()
