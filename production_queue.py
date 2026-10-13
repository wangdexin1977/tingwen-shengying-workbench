"""持久保存待制作项目，串行执行 MP3 / MP4；仅使用已保存的配图。"""
import base64
import json
import subprocess
import threading
import time
import uuid
from pathlib import Path

from flask import abort, g, jsonify, request

ROOT = Path(__file__).resolve().parent / 'queue'
LOCK = threading.RLock()
STATE_DEFAULT = {'order': [], 'running': False, 'paused': False, 'scheduled_at': None,
                 'output': 'mp4', 'current': '', 'message': '保存项目后可按顺序制作'}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    pending.replace(path)


class ProductionQueue:
    def __init__(self, workbench):
        self.workbench = workbench
        self.thread = None
        self.scheduler = None
        self.keep_awake = None

    def state(self):
        path = ROOT / 'state.json'
        return {**STATE_DEFAULT, **(json.loads(path.read_text()) if path.exists() else {})}

    def set_state(self, **changes):
        with LOCK:
            state = self.state()
            state.update(changes)
            write_json(ROOT / 'state.json', state)
            return state

    def folder(self, identifier):
        try:
            if str(uuid.UUID(identifier)) != identifier:
                raise ValueError()
        except (ValueError, TypeError):
            abort(404)
        return ROOT / 'projects' / identifier

    def project(self, identifier):
        path = self.folder(identifier) / 'project.json'
        if not path.exists():
            abort(404)
        return json.loads(path.read_text())

    def update(self, identifier, **changes):
        with LOCK:
            project = self.project(identifier)
            project.update(changes)
            write_json(self.folder(identifier) / 'project.json', project)
            return project

    def photos(self, data):
        wb = self.workbench
        if data.get('source_job_id'):
            job = wb.read_job(data['source_job_id'])
            if job['image_state'] != 'ready' or data.get('image_version') != job['image_version']:
                raise ValueError('当前图片尚未完成或版本已变化，请重新选择')
            return [(path.read_bytes(), {'source': 'saved-task', 'source_name': path.name}) for path in wb.image_paths(job)]
        if data.get('photos'):
            return wb.local_photos(data)
        if data.get('photo_url'):
            return [wb.download_photo(data['photo_url'])]
        return []

    def snapshot(self, data):
        wb = self.workbench
        settings = data.get('settings', {})
        audio = wb.audio_configuration(settings)
        if audio['audio_provider'] == 'omnivoice':
            path = wb.omnivoice_local.project_root() / 'outputs/webui/user_settings.json'
            try:
                config = json.loads(path.read_text()) if path.exists() else {}
                profile = config.get('model_profiles', {}).get(audio['omnivoice_settings']['backend'], {})
            except (OSError, ValueError):
                profile = {}
            audio['omnivoice_settings']['profile_snapshot'] = profile
        voice = settings.get('voice', wb.VOICES[0][0])
        if voice not in {item[0] for item in wb.VOICES}:
            raise ValueError('朗读音色无效')
        title_style = wb.normalize_title_settings(settings.get('title_settings'))
        subtitles = wb.subtitles.normalize_style(settings.get('subtitle_settings'))
        music = wb.background_music.normalize_settings(settings.get('music_settings'))
        if music['enabled']:
            wb.background_music.entry(music['track_id'])
        return {'voice': voice, **audio, 'title_settings': title_style,
                'subtitle_settings': subtitles, 'music_settings': music,
                'title_enabled': bool(settings.get('title_enabled', False))}

    def save_projects(self, data):
        settings = self.snapshot(data)
        projects = data.get('projects')
        if not isinstance(projects, list) or not 1 <= len(projects) <= 200:
            raise ValueError('每次可保存 1–200 个稿件或章节')
        validated = []
        for item in projects:
            if not isinstance(item, dict):
                raise ValueError('项目格式无效')
            text = str(item.get('text', '')).strip()
            if not text:
                raise ValueError('请填写文案或选择至少一个有效章节')
            if settings['audio_provider'] == 'omnivoice' and sum(not char.isspace() for char in text) > 100000:
                raise ValueError('单个本机朗读项目超过 100000 字，请按章节拆分')
            title = str(item.get('title', '')).strip() if settings['title_enabled'] else ''
            if self.workbench.title_character_count(title) > 30:
                raise ValueError('视频标题最多 30 字')
            validated.append({'text': text, 'title': title,
                              'name': str(item.get('name') or title or self.workbench.default_narration_title(text))[:120]})
        photos = self.photos(data)
        if not photos and not data.get('audio_only'):
            raise ValueError('请先选用并保存照片，或完成 AI 配图后入队；仅制作 MP3 可不选图')
        saved = []
        for item in validated:
            identifier = str(uuid.uuid4())
            directory = self.folder(identifier)
            directory.mkdir(parents=True)
            images = []
            for index, (raw, metadata) in enumerate(photos):
                name = f'image-{index + 1}.png'
                (directory / name).write_bytes(raw)
                images.append({'filename': name, 'metadata': metadata})
            project = {'id': identifier, 'created': time.time(), **item, 'settings': settings,
                       'images': images, 'images_version': uuid.uuid4().hex,
                       'state': 'waiting', 'job_id': '', 'error': ''}
            write_json(directory / 'project.json', project)
            saved.append(identifier)
        with LOCK:
            state = self.state()
            self.set_state(order=state['order'] + saved)
        return saved

    def image_payload(self, project):
        photos = []
        for image in project['images']:
            filename = image['filename']
            if Path(filename).name != filename:
                raise ValueError('保存的照片路径无效')
            path = self.folder(project['id']) / filename
            photos.append({'photo_name': filename, 'photo_data': base64.b64encode(path.read_bytes()).decode()})
        return {'image_mode': 'upload', 'photos': photos} if photos else {'image_mode': 'none'}

    def invoke(self, path, payload):
        wb = self.workbench
        with wb.app.test_request_context(path, method='POST', json=payload,
                                         headers={'X-Local-Token': wb.TOKEN, 'Host': '127.0.0.1'}):
            g.production_queue_internal = True
            response = wb.app.full_dispatch_request()
            if response.status_code >= 400:
                raise RuntimeError((response.get_json() or {}).get('error', '制作步骤失败'))
            return response.get_json()

    def wait_for(self, identifier, part):
        wb = self.workbench
        while True:
            if not self.state()['running']:
                raise InterruptedError('队列已停止，已生成的素材保留')
            job = wb.read_job(identifier)
            if self.state()['paused']:
                time.sleep(.2)
                continue
            if job[part + '_state'] in {'ready', 'done'} and (identifier, part) not in wb.ACTIVE:
                return job
            if job[part + '_state'] in {'failed', 'cancelled', 'interrupted'} and (identifier, part) not in wb.ACTIVE:
                raise RuntimeError(job.get(part + '_message') or '制作未完成')
            time.sleep(.2)

    def execute(self, identifier, output):
        wb = self.workbench
        project = self.project(identifier)
        if output == 'mp4' and not project['images']:
            raise RuntimeError('本项目没有保存配图，请添加照片后重试')
        self.update(identifier, state='running', error='')
        self.set_state(current=identifier, message='正在制作：' + project['name'])
        job_id = project.get('job_id')
        if not job_id:
            payload = {'text': project['text'], 'title': project['title'], **project['settings'],
                       'prompt': '', **self.image_payload(project)}
            job = self.invoke('/api/jobs', payload)
            job_id = job['id']
            self.update(identifier, job_id=job_id, job_images_version=project['images_version'])
        else:
            job = wb.read_job(job_id)
            if job['audio_state'] != 'ready':
                self.invoke(f'/api/jobs/{job_id}/retry-audio', project['settings'])
            elif job.get('paused') or job.get('cancelled'):
                self.invoke(f'/api/jobs/{job_id}/resume', {})
        job = self.wait_for(job_id, 'audio')
        if output == 'mp3':
            self.update(identifier, state='audio_ready', error='')
            return
        if project.get('job_images_version') != project['images_version'] and project.get('job_id'):
            job = self.invoke(f'/api/jobs/{job_id}/use-image', self.image_payload(project))
            self.update(identifier, job_images_version=project['images_version'])
        self.invoke(f'/api/jobs/{job_id}/confirm', {'image_version': job['image_version'],
                    'title_settings': project['settings']['title_settings'],
                    'subtitle_settings': project['settings']['subtitle_settings'],
                    'music_settings': project['settings']['music_settings']})
        self.wait_for(job_id, 'video')
        self.update(identifier, state='done', error='')

    def start(self, output='mp4', identifiers=None):
        if output not in {'mp3', 'mp4'}:
            raise ValueError('请选择生成 MP3 或 MP4')
        with LOCK:
            state = self.state()
            if state['running'] or (self.thread and self.thread.is_alive()):
                raise ValueError('队列正在运行或停止，请稍后操作')
            if self.workbench.ACTIVE:
                raise ValueError('请等待当前单独任务完成后再启动队列')
            order = state['order']
            if identifiers is not None:
                if not isinstance(identifiers, list) or not identifiers or any(item not in order for item in identifiers):
                    raise ValueError('选择的队列项目无效')
                order = [item for item in order if item in identifiers]
            eligible = {'waiting', 'failed', 'interrupted'} | ({'audio_ready'} if output == 'mp4' else set())
            pending = [item for item in order if self.project(item)['state'] in eligible]
            if not pending:
                raise ValueError('没有可制作的项目；已完成项目不会重复生成')
            self.set_state(running=True, paused=False, scheduled_at=None, output=output, current='', message='按保存顺序开始制作')
            self.awake(True)
            self.thread = threading.Thread(target=self.run, args=(pending, output), daemon=True, name='production-queue')
            self.thread.start()

    def run(self, pending, output):
        try:
            for identifier in pending:
                while self.state()['paused'] and self.state()['running']:
                    time.sleep(.2)
                if not self.state()['running']:
                    break
                try:
                    self.execute(identifier, output)
                except InterruptedError as exc:
                    self.update(identifier, state='interrupted', error=str(exc))
                    break
                except Exception as exc:
                    # 用户选择：保留失败项目，继续制作下一项。
                    self.update(identifier, state='failed', error=str(exc)[:500])
        finally:
            self.set_state(running=False, paused=False, current='', message='本轮队列已结束；失败项保留，可重试')
            self.awake(False)

    def awake(self, enabled):
        if enabled and self.keep_awake is None and Path('/usr/bin/caffeinate').exists():
            self.keep_awake = subprocess.Popen(['/usr/bin/caffeinate', '-i'])
        elif not enabled and self.keep_awake:
            self.keep_awake.terminate()
            self.keep_awake.wait(timeout=5)
            self.keep_awake = None

    def start_scheduler(self):
        if self.scheduler:
            return
        with LOCK:
            state = self.state()
            if state['running']:
                for identifier in state['order']:
                    if self.project(identifier)['state'] == 'running':
                        self.update(identifier, state='interrupted', error='服务重启，素材已保留；请继续或重试')
                self.set_state(running=False, paused=False, current='', message='服务已重启，请继续待制作队列')
            self.awake(bool(state.get('scheduled_at')))
        def monitor():
            while True:
                state = self.state()
                if state.get('scheduled_at') and state['scheduled_at'] <= time.time() and not state['running']:
                    try:
                        self.start(state['output'])
                    except ValueError as exc:
                        # 单独任务占用时等待，既不取消任务也不丢弃预约。
                        self.set_state(message='定时队列等待：' + str(exc))
                time.sleep(1)
        self.scheduler = threading.Thread(target=monitor, daemon=True, name='production-queue-scheduler')
        self.scheduler.start()

    def view(self):
        state = self.state()
        projects = []
        for identifier in state['order']:
            project = self.project(identifier)
            view = {key: project[key] for key in ['id', 'name', 'state', 'created', 'error', 'job_id']}
            view.update(characters=len(project['text']), images=len(project['images']))
            if project['job_id']:
                try:
                    view['job'] = self.workbench.job_view(self.workbench.read_job(project['job_id']))
                except Exception:
                    view['job'] = None
            projects.append(view)
        return {**state, 'projects': projects}


