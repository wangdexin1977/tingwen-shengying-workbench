"""独立本地产品：文案 → MP3 + 自选照片或 AI 配图 → 用户确认 → MP4。"""
import argparse
import base64
import io
import asyncio
import hashlib
import json
import os
import re
import secrets
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
import tempfile
from pathlib import Path
from scripts.make_homepage_bg import normalize_title, title_character_count, unified_title, normalize_title_settings, TITLE_FONTS, draw_yellow_title

from flask import Flask, abort, jsonify, make_response, render_template, request, send_file, url_for
from werkzeug.exceptions import NotFound

from seedream import MODELS, SeedreamError, SubmissionUnknown
from drawthings import DrawThingsClient, DrawThingsError
from image_sources import SOURCES, client_for
from speech import Cancelled, VOICES, synthesize, speech_error_message
from photo_import import PhotoError, local_photos, download_photo, public_target
import records
from PIL import Image
import subtitles
import omnivoice_local
import background_music
import audio_import
VOICE_IMPORT_LOCK = threading.Lock()
MUSIC_IMPORT_LOCK = threading.Lock()

ROOT = Path(__file__).resolve().parent
JOBS = ROOT / "jobs"
JOBS.mkdir(exist_ok=True)
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 技术保护，不设文案字符数上限
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.RLock()
CONTROLS = {}
ACTIVE = set()
STATES_BUSY = {"queued", "working"}


class Control:
    def __init__(self, key=""):
        self.key = key
        self.paused = threading.Event()
        self.cancelled = threading.Event()
        self.photo_url = ""

    def check_cancel(self):
        if self.cancelled.is_set():
            raise Cancelled("任务已取消；已完成素材保留")

    async def wait_async(self):
        self.check_cancel()
        while self.paused.is_set():
            self.check_cancel()
            await asyncio.sleep(.1)

    def sleep(self, seconds):
        if self.cancelled.wait(seconds):
            self.check_cancel()


def folder(job_id):
    try:
        if str(uuid.UUID(job_id)) != job_id:
            raise ValueError()
    except ValueError:
        abort(404)
    return JOBS / job_id


def default_narration_title(text):
    return re.sub(r'\s+', ' ', str(text or '')).strip()[:30]


def media_filename(text, extension):
    stem = default_narration_title(text)
    stem = ''.join('_' if c in '/\\:*?"<>|' or ord(c) < 32 else c for c in stem).strip(' .')
    return (stem or ('听文视频' if extension == 'mp4' else '听文朗读')) + '.' + extension


def video_filename(title, text):
    return media_filename(text, 'mp4')


def audio_path(job):
    name = job.get('audio_filename', 'audio.mp3')
    if Path(name).name != name or '\\' in name or not name.endswith('.mp3'):
        raise ValueError('音频文件名无效')
    return folder(job['id']) / name


def video_path(job):
    # 历史成品继续使用原来的位置，不自动重命名已有文件。
    name = job.get("video_filename", "video.mp4")
    if Path(name).name != name or "\\" in name or not name.endswith(".mp4"):
        raise ValueError("视频文件名无效")
    return folder(job["id"]) / name


def image_paths(job):
    if job.get("image_gallery"):
        paths = []
        for item in job["image_gallery"]:
            name = item["filename"]
            if Path(name).name != name or "\\" in name or not name.startswith("image-") or not name.endswith(".png"):
                raise ValueError("照片路径无效")
            paths.append(folder(job["id"]) / name)
        return paths
    return [folder(job["id"]) / f"image-{job['image_version']}.png"]


def image_digest(job):
    digest = hashlib.sha256()
    for path in image_paths(job):
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def read_job(job_id):
    with LOCK:
        path = folder(job_id) / "job.json"
        if not path.exists():
            abort(404)
        return json.loads(path.read_text(encoding="utf-8"))


def save(job):
    with LOCK:
        path = folder(job["id"]) / "job.json"
        pending = path.with_suffix(".pending")
        pending.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        pending.replace(path)


def update(job_id, **changes):
    with LOCK:
        job = read_job(job_id)
        job.update(changes)
        job["updated"] = time.time()
        save(job)


def message(job_id, part, text):
    update(job_id, **{f"{part}_message": text})


