"""保存用户已有音频，保持原文件；媒体转换由可暂停的任务执行。"""
from pathlib import Path
from photo_import import MAX_REQUEST_BYTES as PHOTO_REQUEST_BYTES

MAX_BYTES = 500 * 1024 * 1024
# 单个音频 500 MB，加上照片的 Base64/JSON 预算及表单开销。
MAX_REQUEST_BYTES = MAX_BYTES + PHOTO_REQUEST_BYTES + 8 * 1024 * 1024
MAX_FORM_MEMORY_BYTES = PHOTO_REQUEST_BYTES
EXTENSIONS = {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.aiff', '.aif', '.opus', '.wma'}


def save_upload(upload, directory):
    if upload is None or not upload.filename:
        raise ValueError('请选择已有音频文件')
    name = Path(upload.filename.replace('\\', '/')).name
    suffix = Path(name).suffix.lower()
    if suffix not in EXTENSIONS:
        raise ValueError('请选择 MP3、WAV、M4A、AAC、FLAC、OGG、AIFF、OPUS 或 WMA 音频')
    source = Path(directory) / ('source-audio' + suffix)
    size = 0
    with source.open('wb') as output:
        while block := upload.stream.read(1024 * 1024):
            size += len(block)
            if size > MAX_BYTES:
                raise ValueError('单个音频请控制在 500 MB 以内')
            output.write(block)
    if not size:
        raise ValueError('所选音频文件为空，请重新选择')
    return source, {'source_name': name[:200], 'source_filename': source.name, 'size_bytes': size}


def source_path(directory, metadata):
    name = metadata.get('source_filename', '')
    if not isinstance(name, str) or Path(name).name != name or not name.startswith('source-audio.') or Path(name).suffix.lower() not in EXTENSIONS:
        raise ValueError('导入音频路径无效')
    path = Path(directory) / name
    if path.is_symlink() or not path.is_file() or path.resolve().parent != Path(directory).resolve():
        raise ValueError('导入的原始音频已被移走，请选择文件创建新任务')
    return path
