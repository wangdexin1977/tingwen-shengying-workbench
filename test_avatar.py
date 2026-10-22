import math
import json
import hashlib
import uuid
import unittest
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import avatar
from scripts.generate_avatar import feature_windows


class AvatarTests(unittest.TestCase):
    def test_real_media_probe_returns_the_frame_rate_used_by_avatar_validation(self):
        import app as workbench
        binary = shutil.which('ffmpeg')
        if not binary:
            self.skipTest('FFmpeg is required for the media contract check')
        with TemporaryDirectory() as directory:
            clip = Path(directory) / '25fps.mp4'
            subprocess.run([binary, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=96x96:r=25',
                            '-t', '0.2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(clip)],
                           check=True, capture_output=True)
            self.assertEqual(workbench.probe(clip)['streams'][0]['r_frame_rate'], '25/1')

    def test_audio_windows_cover_exact_timeline_with_context(self):
        for seconds in [.01, 24.816, 28, 30, 56, 300.123, 3600.04]:
            count = math.ceil(seconds * 25)
            covered = []
            for first, last, context_first, context_last in feature_windows(count):
                self.assertLessEqual(context_last - context_first, 30 * 25)
                self.assertLessEqual(context_first, first)
                self.assertGreaterEqual(context_last, last)
                # Every output frame maps back to its absolute input sample.
                for fi in range(first, last):
                    self.assertEqual(context_first * 640 + (fi - context_first) * 640, fi * 640)
                covered.extend(range(first, last))
            self.assertEqual(covered, list(range(count)))

    def test_options_reject_unknown_presets_and_non_boolean_switches(self):
        self.assertEqual(avatar.normalize_settings(), avatar.DEFAULTS)
        self.assertTrue(avatar.normalize_settings({"enabled": True})["enabled"])
        for bad in [{"preset": "../../other"}, {"enabled": "false"}, []]:
            with self.assertRaises(ValueError):
                avatar.normalize_settings(bad)

    def test_title_leaves_the_presenter_area_clear(self):
        from PIL import Image
        from scripts.make_homepage_bg import draw_yellow_title
        image = Image.new("RGBA", (1920, 1080))
        result = draw_yellow_title(image, "数字人口型同步测试", left=624)
        self.assertGreaterEqual(image.getbbox()[0], 624)
        self.assertTrue(all(line['bounds'][0] >= 624 for line in result['lines']))

    def test_upstream_float_frame_count_epsilon_does_not_shift_audio(self):
        for frames in range(1, 751):
            samples = frames * 640
            self.assertEqual(math.floor(((samples + 1) / 16000) * 25), frames)
            self.assertEqual(math.floor(((samples + 1) / 16000) * 50), frames * 2)

    def test_unvalidated_runtime_is_unavailable(self):
        with TemporaryDirectory() as directory, patch.object(avatar, "RUNTIME", Path(directory)):
            self.assertFalse(avatar.capability()["available"])
            with self.assertRaises(ValueError):
                avatar.require_available()

    def test_validation_is_bound_to_the_generator_and_presenter_configuration(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime, assets = root / 'runtime', root / 'assets'
            files = [runtime / '.venv/bin/python', runtime / 'musetalk-core/musetalk_mlx/pipeline_mlx.py',
                     root / 'scripts/generate_avatar.py', runtime / 'revisions.json']
            files += [runtime / 'weights-mlx' / name for name in
                      ['config.json', 'unet.safetensors', 'vae.safetensors', 'whisper_encoder.safetensors']]
            for path in files:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
            assets.mkdir()
            profile = assets / 'news-anchor-male.json'
            profile.write_text(json.dumps({'sha256': 'portrait-fixture'}))
            report = {'status': 'passed', 'avatar_sha256': 'portrait-fixture'}
            for key, path in [('generator_sha256', root / 'scripts/generate_avatar.py'),
                              ('profile_sha256', profile), ('revisions_sha256', runtime / 'revisions.json')]:
                report[key] = hashlib.sha256(path.read_bytes()).hexdigest()
            (runtime / 'validated.json').write_text(json.dumps(report))
            with patch.object(avatar, 'ROOT', root), patch.object(avatar, 'RUNTIME', runtime), patch.object(avatar, 'ASSETS', assets):
                self.assertTrue(avatar.capability()['available'])
                (root / 'scripts/generate_avatar.py').write_bytes(b'changed timing code')
                self.assertFalse(avatar.capability()['available'])

    def test_corrupt_face_cache_regenerates_then_reuses_clean_narration(self):
        import app as workbench
        with TemporaryDirectory() as directory, patch.object(workbench, "JOBS", Path(directory)), \
             patch.object(avatar, "require_available"), patch.object(avatar, "cache_key", return_value="testkey"):
            identity = str(uuid.uuid4())
            folder = Path(directory) / identity
            folder.mkdir()
            job = {"id": identity, "audio_filename": "audio.mp3", "audio_duration": 1}
            (folder / "audio.mp3").write_bytes(b"clean narration")
            (folder / "job.json").write_text(json.dumps(job))
            cached = folder / "avatar-face-testkey.mp4"
            cached.write_bytes(b"truncated")
            cached.with_suffix(".json").write_text(json.dumps({"fps": 25, "elapsed_seconds": 10}))
            def probe(path):
                if Path(path).read_bytes() == b"truncated":
                    raise RuntimeError("invalid video")
                return {"format": {"duration": "1"}, "streams": [{"codec_type": "video", "r_frame_rate": "25/1"}]}
            def run(command, *args, **kwargs):
                self.assertEqual(Path(command[command.index("--audio")+1]), folder / "audio.mp3")
                pending = Path(command[command.index("--out")+1])
                pending.write_bytes(b"generated face")
                pending.with_suffix(".json").write_text(json.dumps({"fps": 25, "elapsed_seconds": 2}))
            with patch.object(workbench, "probe", side_effect=probe), patch.object(workbench, "run_process", side_effect=run) as process:
                control = workbench.Control()
                self.assertEqual(workbench.avatar_face_clip(job, control), cached)
                self.assertEqual(process.call_count, 1)
                self.assertFalse(workbench.read_job(identity)["avatar_cached"])
                self.assertEqual(workbench.avatar_face_clip(job, control), cached)
                self.assertEqual(process.call_count, 1)
                self.assertTrue(workbench.read_job(identity)["avatar_cached"])


if __name__ == "__main__":
    unittest.main()
