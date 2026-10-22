"""Check the exported presenter's lip sync with upstream SyncNet + delay control.

The crop follows joonson/syncnet_python's mouth-biased geometry. The measured
offset is a model estimate; the finished sample still needs visual inspection.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--runtime", type=Path, default=ROOT / "avatar_runtime")
    args = ap.parse_args()
    import torch
    sys.path.insert(0, str(args.runtime / "syncnet"))
    from SyncNetInstance import SyncNetInstance

    torch.set_num_threads(4)
    net = SyncNetInstance(device="cpu")
    net.loadParameters(str(args.runtime / "weights-eval/syncnet_v2.model"))
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(args.video)]))
    total = float(probe["format"]["duration"])
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    if (video["width"], video["height"]) != (1920, 1080):
        raise ValueError("本验证针对 1920×1080、左下角 30% 的固定新闻主播布局")
    directory = args.out.parent / (args.out.stem + "-artifacts")
    directory.mkdir(parents=True, exist_ok=True)
    canonical = directory / "presenter-224.mp4"
    # Fixed preset: source face detector box [311,245,714,648], cs=.4,
    # image scale=.5625, presenter position=(24,408). Preserve the final audio.
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(args.video),
                    "-vf", "crop=318:318:154:546,scale=224:224:flags=lanczos", "-r", "25",
                    "-c:v", "libx264", "-crf", "18", "-c:a", "copy", str(canonical)], check=True)
    results = []
    pieces = [("全文", 0, total)]
    length = min(8, total / 3)
    pieces += [("开头", 0, length), ("中间", max(0, (total-length)/2), length), ("结尾", max(0, total-length), length)]
    for boundary in range(28, int(total), 28):
        start = max(0, boundary-3)
        pieces.append((f"衔接{boundary}秒", start, min(6, total-start)))
    for index, (label, start, duration) in enumerate(pieces):
        clip = directory / f"section-{index}.mp4"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", str(start), "-i", str(canonical),
                        "-t", str(duration), "-c:v", "libx264", "-crf", "18", "-c:a", "aac", str(clip)], check=True)
        options = SimpleNamespace(tmp_dir=str(directory / "frames"), reference=f"section-{index}", vshift=15, batch_size=20)
        with torch.no_grad():
            offset, confidence, distances = net.evaluate(options, str(clip))
        result = {"section": label, "start": start, "duration": duration,
                  "offset_frames": int(offset), "offset_ms": int(offset) * 40,
                  "confidence": float(confidence), "distance": float(distances.mean(axis=0).min())}
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    shifted = directory / "deliberately-delayed-400ms.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(canonical), "-af", "adelay=400:all=1",
                    "-t", str(total), "-c:v", "copy", "-c:a", "aac", str(shifted)], check=True)
    options = SimpleNamespace(tmp_dir=str(directory / "frames"), reference="negative-control", vshift=15, batch_size=20)
    with torch.no_grad():
        offset, confidence, _ = net.evaluate(options, str(shifted))
    added_offset = int(offset) - results[0]["offset_frames"]
    control = {"audio_delay_ms": 400, "offset_frames": int(offset), "confidence": float(confidence),
               "additional_offset_ms": added_offset * 40}
    offsets = [r["offset_frames"] for r in results]
    passed = (all(abs(n) <= 2 for n in offsets) and max(offsets)-min(offsets) <= 2
              and all(r["confidence"] >= 2 for r in results)
              and float(confidence) >= 2 and 8 <= abs(added_offset) <= 12)
    profile = json.loads((ROOT / "assets/avatar/news-anchor-male.json").read_text())
    report = {"status": "passed" if passed else "failed", "fps": 25, "target_offset_ms": 80,
              "avatar_sha256": profile["sha256"], "video": str(args.video.resolve()),
              "video_sha256": hashlib.sha256(args.video.read_bytes()).hexdigest(),
              "generator_sha256": hashlib.sha256((ROOT / "scripts/generate_avatar.py").read_bytes()).hexdigest(),
              "profile_sha256": hashlib.sha256((ROOT / "assets/avatar/news-anchor-male.json").read_bytes()).hexdigest(),
              "revisions_sha256": hashlib.sha256((args.runtime / "revisions.json").read_bytes()).hexdigest(),
              "results": results, "negative_control": control,
              "note": "SyncNet 模型估计，需结合嘴形、停顿和表情的目视检查"}
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())
