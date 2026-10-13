"""自动上传的文件范围、状态、重复点击与浏览器交接回归。"""
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import toutiao_publish as publish


class ToutiaoPublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(publish, 'DATA', self.root / 'settings')
        self.patch.start()
        self.video = self.root / '正确的成片.mp4'
        self.video.write_bytes(b'mp4')

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def record(self, state='waiting_login'):
        data = dict(id=str(uuid.uuid4()), path=str(self.video), title='标题', size=3,
                    state=state, created=time.time(), filename=self.video.name)
        publish.write(publish.DATA / 'requests' / (data['id'] + '.json'), data)
        return data

    def page(self, accepts):
        frame = Mock(url=publish.URL)
        controls = [Mock() for _ in accepts]
        for control, accept in zip(controls, accepts):
            control.get_attribute.return_value = accept
        frame.locator.return_value.count.return_value = len(controls)
        frame.locator.return_value.nth.side_effect = controls.__getitem__
        page = Mock(frames=[frame], url=publish.URL)
        return page, controls

    def test_video_control_excludes_cover_and_untrusted_frames(self):
        page, controls = self.page(['image/*', 'video/*'])
        self.assertIs(publish.video_input(page), controls[1])
        page.frames[0].url = 'https://other.example/upload'
        self.assertIsNone(publish.video_input(page))
        self.assertFalse(publish.official('https://mp.toutiao.com.evil.example/'))
        self.assertFalse(publish.official('http://mp.toutiao.com/'))

    def test_selected_exact_file_no_publish_click(self):
        page, controls = self.page(['image/*', '.mp4,.mov'])
        self.assertTrue(publish.load_video(page, self.record()))
        controls[1].set_input_files.assert_called_once_with(str(self.video), timeout=15000)
        controls[0].set_input_files.assert_not_called()
        page.get_by_role.assert_not_called()
        page.click.assert_not_called()

    def test_changed_file_rejected_and_selection_error_does_not_retry(self):
        page, controls = self.page(['video/*'])
        data = self.record()
        self.video.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            publish.load_video(page, data)
        controls[0].set_input_files.assert_not_called()
        self.video.write_bytes(b'mp4')
        controls[0].set_input_files.side_effect = Exception('page detached')
        with self.assertRaises(RuntimeError):
            publish.load_video(page, data)
        self.assertEqual(controls[0].set_input_files.call_count, 1)

    def test_selection_is_not_reported_as_upload_complete(self):
        page = Mock(url=publish.URL)
        for text, expected in [('视频上传中 40% 标题', None), ('上传失败 标题', 'failed'),
                               ('视频上传中 40% 重新上传 标题', None),
                               ('上传成功 标题 标签 发布', 'ready')]:
            page.locator.return_value.inner_text.return_value = text
            self.assertEqual(publish.upload_result(page), expected)

    def test_title_only_fills_empty_and_does_not_click(self):
        page = Mock()
        control = Mock()
        page.locator.return_value.all.return_value = [control]
        control.is_visible.return_value = True
        control.input_value.return_value = '用户的标题'
        publish.fill_title(page, '默认标题')
        control.fill.assert_not_called()
        control.input_value.return_value = ''
        publish.fill_title(page, '字' * 40)
        control.fill.assert_called_once_with('字' * 30, timeout=2000)
        control.click.assert_not_called()

    def test_dead_worker_marks_interrupted_and_retains_file(self):
        data = self.record()
        result = publish.status(data['id'])
        self.assertEqual(result['state'], 'interrupted')
        self.assertTrue(self.video.exists())
        with self.assertRaises(ValueError):
            publish.status('../secrets')

    def test_repeated_click_reuses_request_and_other_video_is_blocked(self):
        data = self.record()
        with patch.object(Path, 'is_file', return_value=True), patch.object(Path, 'exists', return_value=True), \
             patch.object(publish, 'status', return_value=data), patch.object(publish.subprocess, 'Popen') as launch:
            result = publish.start(self.video, '标题')
            self.assertEqual(result['id'], data['id'])
            with self.assertRaises(RuntimeError):
                publish.start(self.root / '另外.mp4', '另一标题')
            launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