def probe(path):
    binary = "/opt/homebrew/bin/ffprobe" if Path("/opt/homebrew/bin/ffprobe").exists() else "ffprobe"
    result = subprocess.run([binary, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                             "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError("生成的媒体文件未通过播放检查")
    info = json.loads(result.stdout)
    if float(info.get("format", {}).get("duration", 0)) <= 0:
        raise RuntimeError("音视频时长无效")
    return info


def audio_worker(job_id, control):
    job = read_job(job_id)
    update(job_id, audio_state="working")
    if job.get("audio_provider") == "upload":
        imported_audio_worker(job, control)
    elif job.get("audio_provider") == "omnivoice":
        local_audio_worker(job, control)
    else:
        asyncio.run(synthesize(job["text"], job["voice"], folder(job_id), control,
                           lambda text: message(job_id, "audio", text), validate=probe,
                           on_progress=lambda complete, total, stage: update(job_id, audio_progress={
                               "stage": stage, "completed": complete, "total": total,
                               "percent": int(complete * 100 / total) if complete is not None and total else None})))
    control.check_cancel()
    source = folder(job_id) / 'audio.mp3'
    target = audio_path(job)
    if source != target:
        source.replace(target)
    info = probe(target)
    imported = job.get("audio_provider") == "upload"
    audio_message = "已有音频已导入，可试听并确认生成 MP4；原始文件保留在任务目录" if imported else "音频已完成，可试听或下载"
    update(job_id, audio_state="ready", audio_message=audio_message,
           audio_progress={"stage": "MP3 已完成", "percent": 100},
           audio_duration=float(info["format"]["duration"]))
    if imported:
        records.append("audio_import", job_id=job_id, part="audio", provider="upload", status="completed",
                       size_bytes=job["imported_audio"]["size_bytes"], audio_duration=float(info["format"]["duration"]))


def imported_audio_worker(job, control):
    directory = folder(job['id'])
    source = audio_import.source_path(directory, job.get('imported_audio', {}))
    pending = directory / 'audio.pending.mp3'
    binary = '/opt/homebrew/bin/ffmpeg' if Path('/opt/homebrew/bin/ffmpeg').exists() else 'ffmpeg'
    message(job['id'], 'audio', '正在本机读取、转换并验证已有音频，不调用语音模型')
    update(job['id'], audio_progress={'stage': '本机音频导入', 'percent': None})
    try:
        run_process([binary, '-v', 'error', '-xerror', '-y', '-protocol_whitelist', 'file,pipe', '-i', str(source),
                     '-map', '0:a:0', '-vn', '-map_metadata', '-1', '-c:a', 'libmp3lame', '-b:a', '192k',
                     '-ar', '44100', '-ac', '2', str(pending)], job['id'], control)
        probe(pending)
    except RuntimeError:
        pending.unlink(missing_ok=True)
        raise RuntimeError('所选音频无法完整解码或没有有效音轨，请换一个文件重新载入；原文件已保留') from None
    control.check_cancel()
    pending.replace(directory / 'audio.mp3')


def local_audio_worker(job, control):
    directory = folder(job['id']) / 'omnivoice'
    directory.mkdir(exist_ok=True)
    text_file = directory / 'narration.txt'
    text_file.write_text(job['text'], encoding='utf-8')
    config = omnivoice_local.settings(job.get('omnivoice_settings'))
    def progress(event):
        current, total = event.get('current'), event.get('total')
        percent = int(current * 100 / total) if isinstance(current, (int, float)) and isinstance(total, (int, float)) and total > 0 else None
        update(job['id'], audio_message=str(event.get('message') or 'OmniVoice 正在本机生成')[:160],
               audio_progress={'stage': 'OmniVoice 本机推理', 'completed': current, 'total': total, 'percent': percent})
    message(job['id'], 'audio', '正在加载 ' + config['backend'] + ' · ' + config['voice_name'] + '；首次加载需要等待')
    result = omnivoice_local.run({'text_file': str(text_file), 'voice_id': config['voice_id'],
        'expression': config['expression'], 'backend': config['backend'], 'parameters': config['parameters'], 'profile_snapshot': config.get('profile_snapshot'), 'output_directory': str(directory)}, directory, control, progress)
    wave = Path(result['audio_path']).resolve()
    if not wave.is_relative_to(directory.resolve()) or not wave.is_file() or not wave.stat().st_size:
        raise RuntimeError('OmniVoice 输出音频无效，已生成素材保留')
    pending = folder(job['id']) / 'audio.pending.mp3'
    binary = '/opt/homebrew/bin/ffmpeg' if Path('/opt/homebrew/bin/ffmpeg').exists() else 'ffmpeg'
    message(job['id'], 'audio', 'OmniVoice 已生成，正在转换并验证 MP3')
    run_process([binary, '-v', 'error', '-y', '-i', str(wave), '-vn', '-codec:a', 'libmp3lame', '-b:a', '128k', str(pending)], job['id'], control)
    probe(pending); control.check_cancel(); pending.replace(folder(job['id']) / 'audio.mp3')


def audio_configuration(data, job=None):
    provider = data.get('audio_provider', (job or {}).get('audio_provider', 'edge-tts'))
    if provider not in {'edge-tts', 'omnivoice'}:
        raise ValueError('语音来源无效')
    config = omnivoice_local.settings(data.get('omnivoice_settings', (job or {}).get('omnivoice_settings'))) if provider == 'omnivoice' else None
    return {'audio_provider': provider, 'omnivoice_settings': config}


def store_picture(job_id, raw, metadata, text="图片已保存，请预览确认"):
    version = uuid.uuid4().hex
    path = folder(job_id) / f"image-{version}.png"
    pending = path.with_suffix(".pending")
    pending.write_bytes(raw)
    pending.replace(path)
    update(job_id, image_state="ready", image_message=text, image_version=version,
           image_metadata=metadata, image_gallery=[], confirmed_version="", confirmed_digest="", video_state="idle",
           video_message="等待你重新确认当前照片", video_progress=None, submitting=False)


def store_gallery(job_id, items):
    version = uuid.uuid4().hex
    gallery = []
    for index, (source, item) in enumerate(items):
        name = f"image-{version}-{index}.png"
        pending = folder(job_id) / (name + ".pending")
        if isinstance(source, bytes):
            pending.write_bytes(source)
        else:
            shutil.copyfile(source, pending)
        pending.replace(folder(job_id) / name)
        gallery.append({"filename": name, "metadata": item["metadata"], "source_id": item["id"]})
    update(job_id, image_state="ready", image_message=f"已选用 {len(gallery)} 张照片，将平均分配音频时长，请预览确认",
           image_version=version, image_gallery=gallery, image_metadata=gallery[0]["metadata"],
           confirmed_version="", confirmed_digest="", video_state="idle", video_progress=None, video_message="等待你确认保留的照片", submitting=False)


def store_local_photos(job_id, photos):
    if len(photos) == 1:
        store_picture(job_id, *photos[0], text="本机照片已选用，请预览确认")
    else:
        store_gallery(job_id, [(raw, {"id": uuid.uuid4().hex, "metadata": metadata}) for raw, metadata in photos])


def image_worker(job_id, control):
    job = read_job(job_id)
    if job.get("provider") == "manual":
        control.check_cancel()
        while control.paused.is_set():
            control.sleep(.1)
        update(job_id, image_state="working", image_message="正在下载你选择的网上照片")
        try:
            raw, metadata = download_photo(control.photo_url)
            store_picture(job_id, raw, metadata)
        finally:
            control.photo_url = ""
        return
    client = client_for(job, control.key)
    control.check_cancel()
    while control.paused.is_set():
        control.sleep(.1)
    update(job_id, image_state="working")
    # 先记录提交状态；重启、超时均不自动重复收费请求。
    label = "Draw Things 正在本机生成图片" if job["provider"] == "drawthings" else "豆包正在生成 2K 图片"
    update(job_id, image_message=label + "；请稍候（不会自动重发）", submitting=job["provider"] == "seedream")
    raw, metadata = client.generate(job["prompt"], job["model"])
    metadata["provider"] = job["provider"]
    # 请求提交后无法撤销；即使用户取消，也保存已返回的收费素材。
    text = "图片已保存，请预览确认"
    if control.cancelled.is_set():
        text = "图片已完成并保留；后续处理已取消，点击继续后可确认"
    store_picture(job_id, raw, metadata, text)


def encoding_progress(path, duration):
    if not path.exists() or duration <= 0:
        return None
    with path.open("rb") as source:
        source.seek(max(0, path.stat().st_size - 8192))
        text = source.read(8192).decode("ascii", errors="ignore")
    values = re.findall(r"(?m)^out_time_us=(\d+)$", text)
    if not values:
        return None
    seconds = int(values[-1]) / 1_000_000
    return {"stage": "视频编码", "percent": min(99, max(0, int(seconds * 100 / duration)))}


def run_process(cmd, job_id, control, track=None):
    with (folder(job_id) / "render.log").open("ab") as log:
        process = subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=True)
        stopped = False
        last_progress = None
        try:
            while process.poll() is None:
                control.check_cancel()
                if control.paused.is_set() != stopped:
                    stopped = control.paused.is_set()
                    os.killpg(process.pid, signal.SIGSTOP if stopped else signal.SIGCONT)
                if track:
                    current = encoding_progress(*track)
                    if current and current != last_progress:
                        update(job_id, video_progress=current)
                        last_progress = current
                time.sleep(.15)
            if process.returncode:
                raise RuntimeError("视频合成失败，请查看本任务目录中的 render.log")
        finally:
            if process.poll() is None:
                if stopped:
                    os.killpg(process.pid, signal.SIGCONT)
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def video_worker(job_id, control):
    job = read_job(job_id)
    version = job["image_version"]
    if not version or job["confirmed_version"] != version:
        raise RuntimeError("当前图片尚未确认")
    directory = folder(job_id)
    images = image_paths(job)
    if image_digest(job) != job["confirmed_digest"]:
        raise RuntimeError("确认后的图片发生变化，请重新确认")
    update(job_id, video_state="working", video_message="正在制作首页画面",
           video_progress={"stage": "画面准备", "percent": 0, "completed": 0, "total": len(images)})
    title_settings = normalize_title_settings(job.get("title_settings"))
    covers = []
    for index, image in enumerate(images):
        control.check_cancel()
        cover = directory / ("cover.png" if index == 0 else f"cover-{index}.png")
        message(job_id, "video", f"正在制作画面 {index + 1}/{len(images)}")
        cmd = [sys.executable, str(ROOT / "scripts/make_homepage_bg.py"), "--bg", str(image),
               "--out", str(cover), "--theme=" + (job["title"] if index == 0 else ""),
               "--title-style", job.get("title_style", "legacy"),
               "--title-font", title_settings["font"], "--title-color", title_settings["color"],
               "--title-font-size", str(title_settings["size"]),
               "--title-secondary=" + (job.get("title_secondary", "") if index == 0 else ""),
               "--width", "1920", "--height", "1080", "--mode", "fit-blur"]
        run_process(cmd, job_id, control)
        covers.append(cover)
        update(job_id, video_progress={"stage": "画面准备", "percent": int((index + 1) * 100 / len(images)),
                                       "completed": index + 1, "total": len(images)})
    style = subtitles.normalize_style(job.get("subtitle_settings"), default_enabled=False)
    subtitle_args = ["--no-subtitle"]
    mode = "off"
    if style["enabled"]:
        message(job_id, "video", "正在准备口播字幕")
        timeline_voice = "omnivoice:" + job["omnivoice_settings"]["voice_id"] if job.get("audio_provider") == "omnivoice" else job["voice"]
        timeline = subtitles.build_timeline(directory, job["text"], timeline_voice, probe, force_estimated=job.get("audio_provider") in {"omnivoice", "upload"}, audio_file=audio_path(job))
        mode = timeline["mode"]
        timeline["style"] = style
        timeline_path = directory / "subtitles.json"
        timeline_path.write_text(json.dumps(timeline, ensure_ascii=False), encoding="utf-8")
        subtitle_args = ["--subtitle-timeline", str(timeline_path)]
    update(job_id, subtitle_timing=mode)
    label = "口播字幕" if mode == "speech" else "估算时间字幕" if mode == "estimated" else "无字幕"
    message(job_id, "video", f"正在合成 MP4（1920×1080，{label}）")
    progress_file = directory / "encode-progress.txt"
    progress_file.write_text("")
    update(job_id, video_progress={"stage": "视频编码", "percent": None})
    cmd = [sys.executable, str(ROOT / "scripts/compose_video.py")]
    for cover in covers:
        cmd.extend(["--bg", str(cover)])
    cmd.extend(["--audio", str(audio_path(job)), "--out", str(directory / "video.pending.mp4"), *subtitle_args,
                "--progress-file", str(progress_file)])
    music = background_music.normalize_settings(job.get("music_settings"))
    music_entry = None
    if music["enabled"] and music["volume"] > 0:
        music_entry = background_music.entry(music["track_id"])
        message(job_id, "video", "正在准备背景音乐并合成 MP4")
        cmd.extend(["--music", str(music_entry["path"]), "--music-volume", str(music["volume"]),
                    "--music-loop-seconds", str(music["loop_seconds"])])
    run_process(cmd, job_id, control, track=(progress_file, job["audio_duration"]))
    control.check_cancel()
    update(job_id, video_progress={"stage": "验证画面与音轨", "percent": None})
    info = probe(directory / "video.pending.mp4")
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if not video or not audio or (video["width"], video["height"]) != (1920, 1080):
        raise RuntimeError("视频画面或音轨验证失败")
    if abs(float(info["format"]["duration"]) - job["audio_duration"]) > 1.5:
        raise RuntimeError("视频与音频时长不匹配")
    (directory / "video.pending.mp4").replace(video_path(job))
    music_label = f" · 背景音乐 {music_entry['name']}（{music['volume']}%）" if music_entry else ""
    update(job_id, video_state="done", video_message=f"视频已完成 · {len(images)} 张照片平均播放 · {label}{music_label}",
           rendered_subtitle_settings=style, rendered_title_settings=title_settings,
           rendered_music_settings=music,
           video_progress={"stage": "MP4 已完成", "percent": 100},
           video_duration=float(info["format"]["duration"]), image_durations=[job["audio_duration"] / len(images)] * len(images))


WORKERS = {"audio": audio_worker, "image": image_worker, "video": video_worker}


def launch(job_id, part):
    with LOCK:
        identity = (job_id, part)
        if identity in ACTIVE:
            return
        ACTIVE.add(identity)
        control = CONTROLS.setdefault(job_id, Control())

    def worker():
        sleep_guard = subprocess.Popen(['/usr/bin/caffeinate', '-i']) if part in {'audio', 'video'} and Path('/usr/bin/caffeinate').exists() else None
        started = time.monotonic()
        call_id = uuid.uuid4().hex
        job = read_job(job_id)
        provider = job.get("audio_provider", "edge-tts") if part == "audio" else job.get("provider", "")
        model = (job.get("omnivoice_settings") or {}).get("voice_name", job["voice"]) if part == "audio" else job.get("model", "")
        if part == "audio" and provider == "upload":
            model = ""
        is_model = (part == "audio" and provider != "upload") or (part == "image" and provider in SOURCES)
        event = "model_call" if is_model else "processing"
        records.append(event, job_id=job_id, call_id=call_id, part=part, status="started", provider=provider, model=model)
        outcome = "completed"
        try:
            WORKERS[part](job_id, control)
        except Cancelled:
            outcome = "cancelled"
            update(job_id, **{f"{part}_state": "cancelled"})
        except Exception as exc:
            outcome = "failed"
            text = speech_error_message(exc) if part == "audio" else str(exc)
            if control.key:
                text = text.replace(control.key, "[密钥已隐藏]")
            changes = {f"{part}_state": "failed", f"{part}_message": text}
            if part == "image" and read_job(job_id).get("provider") == "seedream":
                changes["submitting"] = (isinstance(exc, SubmissionUnknown) or
                                          (not isinstance(exc, SeedreamError) and read_job(job_id).get("submitting", False)))
            update(job_id, **changes)
        finally:
            if sleep_guard is not None:
                sleep_guard.terminate()
                sleep_guard.wait(timeout=5)
            records.append(event, job_id=job_id, call_id=call_id, part=part, status=outcome, provider=provider, model=model,
                           duration_ms=round((time.monotonic() - started) * 1000))
            with LOCK:
                ACTIVE.discard(identity)
                # 清除取消状态任务的内存密钥。
                if control.cancelled.is_set() and not any(j == job_id for j, _ in ACTIVE):
                    control.key = ""
    threading.Thread(target=worker, daemon=True, name=f"{part}-{job_id[:8]}").start()


@app.before_request
def guard():
    if request.path == "/api/jobs/import-audio":
        request.max_content_length = audio_import.MAX_REQUEST_BYTES
        request.max_form_memory_size = audio_import.MAX_FORM_MEMORY_BYTES
    if request.host.split(":")[0] not in {"127.0.0.1", "localhost"}:
        abort(403, "仅允许从本机工作台访问，请使用 http://127.0.0.1:8092/ 打开")
    if request.method == "POST" or request.path == "/api/session":
        origin = request.headers.get("Origin")
        if (origin and origin != f"{request.scheme}://{request.host}") or request.headers.get("Sec-Fetch-Site") == "cross-site":
            abort(403, "请求不是来自当前工作台页面。请通过桌面启动器或 http://127.0.0.1:8092/ 打开，不要直接打开 HTML 文件")
    if request.method == "POST":
        supplied = request.headers.get("X-Local-Token", "")
        if not secrets.compare_digest(supplied.encode("utf-8"), TOKEN.encode("ascii")):
            # 在进入任何任务/模型接口前拒绝，客户端仅可对此明确拒绝恢复一次。
            response = jsonify(error="本机服务已更新，页面会话已过期；请恢复连接后重试，文案和照片选择无需清空",
                               code="local_session_expired" if supplied else "local_session_missing")
            response.headers["Cache-Control"] = "no-store"
            return response, 403
        if not request.is_json and not (request.path == "/api/jobs/import-audio" and request.mimetype == "multipart/form-data"):
            abort(415)


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(409)
@app.errorhandler(413)
def error(exc):
    if exc.code == 413:
        if request.path == "/api/jobs/import-audio":
            return jsonify(error="上传请求或素材设置超过导入限制：单个音频最多 500 MB，照片合计最多 40 MB；请减少本次素材或文案后重试"), 413
        return jsonify(error="上传内容超过本机服务的 64 MB 请求限制，请减少照片或压缩后重试"), 413
    return jsonify(error=getattr(exc, "description", "请求失败")), exc.code


@app.get("/")
def index():
    response = make_response(render_template("index.html", voices=VOICES, token=TOKEN, models=MODELS, sources=SOURCES, subtitle_fonts=subtitles.FONTS, title_fonts=TITLE_FONTS, version=records.VERSION))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get('/api/music')
def music_library():
    return jsonify(tracks=background_music.catalog(), defaults=background_music.DEFAULTS)


@app.get('/api/music/<track_id>/preview')
def music_preview(track_id):
    try:
        item = background_music.entry(track_id)
    except ValueError as exc:
        abort(404, str(exc))
    return send_file(item['path'], max_age=0)


@app.post('/api/music')
def add_music():
    data = request.get_json()
    with MUSIC_IMPORT_LOCK:
        try:
            item = background_music.import_audio(data.get('filename'), data.get('audio_data'))
        except ValueError as exc:
            abort(400, str(exc))
    return jsonify(track=item), 201


@app.get('/api/omnivoice')
def local_voice_capabilities():
    return jsonify(omnivoice_local.capabilities())


@app.get('/api/omnivoice/voices/<voice_id>/preview')
def local_voice_preview(voice_id):
    try: entry = omnivoice_local.library_entry(voice_id)
    except ValueError: abort(404)
    return send_file(entry['preview_path'], max_age=0)


@app.post('/api/omnivoice/voices')
def add_local_voice():
    if ACTIVE:
        abort(409, '请等待当前任务完成后再添加音色')
    data = request.get_json()
    name = str(data.get('name', '')).strip()
    transcript = str(data.get('reference_text', '')).strip()
    suffix = Path(str(data.get('filename', ''))).suffix.lower()
    if not name or len(name) > 100:
        abort(400, '请填写 1–100 字的音色名称')
    if suffix not in {'.wav', '.mp3', '.m4a', '.flac', '.aiff', '.ogg'}:
        abort(400, '请选择 WAV、MP3、M4A、FLAC、AIFF 或 OGG 音频')
    if not omnivoice_local.capabilities()['available']:
        abort(400, '本机 OmniVoice 尚未就绪')
    try: raw = base64.b64decode(data.get('audio_data', ''), validate=True)
    except (ValueError, TypeError): abort(400, '音频数据无效')
    if not raw or len(raw) > 20 * 1024 * 1024:
        abort(400, '参考音频不能为空，且应小于 20 MB')
    if not VOICE_IMPORT_LOCK.acquire(blocking=False):
        abort(409, '另一个音色正在保存，请稍候')
    try:
        with tempfile.TemporaryDirectory(prefix='tingwen-voice-') as temporary:
            directory = Path(temporary); source = directory / ('reference' + suffix); source.write_bytes(raw)
            try: result = omnivoice_local.run({'operation': 'import', 'file': str(source), 'name': name, 'reference_text': transcript}, directory)
            except (RuntimeError, OSError) as exc: abort(400, str(exc))
    finally:
        VOICE_IMPORT_LOCK.release()
    return jsonify(**result, voices=omnivoice_local.capabilities()['voices'])


@app.post("/api/title/preview")
def title_preview():
    data = request.get_json()
    try:
        settings = normalize_title_settings(data.get("settings"))
        title = normalize_title(data.get("title", ""))
        if title_character_count(title) > 30:
            abort(400, "视频标题最多 30 字")
        canvas = Image.new("RGBA", (1920, 1080))
        layout = draw_yellow_title(canvas, title, settings)
    except ValueError as exc:
        abort(400, str(exc))
    output = io.BytesIO()
    canvas.resize((960, 540), Image.Resampling.LANCZOS).save(output, format="PNG")
    return jsonify(image="data:image/png;base64," + base64.b64encode(output.getvalue()).decode(),
                   actual_size=layout["font_size"] if layout else 0)


@app.post("/api/subtitles/preview")
def subtitle_preview():
    data = request.get_json()
    try:
        style = subtitles.normalize_style(data.get("settings"))
    except ValueError as exc:
        abort(400, str(exc))
    text = str(data.get("text", "口播字幕效果预览"))[:80]
    output = io.BytesIO()
    subtitles.preview_image(style, text).save(output, format="PNG")
    return jsonify(image="data:image/png;base64," + base64.b64encode(output.getvalue()).decode())


@app.get("/api/session")
def local_session():
    # 浏览器同源限制 + guard 的来源校验；不放开 CORS，不将校验码写入磁盘。
    response = jsonify(product="tingwen-shengying-workbench", token=TOKEN)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
def health():
    return jsonify(product="tingwen-shengying-workbench", version=records.VERSION, image_providers=list(SOURCES), name="听文声影工作台")


@app.get("/api/records")
def record_list():
    kind = request.args.get("kind", "all")
    if kind not in {"all", "models"}:
        abort(400, "记录类型无效")
    try:
        offset = max(0, int(request.args.get("offset", "0")))
    except ValueError:
        abort(400, "分页参数无效")
    return jsonify(version=records.VERSION, releases=records.RELEASES,
                   log_path=str(records.LOG_FILE), warning="日志无法写入磁盘，请检查目录权限或剩余空间" if records.WRITE_ERROR else "",
                   **records.entries(kind, offset))


@app.get("/api/records/export")
def record_export():
    # 本机导出；不上传或分享给任何平台。
    content = {"version": records.VERSION, "releases": records.RELEASES,
               "note": "历史版本为整理记录；自动运行和模型记录从 1.5 开始。无密钥、完整文案或远程网址。",
               **records.entries(limit=10**9)}
    response = jsonify(content)
    response.headers["Content-Disposition"] = 'attachment; filename="tingwen-records.json"'
    return response


@app.get("/api/jobs")
def jobs():
    items = []
    for path in JOBS.glob("*/job.json"):
        job = json.loads(path.read_text(encoding="utf-8"))
        items.append({k: job.get(k) for k in ("id", "title", "created", "audio_state", "image_state", "video_state")})
    return jsonify(jobs=sorted(items, key=lambda j: j["created"], reverse=True))


def credential(data, job=None):
    key = str(data.get("api_key", "")).strip() or os.environ.get("ARK_API_KEY", "").strip()
    if not key:
        abort(400, "请在豆包设置中填写火山方舟 API Key")
    if "\n" in key or "\r" in key or len(key) > 2048:
        abort(400, "API Key 格式无效")
    if data.get("payment_consent") is not True:
        abort(400, "请先勾选同意豆包按量收费；每次重生成都会产生新请求")
    if job and job.get("provider") != "seedream":
        abort(400, "旧 AI Horde 任务仅保留查看与本地合成；请创建新的豆包任务")
    return key


def image_configuration(data, provider):
    if provider not in SOURCES:
        abort(400, "图片来源无效")
    if provider == "seedream":
        model = data.get("model", MODELS[0])
        if model not in MODELS:
            abort(400, "豆包模型无效")
        return {"provider": provider, "model": model}, credential(data)
    try:
        snapshot = DrawThingsClient(data.get("drawthings_port", 7861)).inspect()
    except DrawThingsError as exc:
        abort(400, str(exc))
    return {"provider": provider, "model": snapshot["model"], "drawthings_port": snapshot["port"],
            "image_settings": snapshot["settings"]}, ""


def archive_video_path(job_id, filename):
    directory = folder(job_id)
    if (not isinstance(filename, str) or Path(filename).name != filename or "\\" in filename
            or not filename.lower().endswith(".mp4") or filename == "video.pending.mp4"):
        abort(404)
    path = directory / filename
    # 只允许产品任务目录内的成片，拒绝符号链接与未完成的临时文件。
    if directory.is_symlink() or path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        abort(404)
    return path


@app.get("/api/videos")
def all_videos():
    videos = []
    for path in JOBS.glob("*/*.mp4"):
        try:
            path = archive_video_path(path.parent.name, path.name)
            info = path.stat()
        except (OSError, NotFound):
            # 无效目录、临时文件和读取期间移走的文件不属于可播放成片。
            continue
        videos.append({"job_id": path.parent.name, "filename": path.name,
                       "path": str(path), "output_folder": str(path.parent),
                       "size_bytes": info.st_size, "modified": info.st_mtime,
                       "media_url": url_for("archive_video_media", job_id=path.parent.name, filename=path.name)})
    videos.sort(key=lambda item: (item["modified"], item["filename"]), reverse=True)
    return jsonify(videos=videos, total=len(videos), root_folder=str(JOBS))


@app.get("/api/videos/<job_id>/media")
def archive_video_media(job_id):
    path = archive_video_path(job_id, request.args.get("filename", ""))
    return send_file(path, mimetype="video/mp4", conditional=True,
                     as_attachment=request.args.get("download") == "1", download_name=path.name)


@app.post("/api/videos/<job_id>/reveal")
def reveal_archived_video(job_id):
    path = archive_video_path(job_id, request.get_json().get("filename", ""))
    subprocess.Popen(["open", "-R", str(path)])
    return jsonify(ok=True)


@app.post("/api/videos/reveal-root")
def reveal_all_videos():
    subprocess.Popen(["open", str(JOBS)])
    return jsonify(ok=True, root_folder=str(JOBS))


@app.post("/api/drawthings/check")
def check_drawthings():
    config, _ = image_configuration(request.get_json(), "drawthings")
    return jsonify(config)


@app.post("/api/jobs")
def create():
    data = request.get_json()
    text = str(data.get("text", "")).strip()
    prompt = str(data.get("prompt", "")).strip()
    title = unified_title(data.get("title", ""), data.get("title_secondary", ""))
    if not title and data.get('title_enabled', bool(title)):
        title = default_narration_title(text)
    if not data.get('title_enabled', bool(title)):
        title = ''
    if title_character_count(title) > 30:
        abort(400, "视频标题最多 30 字，请缩短后再生成")
    try:
        title_settings = normalize_title_settings(data.get("title_settings"))
        subtitle_settings = subtitles.normalize_style(data.get("subtitle_settings"))
        music_settings = background_music.normalize_settings(data.get("music_settings"))
        if music_settings["enabled"]:
            background_music.entry(music_settings["track_id"])
        audio_config = audio_configuration(data)
    except ValueError as exc:
        abort(400, str(exc))
    mode = data.get("image_mode", "generate")
    if mode not in {"generate", "upload", "url", "none"}:
        abort(400, "照片选择方式无效")
    if not text or (mode == "generate" and not prompt):
        abort(400, "请填写文案；使用 AI 生图时还需填写图片要求")
    voice = data.get("voice", VOICES[0][0])
    if voice not in {v[0] for v in VOICES}:
        abort(400, "音色无效")
    photos = None
    photo_url = ""
    if mode == "generate":
        config, key = image_configuration(data, data.get("provider", "seedream"))
    else:
        config, key = {"provider": "manual", "model": "", "image_mode": mode}, ""
        try:
            if mode == "upload":
                photos = local_photos(data)
            elif mode == "url":
                photo_url = str(data.get("photo_url", "")).strip()
                public_target(photo_url)
        except PhotoError as exc:
            abort(400, str(exc))
    with LOCK:
        if ACTIVE:
            abort(409, "请等待当前生成任务完成，或取消后再创建新任务")
        job_id = str(uuid.uuid4())
        folder(job_id).mkdir()
        job = {"id": job_id, "created": time.time(), "title": title,
               "title_secondary": "", "title_style": "clean-yellow",
               "subtitle_settings": subtitle_settings, "title_settings": title_settings,
               "music_settings": music_settings,
               "video_filename": video_filename(title, text), "audio_filename": media_filename(text, "mp3"),
               "text": text, "prompt": prompt, "voice": voice, **audio_config, **config,
               "paused": False, "remote_id": "", "submitting": False,
               "image_version": "", "confirmed_version": "", "confirmed_digest": "",
               "audio_state": "queued", "image_state": "idle" if mode == "none" else "queued", "video_state": "idle",
               "audio_message": "等待音频生成", "image_message": "仅生成音频；可稍后添加照片制作 MP4" if mode == "none" else "等待提交图片", "video_message": "等待你确认素材"}
        save(job)
        CONTROLS[job_id] = Control(key)
        CONTROLS[job_id].photo_url = photo_url
        if photos:
            store_local_photos(job_id, photos)
        records.append("create_task", job_id=job_id, status="created", count=len(photos) if photos else 1)
        launch(job_id, "audio")
        if not photos and mode != "none":
            launch(job_id, "image")
    return jsonify(job_view(read_job(job_id))), 201


@app.post('/api/jobs/import-audio')
def create_from_audio():
    try:
        data = json.loads(request.form.get('metadata', '{}'))
        if not isinstance(data, dict):
            raise ValueError('素材设置无效')
        text = str(data.get('text', '')).strip()
        title = unified_title(data.get('title', ''), '')
        if title_character_count(title) > 30:
            raise ValueError('视频标题最多 30 字，请缩短后再载入')
        title_settings = normalize_title_settings(data.get('title_settings'))
        style = subtitles.normalize_style(data.get('subtitle_settings'), default_enabled=False)
        if not text:
            style['enabled'] = False
        music = background_music.normalize_settings(data.get('music_settings'))
        if music['enabled']:
            background_music.entry(music['track_id'])
        photos = local_photos(data)
        with tempfile.TemporaryDirectory(prefix='tingwen-audio-import-') as temporary:
            source, metadata = audio_import.save_upload(request.files.get('audio'), temporary)
            with LOCK:
                if ACTIVE:
                    abort(409, '请等待当前任务完成或取消后再载入新素材')
                job_id = str(uuid.uuid4())
                directory = folder(job_id)
                directory.mkdir()
                shutil.copyfile(source, directory / metadata['source_filename'])
                job = {'id': job_id, 'created': time.time(), 'title': title, 'title_secondary': '',
                       'title_style': 'clean-yellow', 'title_settings': title_settings, 'subtitle_settings': style,
                       'music_settings': music, 'video_filename': video_filename(title, text or Path(metadata['source_name']).stem),
                       'audio_filename': media_filename(text or Path(metadata['source_name']).stem, 'mp3'),
                       'text': text, 'prompt': '', 'voice': VOICES[0][0], 'audio_provider': 'upload',
                       'omnivoice_settings': None, 'imported_audio': metadata,
                       'provider': 'manual', 'model': '', 'image_mode': 'upload', 'paused': False,
                       'remote_id': '', 'submitting': False, 'image_version': '', 'confirmed_version': '', 'confirmed_digest': '',
                       'audio_state': 'queued', 'image_state': 'queued', 'video_state': 'idle',
                       'audio_message': '等待导入已有音频', 'image_message': '等待选用照片', 'video_message': '等待你确认素材'}
                save(job)
                CONTROLS[job_id] = Control()
                store_local_photos(job_id, photos)
                records.append('create_task', job_id=job_id, provider='upload', status='created', count=len(photos))
                launch(job_id, 'audio')
    except (ValueError, PhotoError) as exc:
        abort(400, str(exc))
    return jsonify(job_view(read_job(job_id))), 201


def job_view(job):
    directory = folder(job["id"])
    # 换图/重试可能将状态重置，但不应隐藏磁盘上已经保存的成片入口。
    try:
        saved_video = archive_video_path(job["id"], video_path(job).name)
    except (NotFound, OSError, ValueError):
        saved_video = None
    gallery = [{"index": index, "metadata": item["metadata"],
                "preview_url": f"/api/jobs/{job['id']}/images/{index}?v={job['image_version']}"}
               for index, item in enumerate(job.get("image_gallery", []))]
    return {**job, "audio_message": speech_error_message(job.get("audio_message", "")), "gallery": gallery, "output_folder": str(directory),
            "audio_path": str(audio_path(job)) if job["audio_state"] == "ready" else "",
            "video_path": str(saved_video) if saved_video else "",
            "video_url": url_for("archive_video_media", job_id=job["id"], filename=saved_video.name) if saved_video else ""}


@app.get("/api/jobs/<job_id>")
def status(job_id):
    return jsonify(job_view(read_job(job_id)))


@app.post("/api/jobs/<job_id>/use-image")
def use_image(job_id):
    data = request.get_json()
    identity = (job_id, "image-import")
    with LOCK:
        job = read_job(job_id)
        if any(j != job_id or p != "audio" for j, p in ACTIVE):
            abort(409, "图片或视频仍在处理，请等待处理完成再选用照片")
        control = CONTROLS.setdefault(job_id, Control())
        if control.cancelled.is_set() and (job_id, "audio") in ACTIVE:
            abort(409, "取消尚未完成，请稍后再选择照片")
        ACTIVE.add(identity)
    try:
        mode = data.get("image_mode")
        if mode == "upload":
            photos = local_photos(data)
        elif mode == "url":
            raw, metadata = download_photo(str(data.get("photo_url", "")).strip())
        else:
            abort(400, "请选择本机照片或粘贴图片网址")
        with LOCK:
            if control.cancelled.is_set() and not job.get("cancelled"):
                abort(409, "照片选用已取消，原有照片保留")
            if mode == "upload":
                store_local_photos(job_id, photos)
            else:
                store_picture(job_id, raw, metadata, "已选用你的照片，请预览并重新确认")
            records.append("replace_photos", job_id=job_id, status="completed", count=len(photos) if mode == "upload" else 1)
            # 手动换图不代表上次收费请求已明确完成，保留费用风险提示。
            if job.get("submitting"):
                update(job_id, submitting=True)
            if control.cancelled.is_set():
                CONTROLS[job_id] = Control(control.key)
            update(job_id, cancelled=False)
        return jsonify(job_view(read_job(job_id)))
    except PhotoError as exc:
        abort(400, str(exc))
    finally:
        with LOCK:
            ACTIVE.discard(identity)


@app.post("/api/jobs/<job_id>/select-images")
def select_images(job_id):
    data = request.get_json()
    with LOCK:
        job = read_job(job_id)
        if any(j != job_id or part != "audio" for j, part in ACTIVE):
            abort(409, "照片或视频仍在处理，请稍后筛选")
        gallery = job.get("image_gallery", [])
        indices = data.get("selected_indices")
        if not isinstance(indices, list) or not indices or any(type(i) is not int or i < 0 or i >= len(gallery) for i in indices) or len(set(indices)) != len(indices):
            abort(400, "请至少保留一张照片")
        if data.get("image_version") != job["image_version"]:
            abort(409, "照片版本已改变，请重新查看")
        if indices == list(range(len(gallery))):
            return jsonify(job_view(job))
        chosen = [item for i, item in enumerate(gallery) if i in indices]
        update(job_id, image_gallery=chosen, image_version=uuid.uuid4().hex, image_metadata=chosen[0]["metadata"],
               image_message=f"保留 {len(chosen)} 张照片，请重新确认", confirmed_version="", confirmed_digest="",
               video_state="idle", video_progress=None, video_message="照片选择改变，请重新确认后合成")
        records.append("select_photos", job_id=job_id, status="completed", count=len(chosen))
        return jsonify(job_view(read_job(job_id)))


@app.get("/api/jobs/<job_id>/images/<int:index>")
def gallery_image(job_id, index):
    job = read_job(job_id)
    paths = image_paths(job)
    if index >= len(paths):
        abort(404)
    return send_file(paths[index], max_age=0)


@app.post("/api/jobs/<job_id>/<action>")
def action(job_id, action):
    with LOCK:
        job = read_job(job_id)
        control = CONTROLS.setdefault(job_id, Control())
        data = request.get_json()
        if action == "pause":
            control.paused.set()
            update(job_id, paused=True)
        elif action == "resume":
            if control.cancelled.is_set():
                if any(j == job_id for j, _ in ACTIVE):
                    abort(409, "取消尚未结束，请等待云端返回或本机处理停止")
                control = Control(control.key)
                CONTROLS[job_id] = control
            control.paused.clear()
            update(job_id, paused=False, cancelled=False)
        elif action == "cancel":
            control.cancelled.set()
            update(job_id, paused=False, cancelled=True)
            for part in WORKERS:
                if (job_id, part) not in ACTIVE and job[f"{part}_state"] in STATES_BUSY:
                    update(job_id, **{f"{part}_state": "cancelled"})
        elif action in {"retry-audio", "retry-image", "regenerate", "change-source"}:
            if any(j != job_id for j, _ in ACTIVE):
                abort(409, "另一个任务正在运行")
            part = "audio" if action == "retry-audio" else "image"
            if (job_id, part) in ACTIVE or (job_id, "video") in ACTIVE:
                abort(409, "该步骤仍在运行，请等待或先取消")
            if (job_id, "image-import") in ACTIVE:
                abort(409, "照片正在选用，请稍后再试")
            if control.cancelled.is_set():
                if any(j == job_id for j, _ in ACTIVE):
                    abort(409, "取消尚未完成，请稍后重试")
                control = Control(control.key)
                CONTROLS[job_id] = control
            if part == "image":
                provider = data.get("provider") if action == "change-source" else job.get("provider")
                if provider not in SOURCES:
                    abort(400, "旧 AI Horde 任务不再联网生成；请明确选择新的图片来源")
                if job.get("submitting") and provider == "seedream" and not data.get("confirm_resubmit"):
                    abort(409, "上次收费请求结果未知；请明确同意重发，可能重复扣费")
                if provider == "drawthings" and action != "change-source":
                    try:
                        DrawThingsClient(job["drawthings_port"]).inspect()
                    except DrawThingsError as exc:
                        abort(400, str(exc))
                    config = {k: job[k] for k in ("provider", "model", "drawthings_port", "image_settings")}
                    key = ""
                else:
                    config, key = image_configuration({**data, "model": data.get("model", job["model"]) if action == "change-source" else job["model"]}, provider)
                if action in {"regenerate", "change-source"}:
                    prompt = str(data.get("prompt", "")).strip()
                    if not prompt:
                        abort(400, "请输入新的图片要求")
                    update(job_id, prompt=prompt, remote_id="", submitting=False, **config)
                else:
                    update(job_id, **config)
                control.key = key
            if part == "audio":
                if job.get('audio_provider') == 'upload' and data.get('audio_provider', 'upload') != 'upload':
                    abort(400, '已有音频任务只重新处理保存的原文件；生成新语音请创建新任务')
                try: config = {} if job.get('audio_provider') == 'upload' else audio_configuration(data, job)
                except ValueError as exc: abort(400, str(exc))
                update(job_id, **config)
            control.paused.clear()
            update(job_id, paused=False, cancelled=False, confirmed_version="", confirmed_digest="", video_state="idle",
                   video_message="等待你重新确认素材", video_progress=None,
                   **{f"{part}_state": "queued", f"{part}_progress": None, f"{part}_message": "等待重新生成；已完成素材保留"})
            launch(job_id, part)
        elif action == "confirm":
            if job["audio_state"] != "ready" or job["image_state"] != "ready":
                abort(409, "音频与图片必须都完成才能合成")
            if job["paused"] or job.get("cancelled") or control.cancelled.is_set():
                abort(409, "请先继续任务或重试已取消步骤")
            if data.get("image_version") != job["image_version"]:
                abort(409, "图片版本已改变，请重新预览确认")
            try:
                style = subtitles.normalize_style(data.get("subtitle_settings", job.get("subtitle_settings")), default_enabled=False)
                if style['enabled'] and not job.get('text'):
                    abort(400, '本任务没有字幕文案，请关闭口播字幕；如需字幕，填写对应文案后重新载入素材')
                title_settings = normalize_title_settings(data.get("title_settings", job.get("title_settings")))
                music_settings = background_music.normalize_settings(data.get("music_settings", job.get("music_settings")))
                if music_settings["enabled"]:
                    background_music.entry(music_settings["track_id"])
            except ValueError as exc:
                abort(400, str(exc))
            if job["video_state"] in {"queued", "working"}:
                return jsonify(job_view(job))
            previous = job.get("rendered_subtitle_settings", subtitles.normalize_style(job.get("subtitle_settings"), default_enabled=False))
            previous_music = background_music.normalize_settings(job.get("rendered_music_settings", job.get("music_settings")))
            if job["video_state"] == "done" and style == previous and music_settings == previous_music and job.get("title_style") == "clean-yellow" and title_settings == normalize_title_settings(job.get("rendered_title_settings", job.get("title_settings"))):
                return jsonify(job_view(job))
            if ACTIVE:
                abort(409, "还有其他步骤正在运行，请稍后确认")
            merged_title = unified_title(job["title"], job.get("title_secondary", ""))
            if title_character_count(merged_title) > 30:
                abort(400, "历史标题超过 30 字，请新建任务并缩短标题")
            digest = image_digest(job)
            update(job_id, title=merged_title, title_secondary="", title_style="clean-yellow", title_settings=title_settings, confirmed_version=job["image_version"], confirmed_digest=digest,
                   video_state="queued", subtitle_settings=style, music_settings=music_settings, video_progress=None, video_message="已确认图片、标题、字幕与背景音乐设置，等待合成")
            launch(job_id, "video")
        elif action == "reveal":
            kind = data.get("kind")
            if kind is None:
                subprocess.Popen(["open", str(folder(job_id))])
            elif kind in {"audio", "video"}:
                if kind == "video":
                    try:
                        path = archive_video_path(job_id, video_path(job).name)
                    except (NotFound, OSError, ValueError):
                        abort(409, "该文件尚未生成完成或已被移走")
                else:
                    path = audio_path(job)
                    if job["audio_state"] != "ready" or not path.is_file():
                        abort(409, "该文件尚未生成完成")
                subprocess.Popen(["open", "-R", str(path)])
            else:
                abort(400, "文件类型无效")
        else:
            abort(404)
        records.append("task_action", job_id=job_id, part=action, status="accepted")
        return jsonify(job_view(read_job(job_id)))


@app.get("/api/jobs/<job_id>/media/<kind>")
def media(job_id, kind):
    job = read_job(job_id)
    if kind == "image" and job["image_version"]:
        path = image_paths(job)[0]
        extension = "png"
    elif kind == "audio" and job["audio_state"] == "ready":
        path, extension = audio_path(job), "mp3"
    elif kind == "video" and job["video_state"] == "done":
        path, extension = video_path(job), "mp4"
    else:
        abort(404)
    filename = "".join(c for c in job["title"] if c.isalnum() or c in "-_ ") or "听文视频"
    name = path.name if kind in {"video", "audio"} else f"{filename}.{extension}"
    return send_file(path, as_attachment=request.args.get("download") == "1", download_name=name)


@app.get("/api/preview/<voice>")
def preview(voice):
    if voice not in {v[0] for v in VOICES}:
        abort(404)
    return send_file(ROOT / "assets/previews" / f"{voice}.mp3", max_age=31536000)


def recover():
    # 保留已生成素材和云端 ID。重启不自动发起新的外部请求。
    for path in JOBS.glob("*/job.json"):
        job = json.loads(path.read_text(encoding="utf-8"))
        for part in WORKERS:
            if job[f"{part}_state"] in STATES_BUSY:
                job[f"{part}_state"] = "interrupted"
                job[f"{part}_message"] = "程序重启；点击重试以恢复（已完成素材保留）"
        job["paused"] = False
        save(job)


import integration
integration.install(sys.modules[__name__])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8092)
    args = parser.parse_args()
    recover()
    production_queue.start_scheduler()
    records.append("service", status="started")
    app.run(host="127.0.0.1", port=args.port, threaded=True, debug=False, use_reloader=False)
