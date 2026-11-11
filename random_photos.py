"""从本产品专用目录抽取照片；返回素材副本供既有导入流程保存。"""
import base64
import hashlib
import io
import json
import random
import re
import shutil
import uuid
from pathlib import Path

from PIL import Image
from photo_import import PhotoError, MAX_BYTES, MAX_BATCH_BYTES, MAX_BATCH_COUNT, normalize, local_photos

DIRECTORY = Path(__file__).resolve().parent / '随机配图'
SELECTIONS = Path(__file__).resolve().parent / 'records' / 'random-photos'
EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif', '.avif', '.bmp', '.tif', '.tiff', '.gif'}


def candidates():
    try:
        DIRECTORY.mkdir(parents=True, exist_ok=True)
        return sorted((path for path in DIRECTORY.iterdir()
                       if not path.name.startswith('.') and not path.is_symlink()
                       and path.is_file() and path.suffix.lower() in EXTENSIONS), key=lambda path: path.name)
    except OSError:
        raise PhotoError('无法读取随机配图文件夹，请检查文件夹权限') from None


def status():
    return {'directory': str(DIRECTORY), 'candidate_count': len(candidates()), 'default_count': 7,
            'max_count': MAX_BATCH_COUNT}


def select(count=7):
    if type(count) is not int or not 1 <= count <= MAX_BATCH_COUNT:
        raise PhotoError('随机图片数量必须为 1–50 的整数')
    paths = candidates()
    random.SystemRandom().shuffle(paths)
    selected, stored, skipped, total = [], [], [], 0
    identifier = uuid.uuid4().hex
    pending = SELECTIONS / ('.' + identifier)
    try:
        pending.mkdir(parents=True)
        for path in paths:
            try:
                with path.open('rb') as source:
                    raw = source.read(MAX_BYTES + 1)
                normalized, _ = normalize(raw, 'upload', path.name)
            except (PhotoError, OSError):
                skipped.append(path.name)
                continue
            total += len(raw)
            if total > MAX_BATCH_BYTES:
                raise PhotoError('抽取照片合计超过 500 MB，请减少抽取数量或先压缩图片')
            filename = f'photo-{len(selected)}{path.suffix.lower()}'
            (pending / filename).write_bytes(raw)
            stored.append({'filename': filename, 'photo_name': path.name, 'sha256': hashlib.sha256(raw).hexdigest()})
            with Image.open(io.BytesIO(normalized)) as picture:
                picture.thumbnail((320, 180))
                preview = io.BytesIO()
                picture.save(preview, 'PNG')
            selected.append({'photo_name': path.name, 'preview_data': base64.b64encode(preview.getvalue()).decode('ascii')})
            if len(selected) == count:
                break
        if not selected:
            raise PhotoError('随机配图文件夹中没有可用图片；请放入 JPG、PNG、WebP 或 HEIC 等图片后重试')
        (pending / 'selection.json').write_text(json.dumps(stored, ensure_ascii=False), encoding='utf-8')
        pending.replace(SELECTIONS / identifier)
    except (PhotoError, OSError) as exc:
        shutil.rmtree(pending, ignore_errors=True)
        if isinstance(exc, PhotoError):
            raise
        raise PhotoError('无法保存随机图片副本，请检查本机磁盘空间和文件夹权限') from None
    message = (f'已随机选择 {count} 张图片' if len(selected) == count else
               f'可用图片不足 {count} 张，已使用全部 {len(selected)} 张')
    if skipped:
        message += f'；已跳过 {len(skipped)} 个损坏、过大或无法读取的图片文件'
    return {'selection_id': identifier, 'photos': selected, 'total_bytes': total, 'requested_count': count, 'candidate_count': len(paths),
            'skipped': skipped, 'message': message + '；将平均分配完整音频时长'}


def photos(identifier):
    """读取本次抽样的冻结副本，图库改动不会使预览和实际使用的图片不一致。"""
    if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{32}', identifier):
        raise PhotoError('随机图片选择无效，请重新随机选择')
    directory = SELECTIONS / identifier
    try:
        if directory.is_symlink():
            raise ValueError()
        stored = json.loads((directory / 'selection.json').read_text(encoding='utf-8'))
        if not isinstance(stored, list) or not 1 <= len(stored) <= MAX_BATCH_COUNT:
            raise ValueError()
        selected = []
        for item in stored:
            filename = item['filename']
            path = directory / filename
            if Path(filename).name != filename or path.is_symlink():
                raise ValueError()
            with path.open('rb') as source:
                raw = source.read(MAX_BYTES + 1)
            if hashlib.sha256(raw).hexdigest() != item['sha256']:
                raise ValueError()
            selected.append({'photo_name': item['photo_name'], '_photo_bytes': raw})
        return local_photos({'photos': selected})
    except (OSError, ValueError, KeyError, TypeError):
        raise PhotoError('随机图片副本缺失或已改变，请重新随机选择') from None