def install(workbench):
    manager = ProductionQueue(workbench)
    app = workbench.app

    @app.before_request
    def queue_guard():
        if request.method == 'POST' and (request.path == '/api/jobs' or request.path == '/api/jobs/import-audio' or request.path.startswith('/api/jobs/')):
            if manager.state()['running'] and not getattr(g, 'production_queue_internal', False) and not request.path.endswith('/reveal'):
                return jsonify(error='顺序制作队列正在运行，请通过队列暂停或停止后再操作单独任务'), 409

    @app.get('/api/queue')
    def list_queue():
        return jsonify(manager.view())

    @app.post('/api/queue/projects')
    def save_projects():
        try:
            saved = manager.save_projects(request.get_json() or {})
        except (ValueError, OSError, RuntimeError) as exc:
            abort(400, str(exc))
        return jsonify(ids=saved, queue=manager.view()), 201

    @app.get('/api/queue/projects/<identifier>')
    def get_project(identifier):
        return jsonify(manager.project(identifier))

    @app.post('/api/queue/projects/<identifier>/images')
    def replace_images(identifier):
        if manager.state()['running']:
            abort(409, '请先暂停并停止队列，再更改保存的配图')
        project = manager.project(identifier)
        try:
            photos = manager.photos(request.get_json() or {})
            if not photos:
                raise ValueError('请至少选择一张照片')
            version = uuid.uuid4().hex
            images = []
            for index, (raw, metadata) in enumerate(photos):
                filename = f'image-{version}-{index}.png'
                (manager.folder(identifier) / filename).write_bytes(raw)
                images.append({'filename': filename, 'metadata': metadata})
            manager.update(identifier, images=images, images_version=version,
                           state='audio_ready' if project['state'] == 'audio_ready' else 'waiting', error='')
        except (ValueError, OSError) as exc:
            abort(400, str(exc))
        return jsonify(manager.view())

    @app.post('/api/queue/start')
    def start_queue():
        data = request.get_json() or {}
        try:
            manager.start(data.get('output', 'mp4'), data.get('ids'))
        except ValueError as exc:
            abort(409, str(exc))
        return jsonify(manager.view())

    @app.post('/api/queue/schedule')
    def schedule_queue():
        data = request.get_json() or {}
        when = data.get('scheduled_at')
        output = data.get('output', 'mp4')
        if not isinstance(when, (int, float)) or not time.time() < when < time.time() + 366 * 86400 or output not in {'mp3', 'mp4'}:
            abort(400, '请选择未来的开始时间与输出方式')
        if manager.state()['running']:
            abort(409, '队列正在运行，请结束后再预约')
        if not manager.state()['order']:
            abort(400, '请先保存至少一个待制作项目')
        manager.set_state(scheduled_at=when, output=output, message='预约已保存，到时按队列顺序制作')
        manager.awake(True)
        return jsonify(manager.view())

    @app.post('/api/queue/<action>')
    def queue_action(action):
        state = manager.state()
        if action in {'pause', 'resume', 'stop'}:
            manager.set_state(paused=action == 'pause', **({'running': False, 'scheduled_at': None} if action == 'stop' else {}))
            if state['current']:
                project = manager.project(state['current'])
                if project['job_id']:
                    manager.invoke(f"/api/jobs/{project['job_id']}/" + ('cancel' if action == 'stop' else action), {})
            if action == 'stop' and not state['running']:
                manager.awake(False)
        elif action == 'cancel-schedule':
            manager.set_state(scheduled_at=None, message='预约已取消，已保存项目保留')
            if not state['running']:
                manager.awake(False)
        elif action == 'reorder':
            order = (request.get_json() or {}).get('order')
            if state['running']:
                abort(409, '请在开始制作前调整顺序')
            if not isinstance(order, list) or len(order) != len(set(order)) or set(order) != set(state['order']):
                abort(400, '队列顺序无效')
            manager.set_state(order=order)
        elif action == 'remove':
            if state['running']:
                abort(409, '请先停止队列')
            identifier = (request.get_json() or {}).get('id')
            if identifier not in state['order']:
                abort(404)
            manager.update(identifier, archived=True)
            manager.set_state(order=[item for item in state['order'] if item != identifier])
        else:
            abort(404)
        return jsonify(manager.view())
    return manager
