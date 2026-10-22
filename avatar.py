"""Fixed news presenter settings and local MuseTalk runtime discovery."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "avatar_runtime"
ASSETS = ROOT / "assets/avatar"
DEFAULTS = {"enabled": False, "preset": "news-anchor-male"}


def normalize_settings(value=None):
    if value is not None and not isinstance(value, dict):
        raise ValueError("数字人设置无效")
    value = value or {}
    if not isinstance(value.get("enabled", False), bool):
        raise ValueError("数字人开关需为布尔值")
    if value.get("preset", DEFAULTS["preset"]) != DEFAULTS["preset"]:
        raise ValueError("请选择新闻男主播形象")
    return {**DEFAULTS, "enabled": value.get("enabled", False)}


def capability():
    paths = [RUNTIME / ".venv/bin/python", RUNTIME / "musetalk-core/musetalk_mlx/pipeline_mlx.py"]
    paths += [RUNTIME / "weights-mlx" / name for name in
              ("config.json", "unet.safetensors", "vae.safetensors", "whisper_encoder.safetensors")]
    ready = all(p.is_file() and p.stat().st_size for p in paths)
    checked = False
    try:
        validation = json.loads((RUNTIME / "validated.json").read_text(encoding="utf-8"))
        profile = json.loads((ASSETS / "news-anchor-male.json").read_text(encoding="utf-8"))
        checked = validation.get("status") == "passed" and validation.get("avatar_sha256") == profile["sha256"]
        for key, path in [("generator_sha256", ROOT / "scripts/generate_avatar.py"),
                          ("profile_sha256", ASSETS / "news-anchor-male.json"),
                          ("revisions_sha256", RUNTIME / "revisions.json")]:
            checked = checked and validation.get(key) == hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, ValueError, KeyError):
        pass
    return {"available": bool(ready and checked), "defaults": DEFAULTS,
            "name": "新闻男主播", "width_percent": 30, "fps": 25,
            "message": "本机口型模型已就绪" if ready and checked else "数字人模型尚未就绪，完成安装与口型同步验证后可启用"}


def require_available():
    if not capability()["available"]:
        raise ValueError("数字人口型模型尚未完成安装或同步验证，请稍后使用")


def cache_key(audio):
    digest = hashlib.sha256()
    with Path(audio).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    for path in [ASSETS / "news-anchor-male.json", ROOT / "scripts/generate_avatar.py",
                 RUNTIME / "revisions.json"]:
        digest.update(path.read_bytes())
    return digest.hexdigest()[:24]
