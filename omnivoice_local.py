"""本机 OmniVoice 适配：使用独立音色库，离线推理，独立任务输出。"""
import json
import os
import queue
import re
import signal
import subprocess
import threading
from pathlib import Path

EXPRESSIONS = {'follow_reference': '跟随参考音频', 'calm': '平静', 'happy': '高兴',
               'sad': '悲伤', 'energetic': '激昂', 'serious': '严肃', 'whisper': '耳语'}


def project_root():
    return Path(__file__).resolve().parent / 'tts'


def library_entry(voice_id):
    if not isinstance(voice_id, str) or not re.fullmatch(r'[0-9a-f]{16}', voice_id):
        raise ValueError('请从 OmniVoice 音色库选择有效音色')
    root = project_root() / 'outputs/webui/voices/library'
    directory = root / voice_id
    try:
        if directory.resolve().parent != root.resolve():
            raise ValueError('音色路径无效')
        data = json.loads((directory / 'metadata.json').read_text(encoding='utf-8'))
        source = directory / data['source_filename']
        preview = directory / 'preview.wav'
        if source.resolve().parent != directory.resolve() or preview.resolve().parent != directory.resolve() or not source.is_file() or not preview.is_file():
            raise ValueError('音色文件缺失或路径无效')
        return {**data, 'id': voice_id, 'preview_path': preview}
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('所选 OmniVoice 音色已不存在，请刷新音色库') from exc


def capabilities():
    root = project_root()
    ready = (root / '.venv/bin/python').is_file() and (root / 'mlx_indextts/webui.py').is_file() and (root / 'models/OmniVoice-bfloat16').is_dir()
    voices = []
    for p in (root / 'outputs/webui/voices/library').glob('*/metadata.json'):
        try:
            entry = library_entry(p.parent.name)
            voices.append({'id': p.parent.name, 'name': str(entry.get('name') or '未命名音色'),
                           'is_favorite': bool(entry.get('is_favorite')), 'added_at': str(entry.get('added_at') or '')})
        except ValueError:
            continue
    voices.sort(key=lambda x: (x['is_favorite'], x['added_at']), reverse=True)
    return {'available': ready, 'voices': voices, 'expressions': EXPRESSIONS,
            'message': '本机语音已就绪（独立音色库）' if ready else '未找到已安装的 OmniVoice；请检查 INDEXTTS_PROJECT_ROOT 配置，不会自动下载模型'}


def settings(value):
    if not isinstance(value, dict):
        raise ValueError('请先选择 OmniVoice 音色和表达方式')
    voice = library_entry(value.get('voice_id'))
    expression = value.get('expression', 'follow_reference')
    if expression not in EXPRESSIONS:
        raise ValueError('OmniVoice 表达方式无效')
    if not capabilities()['available']:
        raise ValueError('本机 OmniVoice 模型尚未就绪')
    backend = value.get('backend', 'OmniVoice')
    if backend not in {'OmniVoice', 'IndexTTS 2.5', 'IndexTTS 2.0', 'Fish Audio S2 Pro', 'VoiceStudio · OmniVoice 原生', 'CosyVoice 3'}:
        raise ValueError('语音模型无效')
    limits = {'speed': (.5, 2), 'interval_silence': (0, 2000), 'seed': (0, 2147483647),
              'temperature': (.1, 2), 'top_p': (.1, 1), 'top_k': (1, 100),
              'max_text_tokens': (30, 300), 'omnivoice_num_steps': (4, 100),
              'omnivoice_guidance_scale': (0, 10), 'emotion_strength': (0, 1)}
    parameters = value.get('parameters') or {}
    if not isinstance(parameters, dict) or set(parameters) - set(limits):
        raise ValueError('语音参数无效')
    validated = {}
    for key, raw in parameters.items():
        number = float(raw)
        low, high = limits[key]
        if not low <= number <= high:
            raise ValueError('语音参数超出范围：' + key)
        validated[key] = number
    snapshot = value.get('profile_snapshot')
    if snapshot is not None and not isinstance(snapshot, dict):
        raise ValueError('保存的语音参数档案无效')
    return {'voice_id': voice['id'], 'voice_name': voice['name'], 'expression': expression,
            'backend': backend, 'parameters': validated, 'profile_snapshot': snapshot}


def run(request, directory, control=None, progress=None):
    """子进程只处理一个本机操作；stdout 为 JSON，模型诊断留在本机日志。"""
    directory.mkdir(parents=True, exist_ok=True)
    request_path = directory / 'request.json'
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
    events = queue.Queue()
    root = project_root()
    env = {**os.environ, 'INDEXTTS_PROJECT_ROOT': str(root), 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}
    command = [str(root / '.venv/bin/python'), str(Path(__file__).parent / 'scripts/omnivoice_runner.py'), str(request_path)]
    with (directory / 'omnivoice.log').open('ab') as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log, text=True, env=env, start_new_session=True)
        def reader():
            for line in process.stdout:
                try: events.put(json.loads(line))
                except ValueError: continue
            process.stdout.close()
        thread = threading.Thread(target=reader, daemon=True); thread.start()
        stopped = False
        result = None
        try:
            while process.poll() is None or thread.is_alive() or not events.empty():
                if control:
                    control.check_cancel()
                    if process.poll() is None and control.paused.is_set() != stopped:
                        stopped = control.paused.is_set()
                        os.killpg(process.pid, signal.SIGSTOP if stopped else signal.SIGCONT)
                try: event = events.get(timeout=.15)
                except queue.Empty: continue
                if event.get('event') == 'progress':
                    if progress: progress(event)
                elif 'ok' in event: result = event
            process.wait()
            if control: control.check_cancel()
            if process.returncode or not result or not result.get('ok'):
                raise RuntimeError((result or {}).get('error') or 'OmniVoice 未完成，请查看任务目录中的 omnivoice.log；已输出片段保留')
            return result
        finally:
            if process.poll() is None:
                if stopped: os.killpg(process.pid, signal.SIGCONT)
                os.killpg(process.pid, signal.SIGTERM)
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL); process.wait()
            thread.join(timeout=1)
