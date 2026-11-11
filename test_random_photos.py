"""专用图库抽样、素材冻结与真实多图合成；不调用外部图片或配音模型。"""
import base64
import io
import re
import subprocess
import shutil
import time
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import app as server
import preferences
import test_workbench as fixtures
import production_queue


class RandomPhotoTests(fixtures.WorkbenchTests):
    def setUp(self):
        super().setUp()
        self.library = Path(self.tmp.name) / 'random-library'
        self.library.mkdir()
        self.enterContext(patch.object(server.random_photos, 'SELECTIONS', Path(self.tmp.name) / 'selections'))

    def add_photos(self, count):
        for i in range(count):
            Image.new('RGB', (80, 45), (i * 17 % 255, i * 31 % 255, 120)).save(self.library / f'{i + 1}.png')

    def select(self, data=None):
        # 模块在实现接口时才导入，当前应以缺少接口的 404 明确失败。
        if hasattr(server, 'random_photos'):
            with patch.object(server.random_photos, 'DIRECTORY', self.library):
                return self.post('/api/random-photos/select', data or {})
        return self.post('/api/random-photos/select', data or {})

    def test_default_seven_unique_and_custom_count(self):
        self.add_photos(12)
        response = self.select()
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(len(response.json['photos']), 7)
        self.assertEqual(len({p['photo_name'] for p in response.json['photos']}), 7)
        self.assertTrue(all('photo_data' not in photo for photo in response.json['photos']))
        response = self.select({'count': 3})
        self.assertEqual(len(response.json['photos']), 3)

    def test_empty_and_invalid_count_do_not_create_jobs(self):
        self.assertEqual(self.select().status_code, 400)
        for count in [0, 51, 1.5, True, '7', None]:
            response = self.select({'count': count})
            self.assertEqual(response.status_code, 400, (count, response.json))
        self.assertEqual(list(server.JOBS.iterdir()), [])

    def test_insufficient_and_corrupt_files_use_valid_images_with_notice(self):
        self.add_photos(2)
        (self.library / 'broken.jpg').write_bytes(b'broken')
        (self.library / 'notes.txt').write_text('not an image')
        (self.library / '.hidden.png').write_bytes((self.library / '1.png').read_bytes())
        (self.library / 'subdirectory').mkdir()
        (self.library / 'linked.png').symlink_to(self.library / '1.png')
        response = self.select()
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual({p['photo_name'] for p in response.json['photos']}, {'1.png', '2.png'})
        self.assertIn('2', response.json['message'])
        self.assertTrue(response.json['skipped'])

    def test_selected_bytes_are_frozen_before_task_creation(self):
        self.add_photos(3)
        response = self.select({'count': 3})
        self.assertEqual(response.status_code, 200, response.json)
        photos = response.json['photos']
        selection = response.json['selection_id']
        for path in self.library.iterdir():
            path.unlink()
        with patch.object(server, 'launch'), patch.object(server, 'client_for') as ai:
            created = self.post('/api/jobs', {'text': '随机配图测试', 'image_mode': 'upload', 'random_photo_selection': selection})
        self.assertEqual(created.status_code, 201, created.json)
        self.assertEqual(len(created.json['gallery']), 3)
        ai.assert_not_called()
        self.assertEqual([p['metadata']['source_name'] for p in created.json['gallery']], [p['photo_name'] for p in photos])
        with patch.object(production_queue, 'ROOT', Path(self.tmp.name) / 'queue'):
            saved = self.post('/api/queue/projects', {'projects': [{'text': '队列配图测试'}],
                             'random_photo_selection': selection, 'settings': {'subtitle_settings': {'enabled': False}}})
            self.assertEqual(saved.status_code, 201, saved.json)
            project = server.production_queue.project(saved.json['ids'][0])
            self.assertEqual(len(project['images']), 3)
            self.assertTrue(all((server.production_queue.folder(project['id']) / item['filename']).exists()
                                for item in project['images']))

    def test_oversize_batch_rejected_and_directory_open_uses_only_dedicated_folder(self):
        self.add_photos(3)
        with patch.object(server.random_photos, 'MAX_BATCH_BYTES', 1):
            response = self.select({'count': 2})
        self.assertEqual(response.status_code, 400)
        self.assertIn('500 MB', response.json['error'])
        with patch.object(server.random_photos, 'DIRECTORY', self.library), patch.object(server.subprocess, 'Popen') as opened:
            response = self.post('/api/random-photos/open', {'directory': '/arbitrary'})
            self.assertEqual(response.status_code, 200)
            opened.assert_called_once_with(['open', str(self.library)])
        self.assertEqual(self.client.post('/api/random-photos/select', json={}).status_code, 403)

    def test_changed_snapshot_and_invalid_selection_are_rejected(self):
        self.add_photos(1)
        selected = self.select({'count': 1}).json
        directory = server.random_photos.SELECTIONS / selected['selection_id']
        next(directory.glob('photo-*')).write_bytes(b'changed')
        for identifier in [selected['selection_id'], '../library', 'missing', True]:
            with patch.object(server, 'launch') as launched:
                response = self.post('/api/jobs', {'text': '副本校验', 'image_mode': 'upload', 'random_photo_selection': identifier})
            self.assertEqual(response.status_code, 400, response.json)
            launched.assert_not_called()
        self.assertEqual(list(server.JOBS.iterdir()), [])

    def test_real_random_three_photo_video_preserves_order_and_full_audio_duration(self):
        self.add_photos(3)
        selection = self.select({'count': 3}).json
        selected = selection['photos']
        def cached_audio(identifier, control):
            path = server.audio_path(server.read_job(identifier))
            shutil.copyfile(server.ROOT / 'assets/previews/zh-CN-YunxiNeural.mp3', path)
            server.update(identifier, audio_state='ready', audio_duration=float(server.probe(path)['format']['duration']))
        with patch.dict(server.WORKERS, {'audio': cached_audio}), patch.object(server, 'client_for') as ai:
            created = self.post('/api/jobs', {'text': '随机三图均分测试', 'image_mode': 'upload', 'random_photo_selection': selection['selection_id'],
                                'title_enabled': False, 'subtitle_settings': {'enabled': False}})
            self.assertEqual(created.status_code, 201, created.json)
            identifier = created.json['id']
            deadline = time.monotonic() + 10
            while server.ACTIVE and time.monotonic() < deadline:
                time.sleep(.02)
            job = server.read_job(identifier)
            self.assertEqual(job['audio_state'], 'ready')
            self.assertEqual(self.post(f'/api/jobs/{identifier}/confirm', {'image_version': job['image_version']}).status_code, 200)
            deadline = time.monotonic() + 40
            while server.ACTIVE and time.monotonic() < deadline:
                time.sleep(.05)
            job = server.read_job(identifier)
            self.assertEqual(job['video_state'], 'done', job.get('video_message'))
            ai.assert_not_called()
        self.assertEqual(job['image_durations'], [job['audio_duration'] / 3] * 3)
        info = server.probe(server.video_path(job))
        self.assertLess(abs(float(info['format']['duration']) - job['audio_duration']), .5)
        for i, photo in enumerate(selected):
            at = job['audio_duration'] / 3 * (i + .5)
            frame = subprocess.run(['/opt/homebrew/bin/ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(server.video_path(job)),
                                    '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', '-'], capture_output=True, check=True)
            with Image.open(io.BytesIO(frame.stdout)) as picture:
                pixel = picture.convert('RGB').getpixel((960, 540))
            original_index = int(Path(photo['photo_name']).stem) - 1
            expected = (original_index * 17, original_index * 31, 120)
            self.assertTrue(all(abs(a - b) < 12 for a, b in zip(pixel, expected)), (i, pixel, expected))

    def test_count_and_mode_preferences_accept_only_integer_count(self):
        value = preferences.normalize({'controls': {'imageMode': 'random', 'randomPhotoCount': 9}})
        self.assertEqual(value['controls']['randomPhotoCount'], 9)
        for count in [0, 51, True, 2.5]:
            with self.assertRaises(ValueError):
                preferences.normalize({'controls': {'randomPhotoCount': count}})

    def test_frontend_selection_reused_count_change_invalidates_and_pending_is_shared(self):
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('<option value="random">', html)
        self.assertRegex(html, r'<input id="randomPhotoCount"[^>]*value="7"')
        script = (server.ROOT / 'static/random-photos.js').read_text()
        harness = r'''
const assert=require('node:assert/strict'),els=new Map();
function element(){return {value:'',children:[],addEventListener(){},replaceChildren(...children){this.children=children},append(...children){this.children.push(...children)}}}
function $(id){if(!els.has(id))els.set(id,element());return els.get(id)}
const document={createElement:element};
let clearWorkspaceUI=()=>{},updateTitlePreview=()=>{},showError=()=>{};
let calls=0,requests=[];
async function api(path,data){if(path.endsWith('/select')){calls++;requests.push(data);await new Promise(resolve=>setTimeout(resolve,5));return {selection_id:'selection-'+calls,requested_count:data.count,message:'已选',photos:Array.from({length:data.count},(_,i)=>({photo_name:i+'.png',preview_data:'Qg=='}))}}return {directory:'/test/library',candidate_count:12}}
$('randomPhotoCount').value='7';
'''
        assertions = r'''
async function main(){
 const [a,b]=await Promise.all([randomPhotoPayload(),randomPhotoPayload()]);
 assert.equal(calls,1);assert.equal(a.random_photo_selection,'selection-1');assert.deepEqual(a,b);assert.equal(a.image_mode,'upload');
 assert.ok(!a.photos);
 await randomPhotoPayload();assert.equal(calls,1);
 $('randomPhotoCount').value='3';$('randomPhotoCount').oninput();await randomPhotoPayload();assert.equal(calls,2);assert.equal(requests[1].count,3);
 await chooseRandomPhotos();assert.equal(calls,3);
 clearWorkspaceUI();await randomPhotoPayload();assert.equal(calls,4);
 $('randomPhotoCount').value='1.5';await assert.rejects(randomPhotoPayload(),/整数/);
}
main().catch(error=>{console.error(error);process.exitCode=1});
'''
        result = subprocess.run(['/opt/homebrew/bin/node', '-'], input=harness + script + assertions,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


for name in dir(fixtures.WorkbenchTests):
    if name.startswith('test_') and name not in RandomPhotoTests.__dict__:
        setattr(RandomPhotoTests, name, None)
