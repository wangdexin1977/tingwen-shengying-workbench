"""本机背景音乐库：内置 CC0 曲目与用户导入音频。"""
import base64
import binascii
import hashlib
import json
import math
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from scripts.compose_video import FFMPEG, FFPROBE

ROOT = Path(__file__).resolve().parent
BUILTINS = ROOT / 'assets' / 'music'
UPLOADS = ROOT / 'music_uploads'
DEFAULTS = {'enabled': False, 'track_id': 'vaporware', 'volume': 15, 'loop_seconds': 180}
MAX_BYTES = 40 * 1024 * 1024
EXTENSIONS = {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.aiff', '.aif', '.opus'}


def normalize_settings(value=None, *, default_enabled=False):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError('背景音乐设置无效')
    settings = {**DEFAULTS, 'enabled': default_enabled, **{k: v for k, v in value.items() if k in DEFAULTS}}
    if not isinstance(settings['enabled'], bool):
        raise ValueError('背景音乐开关无效')
    if not isinstance(settings['track_id'], str) or not re.fullmatch(r'[a-z0-9-]{1,64}', settings['track_id']):
        raise ValueError('请选择有效的背景音乐')
    volume = settings['volume']
    if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not math.isfinite(volume) or not 0 <= volume <= 100:
        raise ValueError('背景音乐音量需为 0–100%')
    loop = settings['loop_seconds']
    if isinstance(loop, bool) or loop not in (120, 180, 300):
        raise ValueError('背景音乐循环时长需为 2、3 或 5 分钟')
    settings['volume'] = round(volume, 2)
    settings['loop_seconds'] = int(loop)
    return settings


def entry(track_id):
    if not isinstance(track_id, str) or not re.fullmatch(r'[a-z0-9-]{1,64}', track_id):
        raise ValueError('音乐编号无效')
    builtins = json.loads((BUILTINS / 'catalog.json').read_text(encoding='utf-8'))
    item = next((item for item in builtins if item['id'] == track_id), None)
    directory = BUILTINS
    if item is None:
        if not re.fullmatch(r'user-[a-f0-9]{32}', track_id):
            raise ValueError('背景音乐不存在，请重新选择')
        directory = UPLOADS
        meta = directory / (track_id + '.json')
        if meta.is_symlink() or not meta.is_file():
            raise ValueError('自定义音乐不存在，请重新添加')
        item = json.loads(meta.read_text(encoding='utf-8'))
    filename = item['filename']
    if Path(filename).name != filename or '\\' in filename:
        raise ValueError('音乐文件名无效')
    path = directory / filename
    if path.is_symlink() or not path.is_file() or path.resolve().parent != directory.resolve():
        raise ValueError('音乐文件不存在，请重新添加')
    return {**item, 'path': path}


def catalog():
    ids = [i['id'] for i in json.loads((BUILTINS / 'catalog.json').read_text(encoding='utf-8'))]
    if UPLOADS.exists():
        ids += [p.stem for p in sorted(UPLOADS.glob('user-*.json'))]
    result = []
    for track_id in ids:
        try:
            item = entry(track_id)
            item.pop('path')
            result.append({**item, 'preview_url': '/api/music/' + track_id + '/preview'})
        except (ValueError, OSError, KeyError):
            continue
    return result


def import_audio(filename, encoded):
    if not isinstance(filename, str) or Path(filename).suffix.lower() not in EXTENSIONS:
        raise ValueError('音乐支持 MP3、WAV、M4A、AAC、FLAC、OGG、AIFF 或 OPUS')
    if not isinstance(encoded, str) or len(encoded) > (MAX_BYTES + 2) // 3 * 4:
        raise ValueError('单首音乐请控制在 40 MB 以内')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('音乐文件读取失败') from None
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError('音乐为空或超过 40 MB')
    UPLOADS.mkdir(exist_ok=True)
    digest = hashlib.sha256(raw).hexdigest()
    for item in catalog():
        if item.get('original_sha256') == digest:
            return item
    # 用户文件先在临时目录完整解码，成功后再加入曲库；不复制原文件路径。
    with tempfile.TemporaryDirectory(prefix='.import-', dir=UPLOADS) as temporary:
        directory = Path(temporary)
        source = directory / ('source' + Path(filename).suffix.lower())
        source.write_bytes(raw)
        try:
            result = subprocess.run([FFPROBE, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(source)], capture_output=True, text=True, timeout=15)
            info = json.loads(result.stdout)
            duration = float(info['format']['duration'])
            if result.returncode or not any(s['codec_type'] == 'audio' for s in info['streams']) or not math.isfinite(duration) or not .5 <= duration <= 3600:
                raise ValueError()
            output = directory / 'music.mp3'
            result = subprocess.run([FFMPEG, '-v', 'error', '-xerror', '-y', '-i', str(source), '-map', '0:a:0', '-vn', '-map_metadata', '-1', '-c:a', 'libmp3lame', '-b:a', '160k', '-ar', '44100', '-ac', '2', str(output)], capture_output=True, timeout=90)
            if result.returncode or not output.is_file() or output.stat().st_size == 0:
                raise ValueError()
        except (ValueError, KeyError, subprocess.TimeoutExpired):
            raise ValueError('音乐无法完整解码，或时长不在 0.5 秒至 60 分钟内，请换一个音频文件') from None
        track_id = 'user-' + uuid.uuid4().hex
        item = {'id': track_id, 'name': Path(filename.replace('\\', '/')).stem[:100], 'filename': track_id + '.mp3', 'duration': round(duration, 3), 'builtin': False, 'license': '用户自备', 'author': '', 'source_url': '', 'original_sha256': digest}
        output.replace(UPLOADS / item['filename'])
        (UPLOADS / (track_id + '.json')).write_text(json.dumps(item, ensure_ascii=False), encoding='utf-8')
    return {**item, 'preview_url': '/api/music/' + track_id + '/preview'}
