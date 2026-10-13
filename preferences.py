"""本产品设置持久化。只接受操作参数，不保存稿件、素材、密钥或付费同意。"""
import json
import math
import os
import threading
from pathlib import Path

PATH = Path(__file__).resolve().parent / 'settings' / 'preferences.json'
LOCK = threading.RLock()
BOOL_FIELDS = {'showTitle', 'musicEnabled', 'subtitleEnabled', 'audioOnly'}
RANGES = {
    'speechSpeed': (.5, 2), 'speechSilence': (0, 2000), 'speechSeed': (0, 2147483647),
    'speechTokens': (30, 300), 'speechTemperature': (.1, 2), 'speechSteps': (4, 100),
    'speechGuidance': (0, 10), 'drawthingsPort': (1024, 65535), 'titleSize': (48, 320),
    'subtitlePosition': (6, 94), 'musicVolume': (0, 100),
}
MODELS = ['OmniVoice', 'IndexTTS 2.5', 'IndexTTS 2.0', 'Fish Audio S2 Pro', 'VoiceStudio · OmniVoice 原生', 'CosyVoice 3']
ENUMS = {
    'audioProvider': ['edge-tts', 'omnivoice'], 'localBackend': MODELS,
    'omniExpression': ['follow_reference', 'calm', 'happy', 'sad', 'energetic', 'serious', 'whisper'],
    'imageMode': ['generate', 'upload', 'url'], 'provider': ['drawthings', 'seedream'],
    'queueScheduledOutput': ['mp3', 'mp4'], 'musicLoop': [120, 180, 300], 'subtitleSize': [28, 36, 48, 60, 72],
}
PROFILE_FIELDS = {
    'speed': 'speechSpeed', 'interval_silence': 'speechSilence', 'seed': 'speechSeed',
    'max_text_tokens': 'speechTokens', 'temperature': 'speechTemperature',
    'omnivoice_num_steps': 'speechSteps', 'omnivoice_guidance_scale': 'speechGuidance',
}


def normalize(value):
    import re
    if not isinstance(value, dict) or set(value) - {'controls', 'mode', 'speech_profiles'}:
        raise ValueError('设置格式无效')
    controls = value.get('controls', {})
    if not isinstance(controls, dict):
        raise ValueError('设置项格式无效')
    result = {}
    text_fields = {'edgeVoice', 'omniVoice', 'titleFont', 'subtitleFont', 'musicTrack', 'model'}
    colors = {'titleColor', 'subtitleColor'}
    for key, raw in controls.items():
        if key in BOOL_FIELDS:
            valid = isinstance(raw, bool)
        elif key in RANGES:
            valid = isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw) and RANGES[key][0] <= raw <= RANGES[key][1]
        elif key in ENUMS:
            valid = raw in ENUMS[key] and not isinstance(raw, bool)
        elif key in text_fields:
            valid = isinstance(raw, str) and len(raw) <= 100 and not any(ord(c) < 32 for c in raw)
        elif key in colors:
            valid = isinstance(raw, str) and bool(re.fullmatch('#[0-9a-fA-F]{6}', raw))
        else:
            valid = False
        if not valid:
            raise ValueError('不支持或无效的设置项：' + key)
        result[key] = raw
    mode = value.get('mode', 'short')
    if mode not in {'short', 'long'}:
        raise ValueError('创作模式无效')
    profiles = value.get('speech_profiles', {})
    if not isinstance(profiles, dict):
        raise ValueError('语音参数档案无效')
    for backend, profile in profiles.items():
        if backend not in MODELS or not isinstance(profile, dict):
            raise ValueError('语音模型档案无效')
        for key, number in profile.items():
            if key not in PROFILE_FIELDS:
                raise ValueError('语音参数无效')
            low, high = RANGES[PROFILE_FIELDS[key]]
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not low <= number <= high:
                raise ValueError('语音参数超出范围')
    return {'controls': result, 'mode': mode, 'speech_profiles': profiles}


def read():
    with LOCK:
        if not PATH.exists():
            return None
        return normalize(json.loads(PATH.read_text(encoding='utf-8')))


def save(value):
    normalized = normalize(value)
    with LOCK:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        pending = PATH.with_suffix('.pending')
        with pending.open('w', encoding='utf-8') as output:
            json.dump(normalized, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        pending.replace(PATH)
    return normalized
