"""PC 口播字幕：语音时间信息、旧音频估算及共用的 Pillow 样式渲染。"""
import html
import json
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = {
    "sans": ("冬青黑体", "/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
    "hei": ("华文黑体", "/System/Library/Fonts/STHeiti Medium.ttc", 0),
    "song": ("宋体", "/System/Library/Fonts/Supplemental/Songti.ttc", 0),
}
FONTS = {key: value for key, value in FONT_CANDIDATES.items() if Path(value[1]).is_file()}
DEFAULT_STYLE = {"enabled": True, "color": "#FFFFFF", "font": "sans", "size": 48, "position": 90}


def normalize_style(value=None, default_enabled=True):
    if value is not None and not isinstance(value, dict):
        raise ValueError("字幕设置格式无效")
    style = {**DEFAULT_STYLE, "enabled": default_enabled, **(value or {})}
    if type(style["enabled"]) is not bool:
        raise ValueError("字幕开关无效")
    if not isinstance(style["color"], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", style["color"]):
        raise ValueError("请选择有效的字幕颜色")
    if not isinstance(style["font"], str) or style["font"] not in FONTS:
        raise ValueError("所选字幕字体在本机不可用")
    for key, low, high in [("size", 28, 72), ("position", 6, 94)]:
        if type(style[key]) is not int or not low <= style[key] <= high:
            raise ValueError("字幕字号或位置超出可调范围")
    return {key: style[key] if key != "color" else style[key].upper() for key in DEFAULT_STYLE}


def split_captions(text, limit=24):
    """按标点和可读长度切分，保留原文标点、英文空格和数字。"""
    text = re.sub(r"\s+", " ", text).strip()
    captions, current = [], ""
    tokens = re.findall(r"[A-Za-z0-9]+(?:[.'’-][A-Za-z0-9]+)*|.", text)
    tokens = [chunk for token in tokens for chunk in [token[i:i + limit] for i in range(0, len(token), limit)]]
    for token in tokens:
        if current and len(current) + len(token) > limit:
            captions.append(current.strip())
            current = ""
        current += token
        if token in "。！？!?；;" or (len(current) >= 10 and token in "，,：:"):
            captions.append(current.strip())
            current = ""
    if current.strip():
        captions.append(current.strip())
    return captions


def timed_captions(text, events, duration):
    """把原文短句映射到词时间戳；不能完整匹配时由调用方明确降级。"""
    normalized = re.sub(r"\s+", " ", text).strip()
    spans, cursor = [], 0
    for event in events:
        word = html.unescape(event.get("text", "")).strip()
        if not word:
            continue
        found = normalized.find(word, cursor)
        if found < 0:
            return None
        start, end = event.get("start"), event.get("end")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            return None
        if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration + .25:
            return None
        if spans and start < spans[-1][2]:
            return None
        cursor = found + len(word)
        spans.append((found, cursor, start, min(duration, end)))
    if not spans:
        return None
    # 防止仅返回开头几个词时误称完整同步。
    covered = "".join(normalized[a:b] for a, b, _, _ in spans)
    spoken = re.sub(r"[\W_]", "", normalized)
    if len(re.sub(r"[\W_]", "", covered)) < len(spoken) * .9:
        return None
    result, offset = [], 0
    for caption in split_captions(normalized):
        found = normalized.find(caption, offset)
        end = found + len(caption)
        words = [event for event in spans if event[0] < end and event[1] > found]
        if not words:
            # 纯标点并入上一条，避免遗漏原文。
            if result:
                result[-1]["text"] += caption
            else:
                return None
        else:
            result.append({"text": caption, "start": words[0][2], "end": words[-1][3]})
        offset = end
    # 连续短句使用下一句的开始作为切换点，保持停顿时的可读性。
    for current, following in zip(result, result[1:]):
        if following["start"] <= current["start"]:
            return None
        current["end"] = following["start"]
    return result


def estimated_captions(text, duration):
    captions = split_captions(text)
    weights = [max(1, len(re.sub(r"\s", "", caption))) for caption in captions]
    total, elapsed, result = sum(weights), 0.0, []
    for caption, weight in zip(captions, weights):
        end = elapsed + duration * weight / total
        result.append({"text": caption, "start": elapsed, "end": end})
        elapsed = end
    return result


def build_timeline(directory, text, voice, probe, *, force_estimated=False, audio_file=None):
    from speech import split_text

    if force_estimated:
        duration = float(probe(audio_file or directory / "audio.mp3")["format"]["duration"])
        return {"mode": "estimated", "captions": estimated_captions(text, duration)}
    parts = split_text(text)
    elapsed, captions, estimated = 0.0, [], False
    for index, part in enumerate(parts):
        audio = directory / f"part-{index:04d}.mp3"
        # 兼容只有合并音频的旧任务。
        if not audio.exists():
            duration = float(probe(audio_file or directory / "audio.mp3")["format"]["duration"])
            return {"mode": "estimated", "captions": estimated_captions(text, duration)}
        duration = float(probe(audio)["format"]["duration"])
        exact = None
        try:
            timing = json.loads(audio.with_suffix(".timing.json").read_text(encoding="utf-8"))
            if timing["text"] == part and timing["voice"] == voice:
                exact = timed_captions(part, timing["boundaries"], duration)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if exact is None:
            estimated = True
            exact = estimated_captions(part, duration)
        for caption in exact:
            captions.append({**caption, "start": caption["start"] + elapsed, "end": caption["end"] + elapsed})
        elapsed += duration
    return {"mode": "estimated" if estimated else "speech", "captions": captions}


def render_strip(text, style, width=1920, height=1080):
    """所有字幕使用同尺寸透明条带，避免长文打开大量 FFmpeg 输入。"""
    label, path, index = FONTS[style["font"]]
    size = style["size"]
    strip_height = min(height, size * 3 + 48)
    image = Image.new("RGBA", (width, strip_height))
    draw = ImageDraw.Draw(image)
    while True:
        font = ImageFont.truetype(path, size, index=index)
        lines, line = [], ""
        for char in text:
            if line and draw.textlength(line + char, font=font) > width * .88:
                lines.append(line)
                line = ""
            line += char
        if line:
            lines.append(line)
        if len(lines) * size * 1.4 <= strip_height - 24 or size <= 16:
            break
        size -= 2
    line_height = int(size * 1.4)
    y = (strip_height - len(lines) * line_height) // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (width - (bbox[2] - bbox[0])) / 2 - bbox[0]
        draw.text((x, y - bbox[1]), line, font=font, fill=style["color"],
                  stroke_width=max(2, size // 16), stroke_fill="#000000")
        y += line_height
    return image


def strip_y(style, height, strip_height):
    return round(max(0, min(height - strip_height, height * style["position"] / 100 - strip_height / 2)))


def preview_image(style, text, left=0):
    image = Image.new("RGBA", (1920, 1080))
    if style["enabled"]:
        strip = render_strip(text, style, width=1920-left)
        image.alpha_composite(strip, (left, strip_y(style, 1080, strip.height)))
    return image.resize((960, 540), Image.Resampling.LANCZOS)


def render_sequence(timeline, style, directory, total, width, height):
    directory = Path(directory)
    directory.mkdir(exist_ok=True)
    blank = render_strip("", style, width, height)
    blank.save(directory / "blank.png")
    entries, cursor = [], 0.0
    for index, caption in enumerate(timeline["captions"]):
        start, end = max(cursor, float(caption["start"])), min(total, float(caption["end"]))
        if end <= start:
            continue
        if start > cursor:
            entries.append((directory / "blank.png", start - cursor))
        path = directory / f"caption-{index:05d}.png"
        render_strip(caption["text"], style, width, height).save(path)
        entries.append((path, end - start))
        cursor = end
    if cursor < total:
        entries.append((directory / "blank.png", total - cursor))
    if not entries:
        entries = [(directory / "blank.png", total)]
    lines = ["ffconcat version 1.0"]
    for path, duration in entries:
        escaped = str(path.resolve()).replace("\\", "\\\\").replace("'", "\\'")
        lines.extend([f"file '{escaped}'", f"duration {duration:.9f}"])
    escaped = str((directory / "blank.png").resolve()).replace("'", "\\'")
    lines.append(f"file '{escaped}'")
    sequence = directory / "subtitles.ffconcat"
    sequence.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sequence, strip_y(style, height, blank.height)
