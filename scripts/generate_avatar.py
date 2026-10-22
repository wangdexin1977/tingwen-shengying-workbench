"""Generate a speech-driven face clip; preserve the original narration timeline.

The fixed presenter has a checked crop. MuseTalk sees the clean narration,
never the music mix. All frame times derive from absolute audio sample times.
"""
import argparse
import hashlib
import fcntl
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time
import wave


ROOT = Path(__file__).resolve().parents[1]
FPS = 25
SAMPLE_RATE = 16000


def load_core(runtime):
    # Load the small upstream neural package without the unrelated model server.
    package = runtime / "musetalk-core" / "musetalk_mlx"
    spec = importlib.util.spec_from_file_location(
        "shengying_musetalk", package / "__init__.py",
        submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    from shengying_musetalk.pipeline_mlx import MuseTalkPipeline
    return MuseTalkPipeline


def feature_windows(total_frames, fps=FPS):
    """28s of work + up to 1s context on each side fits Whisper's 30s input."""
    work = 28 * fps
    for first in range(0, total_frames, work):
        last = min(total_frames, first + work)
        yield first, last, max(0, first - fps), min(total_frames, last + fps)


def render(audio, out, runtime, presenter, metadata, progress=None, max_frames=0):
    import cv2
    import mlx.core as mx
    import numpy as np
    from PIL import Image

    started = time.monotonic()
    config = json.loads(metadata.read_text(encoding="utf-8"))
    if hashlib.sha256(presenter.read_bytes()).hexdigest() != config["sha256"]:
        raise ValueError("主播素材已改变，需要重新校准人脸位置")
    x1, y1, x2, y2 = config["face_box"]
    rgba = np.asarray(Image.open(presenter).convert("RGBA"))
    image = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2BGR)
    if not (0 <= x1 < x2 <= image.shape[1] and 0 <= y1 < y2 <= image.shape[0]):
        raise ValueError("主播人脸裁剪区域无效")
    original = image[y1:y2, x1:x2]
    blink = np.asarray(Image.open(metadata.parent / config["blink_image"]).convert("RGB"))
    if hashlib.sha256((metadata.parent / config["blink_image"]).read_bytes()).hexdigest() != config["blink_sha256"]:
        raise ValueError("主播眨眼素材已改变，需要重新校准")
    if blink.shape != image.shape:
        raise ValueError("主播眨眼素材尺寸不匹配")
    blink = cv2.cvtColor(blink[y1:y2, x1:x2], cv2.COLOR_RGB2BGR)
    if min(original.shape[:2]) < 128:
        raise ValueError("主播人脸清晰度不足")
    out.parent.mkdir(parents=True, exist_ok=True)
    wav = out.with_suffix(".input.wav")
    ffmpeg = "/opt/homebrew/bin/ffmpeg" if Path("/opt/homebrew/bin/ffmpeg").exists() else shutil.which("ffmpeg")
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(audio),
                    "-vn", "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(wav)], check=True)
    with wave.open(str(wav), "rb") as source:
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768
    if not len(samples):
        raise ValueError("音频为空，无法生成口型")
    count = math.ceil(len(samples) * FPS / SAMPLE_RATE)
    if max_frames:
        count = min(count, max_frames)

    def report(done, stage):
        data = {"stage": stage, "completed": done, "total": count,
                "percent": round(done * 100 / count), "elapsed_seconds": round(time.monotonic() - started, 3)}
        print(json.dumps(data, ensure_ascii=False), flush=True)
        if progress:
            pending = progress.with_suffix(".pending")
            pending.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            pending.replace(progress)

    report(0, "加载口型模型")
    mx.set_cache_limit(512 * 1024 ** 2)
    pipeline = load_core(runtime).from_pretrained_mlx(runtime / "weights-mlx").astype(mx.float16)
    from shengying_musetalk.whisper.log_mel import log_mel_spectrogram
    crop = cv2.resize(original, (256, 256), interpolation=cv2.INTER_LANCZOS4)
    latent = pipeline.get_latents_for_unet(crop).astype(mx.float16)
    mx.eval(latent)
    height, width = original.shape[:2]
    # Preserve the eyes and feather the lower face into the original portrait.
    yy, xx = np.mgrid[:height, :width]
    edge = np.minimum(xx, width - 1 - xx) / max(1, width * .12)
    top = (yy / height - .36) / .16
    bottom = (height - 1 - yy) / max(1, height * .10)
    blend = np.clip(np.minimum(np.minimum(edge, top), bottom), 0, 1)[..., None]
    eyes = np.zeros((height, width), dtype=np.float32)
    for ex, ey, rx, ry in config["eyes"]:
        distance = ((xx + x1 - ex) / rx) ** 2 + ((yy + y1 - ey) / ry) ** 2
        eyes = np.maximum(eyes, np.clip((1 - distance) * 5, 0, 1))
    eyes = eyes[..., None]
    command = [ffmpeg, "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
               "-s", f"{width}x{height}", "-r", str(FPS), "-i", "pipe:0", "-an",
               "-c:v", "h264_videotoolbox", "-q:v", "75", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    encoder = subprocess.Popen(command, stdin=subprocess.PIPE)
    variations = []
    previous_mouth = None
    try:
        for first, last, context_first, context_last in feature_windows(count):
            start_sample = context_first * SAMPLE_RATE // FPS
            end_sample = context_last * SAMPLE_RATE // FPS
            window = samples[start_sample:end_sample]
            required = end_sample - start_sample
            if len(window) < required:
                window = np.pad(window, (0, required - len(window)))
            # One sample of count epsilon avoids floor(1.16*25)==28 instead
            # of 29 in the upstream float frame counter. Audio is not shifted.
            chunks = pipeline.encode_audio(log_mel_spectrogram(mx.array(window)), len(window) + 1, fps=FPS)
            if chunks.shape[0] < context_last-context_first:
                raise RuntimeError("音频帧数不足，拒绝输出错位口型")
            mx.eval(chunks)
            for fi in range(first, last, 2):
                size = min(2, last - fi)
                index = fi - context_first
                faces = pipeline.generate_faces(mx.repeat(latent, size, axis=0), chunks[index:index + size].astype(mx.float16))
                for j, face in enumerate(faces):
                    absolute_frame = fi + j
                    center = absolute_frame * SAMPLE_RATE // FPS
                    quiet = samples[max(0, center - 960):min(len(samples), center + 960)]
                    silent = len(quiet) and float(np.max(np.abs(quiet))) < .0005
                    face = cv2.resize(face, (width, height), interpolation=cv2.INTER_LANCZOS4)
                    patch = original if silent else np.clip(face * blend + original * (1 - blend), 0, 255).astype(np.uint8)
                    phase = (absolute_frame / FPS - 3.2) % 4.6
                    blink_weight = max(0, 1 - abs(phase - .08) / .08)
                    if blink_weight:
                        mask = eyes * blink_weight
                        patch = np.clip(patch * (1 - mask) + blink * mask, 0, 255).astype(np.uint8)
                    mouth = patch[height*3//5:height*9//10, width//4:width*3//4].astype(float)
                    if previous_mouth is not None and not silent:
                        variations.append(float(np.abs(mouth - previous_mouth).mean()))
                    previous_mouth = mouth
                    encoder.stdin.write(np.ascontiguousarray(patch).tobytes())
                if (fi - first) % 50 == 0 or fi + size == last:
                    report(fi + size, "按音频生成口型")
            del chunks
            mx.clear_cache()
        encoder.stdin.close()
        if encoder.wait() != 0:
            raise RuntimeError("数字人口型视频编码失败")
    except BaseException:
        encoder.stdin.close()
        encoder.terminate()
        encoder.wait()
        raise
    if max(variations, default=0) < .1:
        raise RuntimeError("未检测到有效口型变化，拒绝输出静态人物")
    stats = {"fps": FPS, "frames": count, "audio_samples": len(samples),
             "audio_seconds": len(samples) / SAMPLE_RATE, "face_box": config["face_box"],
             "elapsed_seconds": round(time.monotonic() - started, 3),
             "peak_memory_bytes": mx.get_peak_memory(), "model": "MuseTalk 1.5 MLX fp16",
             "mouth_variation_mean": float(np.mean(variations))}
    out.with_suffix(".json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    report(count, "口型生成完成")
    wav.unlink(missing_ok=True)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--runtime", type=Path, default=ROOT / "avatar_runtime")
    ap.add_argument("--presenter", type=Path, default=ROOT / "assets/avatar/news-anchor-male.png")
    ap.add_argument("--metadata", type=Path, default=ROOT / "assets/avatar/news-anchor-male.json")
    ap.add_argument("--progress-file", type=Path)
    ap.add_argument("--max-frames", type=int, default=0)
    args = ap.parse_args()
    lock_path = ROOT / "tts/outputs/webui/generation.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        waiting = time.monotonic()
        while True:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - waiting > 900:
                    raise RuntimeError("本机语音模型仍在占用内存，请空闲后重试数字人")
                time.sleep(.25)
        render(args.audio, args.out, args.runtime, args.presenter, args.metadata, args.progress_file, args.max_frames)


if __name__ == "__main__":
    main()
