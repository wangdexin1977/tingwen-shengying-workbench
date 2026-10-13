"""听文 V3 的无界面语音适配：沿用音色和 2000 字分段，持久保存已完成片段。"""
import asyncio
import json
import re
import time

import edge_tts
from edge_compat import configure_edge_tts

# aiohttp 的 WebSocket 默认可能取消 sock_read 时限；在应用层独立限时。
STREAM_IDLE_TIMEOUT = 30
STREAM_TOTAL_TIMEOUT = 120
RETRY_DELAY = 1.5

VOICES = [
    ("zh-CN-YunjianNeural", "云健", "男 · 充满激情"),
    ("zh-CN-YunxiNeural", "云希", "男 · 沉稳大气"),
    ("zh-CN-YunyangNeural", "云扬", "男 · 成熟稳重"),
    ("zh-CN-YunxiaNeural", "云霞", "男 · 温和清晰"),
    ("zh-CN-XiaoxiaoNeural", "晓晓", "女 · 温暖自然"),
    ("zh-CN-XiaoyiNeural", "小艺", "女 · 温柔感性"),
    ("zh-CN-liaoning-XiaobeiNeural", "小北", "女 · 东北口音"),
    ("zh-CN-shaanxi-XiaoniNeural", "小妮", "女 · 陕西口音"),
    ("zh-HK-HiuGaaiNeural", "Hiu Gaai", "女 · 香港粤语"),
    ("zh-HK-HiuMaanNeural", "Hiu Maan", "女 · 香港粤语"),
    ("zh-HK-WanLungNeural", "Wan Lung", "男 · 香港粤语"),
    ("zh-TW-HsiaoChenNeural", "Hsiao Chen", "女 · 台湾国语"),
    ("zh-TW-HsiaoYuNeural", "Hsiao Yu", "女 · 台湾国语"),
    ("zh-TW-YunJheNeural", "Yun Jhe", "男 · 台湾国语"),
    ("de-DE-FlorianMultilingualNeural", "弗洛里安", "男 · 德国多语言"),
    ("de-DE-SeraphinaMultilingualNeural", "塞拉菲娜", "女 · 德国多语言"),
    ("en-AU-WilliamMultilingualNeural", "威廉", "男 · 澳大利亚多语言"),
    ("en-US-AndrewMultilingualNeural", "安德鲁", "男 · 美国多语言"),
    ("en-US-AvaMultilingualNeural", "艾娃", "女 · 美国多语言"),
    ("en-US-BrianMultilingualNeural", "布莱恩", "男 · 美国多语言"),
    ("en-US-EmmaMultilingualNeural", "艾玛", "女 · 美国多语言"),
    ("fr-FR-RemyMultilingualNeural", "雷米", "男 · 法国多语言"),
    ("fr-FR-VivienneMultilingualNeural", "薇薇安", "女 · 法国多语言"),
    ("it-IT-GiuseppeMultilingualNeural", "朱塞佩", "男 · 意大利多语言"),
    ("ko-KR-HyunsuMultilingualNeural", "贤洙", "男 · 韩国多语言"),
    ("pt-BR-ThalitaMultilingualNeural", "塔莉塔", "女 · 巴西多语言"),
]


class Cancelled(Exception):
    pass


class SpeechServiceError(RuntimeError):
    pass


def speech_error_message(error):
    text = str(error)
    cause = error
    seen = set()
    forbidden = False
    while isinstance(cause, BaseException) and id(cause) not in seen:
        seen.add(id(cause))
        forbidden |= getattr(cause, 'status', None) == 403
        cause = cause.__cause__ or cause.__context__
    if forbidden or ('403' in text and ('Invalid response status' in text or 'speech.platform.bing.com' in text)):
        return '微软语音服务拒绝连接（HTTP 403）。已完成片段和照片保留；可稍后重试，或选择本机 OmniVoice 生成 MP3。'
    return re.sub(r'(?:https?|wss?)://[^\s\"\'<>]+', '[连接地址已隐藏]', text)


