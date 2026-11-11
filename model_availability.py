"""Cheap offline checks for the files each installed backend actually needs."""

import os
from pathlib import Path


VOICESTUDIO_BACKEND = "VoiceStudio · OmniVoice 原生"
REQUIRED_FILES = {
    "OmniVoice": tuple(
        "models/OmniVoice-bfloat16/" + name for name in (
            "model.safetensors", "config.json", "tokenizer.json", "tokenizer_config.json",
            "audio_tokenizer/model.safetensors", "audio_tokenizer/config.json",
        )
    ),
    "IndexTTS 2.5": tuple(
        "models/mlx-IndexTTS-2.5-int8/" + name for name in (
            "gpt_int8.safetensors", "cfm_estimator.safetensors", "spk_proj.npz",
            "multilingual_zh_ja_yue_char_del.tiktoken", "raw/semantic_model.safetensors",
            "raw/emo.safetensors", "raw/campplus.safetensors",
            "raw/length_regulator.safetensors", "raw/semantic_codec.safetensors",
            "raw/bigvgan.safetensors",
        )
    ),
    "IndexTTS 2.0": tuple(
        "models/mlx-IndexTTS-2/" + name for name in (
            "config.yaml", "bpe.model", "gpt.safetensors", "s2mel.safetensors",
            "bigvgan.safetensors", "vq2emb.safetensors",
        )
    ),
    "Fish Audio S2 Pro": tuple(
        "models/fish-audio-s2-pro-8bit/" + name for name in (
            "model.safetensors", "codec.safetensors", "config.json",
            "tokenizer.json", "tokenizer_config.json",
        )
    ),
    VOICESTUDIO_BACKEND: tuple(
        "models/VoiceStudio-OmniVoice/" + name for name in (
            "model.safetensors", "config.json", "tokenizer.json", "tokenizer_config.json",
            "audio_tokenizer/model.safetensors", "audio_tokenizer/config.json",
        )
    ),
    "CosyVoice 3": tuple(
        "models/Fun-CosyVoice3-0.5B-2512/" + name for name in (
            "cosyvoice3.yaml", "llm.pt", "flow.pt", "hift.pt",
            "speech_tokenizer_v3.onnx", "campplus.onnx", "CosyVoice-BlankEN/model.safetensors",
        )
    ) + ("vendor/cosyvoice3-macos/.venv/bin/python", "vendor/cosyvoice3-macos/tts_worker.py"),
}
MODEL_BACKENDS = tuple(REQUIRED_FILES)


def available_backends(project_root: Path) -> list[str]:
    """Check presence, nonempty files and unfinished download markers, not inference."""
    available = []
    for backend, names in REQUIRED_FILES.items():
        paths = [project_root / name for name in names]
        if backend == VOICESTUDIO_BACKEND:
            source = Path(os.environ.get("VOICESTUDIO_PROJECT_ROOT", project_root.parent.parent / "VoiceStudio"))
            if not source.is_absolute():
                source = project_root / source
            paths.append(source / "omnivoice/models/omnivoice.py")
        try:
            for path in paths:
                if not path.is_file() or path.stat().st_size == 0:
                    break
                if any(path.with_name(path.name + suffix).exists()
                       for suffix in (".aria2", ".incomplete", ".download", ".partial")):
                    break
            else:
                available.append(backend)
        except OSError:
            # A disconnected disk or unreadable file must not break the UI.
            continue
    return available
