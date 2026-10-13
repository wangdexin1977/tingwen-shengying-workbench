"""语音流停止返回时，不应永久占用任务；取消必须及时生效。"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import speech


class SpeechTimeoutTests(unittest.TestCase):
    def test_user_pause_does_not_consume_service_timeout(self):
        class Response:
            def __init__(self, *args):
                pass

            async def stream(self):
                await asyncio.sleep(.02)
                yield {"type": "audio", "data": b"complete"}

        async def check(folder):
            control = app.Control()
            control.paused.set()
            task = asyncio.create_task(speech.synthesize("测试", "voice", folder, control, lambda _: None, factory=Response))
            await asyncio.sleep(.25)
            self.assertFalse(task.done())
            self.assertFalse((folder / "audio.mp3").exists())
            control.paused.clear()
            await asyncio.wait_for(task, .7)
            self.assertEqual((folder / "audio.mp3").read_bytes(), b"complete")

        with tempfile.TemporaryDirectory() as folder, patch.object(speech, "STREAM_IDLE_TIMEOUT", .08), patch.object(speech, "STREAM_TOTAL_TIMEOUT", .1):
            asyncio.run(check(Path(folder)))

    def test_timeout_retries_without_reusing_truncated_audio(self):
        calls = []

        class Response:
            def __init__(self, *args):
                calls.append(True)
                self.first = len(calls) == 1

            async def stream(self):
                yield {"type": "audio", "data": b"truncated" if self.first else b"complete"}
                if self.first:
                    await asyncio.Event().wait()

        with tempfile.TemporaryDirectory() as folder, patch.object(speech, "STREAM_IDLE_TIMEOUT", .05), patch.object(speech, "RETRY_DELAY", .01):
            path = Path(folder)
            asyncio.run(speech.synthesize("测试", "voice", path, app.Control(), lambda _: None, factory=Response))
            self.assertEqual((path / "audio.mp3").read_bytes(), b"complete")
        self.assertEqual(len(calls), 2)

    def test_cancel_while_service_never_returns(self):
        closed = []

        class Stalled:
            def __init__(self, *args):
                pass

            async def stream(self):
                try:
                    await asyncio.Event().wait()
                    yield {}
                finally:
                    closed.append(True)

        async def check(folder):
            control = app.Control()
            task = asyncio.create_task(speech.synthesize("测试", "voice", folder, control, lambda _: None, factory=Stalled))
            await asyncio.sleep(.05)
            control.cancelled.set()
            try:
                with self.assertRaises(speech.Cancelled):
                    await asyncio.wait_for(task, .7)
            except asyncio.TimeoutError:
                self.fail("语音流没有返回时，取消未在 0.7 秒内生效")

        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(check(Path(folder)))
        self.assertEqual(closed, [True])

    def test_stalled_service_exhausts_retries_without_partial_success(self):
        calls = []

        class Stalled:
            def __init__(self, *args):
                calls.append(True)

            async def stream(self):
                yield {"type": "audio", "data": b"incomplete"}
                await asyncio.Event().wait()

        async def check(folder):
            try:
                with self.assertRaisesRegex(RuntimeError, "语音服务.*超时"):
                    await asyncio.wait_for(speech.synthesize("测试", "voice", folder, app.Control(), lambda _: None, factory=Stalled), 1.5)
            except asyncio.TimeoutError:
                self.fail("停止返回数据的语音连接没有超时退出")

        with tempfile.TemporaryDirectory() as folder, patch.object(speech, "STREAM_IDLE_TIMEOUT", .08, create=True), patch.object(speech, "STREAM_TOTAL_TIMEOUT", .2, create=True), patch.object(speech, "RETRY_DELAY", .01, create=True):
            path = Path(folder)
            asyncio.run(check(path))
            self.assertFalse((path / "audio.mp3").exists())
            self.assertFalse((path / "part-0000.mp3").exists())
        self.assertEqual(len(calls), 3)

    def test_continuous_metadata_cannot_bypass_total_timeout(self):
        class MetadataOnly:
            def __init__(self, *args):
                pass

            async def stream(self):
                while True:
                    await asyncio.sleep(.01)
                    yield {"type": "SentenceBoundary"}

        async def check(folder):
            try:
                with self.assertRaisesRegex(RuntimeError, "语音服务.*超时"):
                    await asyncio.wait_for(speech.synthesize("测试", "voice", folder, app.Control(), lambda _: None, factory=MetadataOnly), 1.5)
            except asyncio.TimeoutError:
                self.fail("持续返回元数据时，单次连接总时限没有生效")

        with tempfile.TemporaryDirectory() as folder, patch.object(speech, "STREAM_IDLE_TIMEOUT", .2, create=True), patch.object(speech, "STREAM_TOTAL_TIMEOUT", .08, create=True), patch.object(speech, "RETRY_DELAY", .01, create=True):
            asyncio.run(check(Path(folder)))


if __name__ == "__main__":
    unittest.main()
