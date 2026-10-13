"""微软在线语音接口兼容补丁，仅作用于本工作台进程。

参考 edge-tts 上游 PR #491（2026-10-12）：
https://github.com/rany2/edge-tts/pull/491
发布库仍使用旧接口时，接入当前 Edge 的微软语音接口；不改写 site-packages。
"""
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from edge_tts import communicate, constants
from edge_tts.drm import DRM


SYNTHESIS_ENDPOINT = 'wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1'


def parse_http_date(value):
    """HTTP 日期使用英文星期/月名，不依赖操作系统语言；保留时区。"""
    if not isinstance(value, str):
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def configure_edge_tts():
    """仅替换已知旧接口；新版本库若已迁移则沿用其自身实现。"""
    current = urlsplit(communicate.WSS_URL)
    if current.hostname != 'speech.platform.bing.com' or current.path != '/consumer/speech/synthesize/readaloud/edge/v1':
        return False
    endpoint = SYNTHESIS_ENDPOINT + '?Ocp-Apim-Subscription-Key=' + constants.TRUSTED_CLIENT_TOKEN
    constants.WSS_URL = endpoint
    communicate.WSS_URL = endpoint
    DRM.parse_rfc2616_date = staticmethod(parse_http_date)
    return True