async def controlled_stream(stream, control, on_wait):
    """等待网络时也检查取消；暂停时间不计入无响应和总时限。"""
    pending = None
    elapsed, idle, reported = 0, 0, -1
    try:
        while True:
            await control.wait_async()
            if pending is None:
                pending = asyncio.create_task(anext(stream))
            started = time.monotonic()
            done, _ = await asyncio.wait({pending}, timeout=.1)
            interval = time.monotonic() - started
            elapsed += interval
            idle += interval
            control.check_cancel()
            if idle >= STREAM_IDLE_TIMEOUT or elapsed >= STREAM_TOTAL_TIMEOUT:
                raise RuntimeError("语音服务响应超时；已完成片段保留，可检查网络后重试")
            if int(elapsed) != reported:
                reported = int(elapsed)
                on_wait(reported)
            if done:
                try:
                    chunk = pending.result()
                except StopAsyncIteration:
                    return
                pending = None
                idle = 0
                await control.wait_async()
                yield chunk
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await stream.aclose()


async def retry_wait(control, seconds):
    remaining = seconds
    while remaining > 0:
        await control.wait_async()
        duration = min(.1, remaining)
        await asyncio.sleep(duration)
        remaining -= duration
    control.check_cancel()


def split_text(text, limit=2000):
    parts, current = [], ""
    for paragraph in (p.strip() for p in text.split("\n\n")):
        if not paragraph:
            continue
        if len(paragraph) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.extend(paragraph[i:i + limit] for i in range(0, len(paragraph), limit))
        elif len(current) + len(paragraph) + 2 <= limit:
            current = (current + "\n\n" + paragraph).strip()
        else:
            parts.append(current)
            current = paragraph
    if current:
        parts.append(current)
    return parts


async def synthesize(text, voice, folder, control, progress, factory=None, validate=None, on_progress=None):
    use_word_boundaries = factory is None
    if use_word_boundaries:
        configure_edge_tts()
    factory = factory or edge_tts.Communicate
    parts = split_text(text)
    paths = []
    if on_progress:
        on_progress(0, len(parts), "语音分段")
    for i, segment in enumerate(parts):
        await control.wait_async()
        final = folder / f"part-{i:04d}.mp3"
        if not final.exists():
            pending = final.with_suffix(".partial")
            for attempt in range(3):
                label = f"音频片段 {i + 1}/{len(parts)}" + (f" · 重试 {attempt}/2" if attempt else "")
                progress(label)
                received = 0
                boundaries = []
                try:
                    with pending.open("wb") as out:
                        communicator = factory(segment, voice, boundary="WordBoundary") if use_word_boundaries else factory(segment, voice)
                        async for chunk in controlled_stream(communicator.stream(), control,
                                lambda seconds: progress(f"{label} · 本次 {seconds} 秒 · 已接收 {received // 1024} KB")):
                            await control.wait_async()
                            if chunk["type"] == "audio":
                                out.write(chunk["data"])
                                received += len(chunk["data"])
                            elif chunk["type"] in {"WordBoundary", "SentenceBoundary"} and all(key in chunk for key in ("text", "offset", "duration")):
                                boundaries.append({"text": chunk["text"], "start": chunk["offset"] / 10_000_000,
                                                   "end": (chunk["offset"] + chunk["duration"]) / 10_000_000})
                    if not pending.stat().st_size:
                        raise RuntimeError("语音服务没有返回音频")
                    if validate:
                        validate(pending)
                    timing = final.with_suffix(".timing.json")
                    timing_pending = timing.with_suffix(".pending")
                    timing_pending.write_text(json.dumps({"text": segment, "voice": voice, "boundaries": boundaries}, ensure_ascii=False), encoding="utf-8")
                    timing_pending.replace(timing)
                    pending.replace(final)
                    break
                except Cancelled:
                    raise
                except Exception as exc:
                    if getattr(exc, 'status', None) == 403 or 'HTTP 403' in speech_error_message(exc):
                        raise SpeechServiceError(speech_error_message(exc)) from None
                    if attempt == 2:
                        raise
                    await control.wait_async()
                    await retry_wait(control, RETRY_DELAY * (attempt + 1))
        paths.append(final)
        if on_progress:
            on_progress(i + 1, len(parts), "语音分段")
    progress("音频分段已完成，正在合并与验证 MP3")
    if on_progress:
        on_progress(None, len(parts), "合并与验证")
    pending = folder / "audio.pending.mp3"
    with pending.open("wb") as out:
        for path in paths:
            with path.open("rb") as source:
                while True:
                    await control.wait_async()
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
    if validate:
        validate(pending)
    pending.replace(folder / "audio.mp3")
