"""融合产品的文档导入、专业语音工作区和成品交接。"""
import json
import os
import socket
import subprocess
import threading
import uuid
from pathlib import Path

from flask import abort, jsonify, request, send_file

ROOT = Path(__file__).resolve().parent
TTS = ROOT / 'tts'
EXPERT_PORT = 7862
EXPERT_LOCK = threading.Lock()
TOUTIAO_VIDEO_URL = 'https://mp.toutiao.com/profile_v4/xigua/upload-video'


def expert_product_paths():
    root = TTS / 'outputs/webui'
    paths = {source.resolve() for source in root.iterdir()
             if source.suffix.lower() in {'.mp3', '.wav', '.flac'} and source.is_file() and not source.is_symlink()}
    registry = root / 'products.jsonl'
    if registry.exists():
        for line in registry.read_text().splitlines():
            try:
                source = Path(json.loads(line)['path'])
                if source.is_file() and not source.is_symlink() and source.suffix.lower() in {'.mp3', '.wav', '.flac'}:
                    paths.add(source.resolve())
            except (ValueError, KeyError):
                continue
    return paths


def expert_product(key):
    import hashlib
    for source in expert_product_paths():
        if hashlib.sha256(str(source).encode()).hexdigest()[:24] == key:
            return source
    abort(404)


def install(workbench):
    app = workbench.app
    import production_queue
    workbench.production_queue = production_queue.install(workbench)

    @app.get('/api/preferences')
    def get_preferences():
        import preferences
        try:
            return jsonify(preferences=preferences.read())
        except (OSError, ValueError):
            return jsonify(preferences=None, warning='上次配置无法读取，请检查 settings/preferences.json；本次设置仍可重新保存。')

    @app.post('/api/preferences')
    def save_preferences():
        import preferences
        try:
            saved = preferences.save(request.get_json())
        except ValueError as exc:
            abort(400, str(exc))
        except OSError:
            return jsonify(error='配置无法写入，请检查本机磁盘与目录权限'), 500
        return jsonify(preferences=saved)

    @app.post('/api/documents/import')
    def import_document():
        data = request.get_json() or {}
        import base64
        name = Path(str(data.get('filename', ''))).name
        if Path(name).suffix.lower() not in {'.txt', '.md', '.markdown', '.doc', '.docx', '.pdf', '.epub', '.mobi'}:
            abort(400, '请选择 TXT、MD、Word、PDF、EPUB 或 MOBI 文档')
        try:
            raw = base64.b64decode(data.get('data', ''), validate=True)
        except (ValueError, TypeError):
            abort(400, '文档数据无效')
        if not raw or len(raw) > 40 * 1024 * 1024:
            abort(400, '主工作区支持 40 MB 以内文档，更大文件请使用专业工作区')
        directory = ROOT / 'documents' / uuid.uuid4().hex
        directory.mkdir(parents=True)
        source = directory / name
        source.write_bytes(raw)
        try:
            result = workbench.omnivoice_local.run({'operation': 'document', 'file': str(source)}, directory)
        except (OSError, RuntimeError) as exc:
            abort(400, str(exc))
        return jsonify(result)

    @app.post('/api/expert/start')
    def start_expert():
        with EXPERT_LOCK:
            with socket.socket() as probe:
                occupied = probe.connect_ex(('127.0.0.1', EXPERT_PORT)) == 0
            if occupied:
                import urllib.request
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{EXPERT_PORT}/config', timeout=3) as response:
                        config = json.load(response)
                    if config.get('title') != '听文声影工作台 · 专业朗读':
                        abort(409, '专业工作区端口已被其他程序占用')
                except (OSError, ValueError):
                    abort(409, '专业服务正在启动或端口被占用，请稍后重试')
            else:
                pid_path = ROOT / 'expert.pid'
                try:
                    os.kill(int(pid_path.read_text()), 0)
                    return jsonify(starting=True, url=f'http://127.0.0.1:{EXPERT_PORT}/')
                except (OSError, ValueError):
                    pass
                env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                           GRADIO_ANALYTICS_ENABLED='False', GRADIO_TEMP_DIR=str(ROOT / 'tts/tmp'),
                           PYTHONDONTWRITEBYTECODE='1')
                with (ROOT / 'expert.log').open('ab') as log:
                    process = subprocess.Popen([str(TTS / '.venv/bin/python'), '-B', '-m', 'mlx_indextts.webui'],
                        cwd=TTS, env=env, stdin=subprocess.DEVNULL, stdout=log,
                        stderr=subprocess.STDOUT, start_new_session=True)
                pid_path.write_text(str(process.pid))
                return jsonify(starting=True, url=f'http://127.0.0.1:{EXPERT_PORT}/')
        return jsonify(starting=False, url=f'http://127.0.0.1:{EXPERT_PORT}/')

    @app.get('/api/expert/outputs')
    def expert_outputs():
        # 专业工作区默认产物可一键送入同一视频任务流程。
        outputs = []
        import hashlib
        for source in expert_product_paths():
            key = hashlib.sha256(str(source).encode()).hexdigest()[:24]
            outputs.append({'filename': source.name, 'size': source.stat().st_size,
                            'modified': source.stat().st_mtime, 'path': str(source),
                            'key': key, 'url': '/api/expert/audio/' + key})
        return jsonify(outputs=sorted(outputs, key=lambda x: x['modified'], reverse=True))

    @app.get('/api/expert/state')
    def expert_state():
        import urllib.request
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{EXPERT_PORT}/config', timeout=2) as response:
                config = json.load(response)
            return jsonify(ready=config.get('title') == '听文声影工作台 · 专业朗读')
        except (OSError, ValueError):
            return jsonify(ready=False)

    @app.get('/api/expert/audio/<key>')
    def expert_audio(key):
        source = expert_product(key)
        return send_file(source, as_attachment=request.args.get('download') == '1')

    @app.post('/api/expert/audio/<key>/reveal')
    def reveal_expert_audio(key):
        subprocess.Popen(['open', '-R', str(expert_product(key))])
        return jsonify(ok=True)

    @app.get('/api/products/audio')
    def audio_products():
        items = []
        for path in workbench.JOBS.glob('*/job.json'):
            job = json.loads(path.read_text())
            if job.get('audio_state') == 'ready':
                items.append({'id': job['id'], 'title': job.get('title') or job.get('text', '')[:30],
                              'created': job['created'], 'duration': job.get('audio_duration'),
                              'url': f"/api/jobs/{job['id']}/media/audio", 'path': str(workbench.audio_path(job))})
        return jsonify(products=sorted(items, key=lambda x: x['created'], reverse=True))

    @app.post('/api/videos/<job_id>/publish-toutiao')
    def prepare_toutiao_publish(job_id):
        data = request.get_json() or {}
        path = workbench.archive_video_path(job_id, str(data.get('filename', '')))
        job = workbench.read_job(job_id)
        subprocess.Popen(['open', '-R', str(path)])
        subprocess.Popen(['open', TOUTIAO_VIDEO_URL])
        return jsonify(url=TOUTIAO_VIDEO_URL, path=str(path),
                       title=job.get('title') or path.stem,
                       message='已打开今日头条视频发布入口，并在 Finder 中选中 MP4。选择或拖入视频，填写标题与简介后，在今日头条确认发布。首次使用需登录。')
