#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频首页背景图合成脚本
- 背景：整张高清图 cover 铺满目标分辨率（或 fit-blur 完整呈现整图）
- 默认统一标题：最多 30 字，暖黄色大字，无背景底板
- 支持字体、字号、颜色及手动/自动分行；poster / legacy 保留历史 CLI 样式
用法:
  python3 make_homepage_bg.py --theme "今日全球军事速览" --bg bg.jpg --out out.png
"""
import argparse
import json
import math
import os
import re
import sys

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

TITLE_COLORS = ("#FF3030", "#FFD700", "#FFFFFF")
FONT_CANDIDATES = [
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 2, "Hiragino Sans GB W6"),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0, "Hiragino Sans GB W3"),
    ("/System/Library/Fonts/PingFang.ttc", None, "PingFang"),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", None, "Songti"),
    ("/System/Library/Fonts/STHeiti Medium.ttc", None, "STHeiti"),
    ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", None, "Arial Bold"),
]

POSTER_COLORS = ("#E1261C", "#F7DE00")
POSTER_PANEL = "#031127"
YELLOW_TITLE_COLOR = "#FFDA35"
TITLE_FONTS = {
    "sans-bold": ("冬青黑体 · 粗体", "/System/Library/Fonts/Hiragino Sans GB.ttc", 2),
    "sans": ("冬青黑体 · 常规", "/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
    "hei": ("华文黑体", "/System/Library/Fonts/STHeiti Medium.ttc", 0),
    "song": ("宋体", "/System/Library/Fonts/Supplemental/Songti.ttc", 0),
}
TITLE_FONTS = {key: value for key, value in TITLE_FONTS.items() if os.path.isfile(value[1])}
DEFAULT_TITLE_SETTINGS = {"font": "sans-bold", "color": YELLOW_TITLE_COLOR, "size": 265}


def normalize_title_settings(value=None):
    if value is not None and not isinstance(value, dict):
        raise ValueError("标题样式设置格式无效")
    style = {**DEFAULT_TITLE_SETTINGS, **(value or {})}
    if not isinstance(style["font"], str) or style["font"] not in TITLE_FONTS:
        raise ValueError("所选标题字体在本机不可用")
    if not isinstance(style["color"], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", style["color"]):
        raise ValueError("请选择有效的标题颜色")
    if type(style["size"]) is not int or not 48 <= style["size"] <= 320:
        raise ValueError("标题字号应在 48–320 px 范围内")
    return {"font": style["font"], "color": style["color"].upper(), "size": style["size"]}


def normalize_title(text):
    return "\n".join(" ".join(line.split()) for line in str(text).replace("\r", "\n").split("\n") if line.strip())


def title_character_count(title, secondary=""):
    return len(normalize_title(title).replace("\n", "")) + len(normalize_title(secondary).replace("\n", ""))


def unified_title(title, secondary=""):
    return normalize_title("\n".join(value for value in [normalize_title(title), normalize_title(secondary)] if value))


def draw_yellow_title(canvas, title, settings=None):
    """按用户样式绘制自然比例标题，仅描绘字形、轻描边与阴影。"""
    title = normalize_title(title)
    settings = normalize_title_settings(settings)
    if not title:
        return None
    if title_character_count(title) > 30:
        raise ValueError("视频标题最多 30 字")
    width, height = canvas.size
    region = [width * .05, height * .24, width * .95, height * .77]
    texts = []
    for paragraph in title.splitlines():
        count = max(1, math.ceil(len(paragraph) / 10))
        offset = 0
        for i in range(count):
            length = len(paragraph) // count + (i < len(paragraph) % count)
            texts.append(paragraph[offset:offset + length])
            offset += length
    _, path, index = TITLE_FONTS[settings["font"]]
    draw = ImageDraw.Draw(canvas)

    def measure(size):
        font = load_font(path, size, index)
        stroke = max(2, round(size * .018))
        gap = max(12, round(size * .18))
        boxes = [draw.textbbox((0, 0), text, font=font, stroke_width=stroke) for text in texts]
        total = sum(box[3] - box[1] for box in boxes) + gap * (len(boxes) - 1)
        return font, stroke, gap, boxes, total

    # 保留小幅阴影的安全边距；所有行统一字号，长短句不再分主副层级。
    lo, hi, best = 1, max(1, round(settings["size"] * height / 1080)), 1
    while lo <= hi:
        size = (lo + hi) // 2
        _, stroke, _, boxes, total = measure(size)
        if max(box[2] - box[0] for box in boxes) + 2 * stroke <= region[2] - region[0] and total + 2 * stroke <= region[3] - region[1]:
            best, lo = size, size + 1
        else:
            hi = size - 1
    font, stroke, gap, boxes, total = measure(best)
    shadow = Image.new("RGBA", canvas.size)
    shadow_draw = ImageDraw.Draw(shadow)
    text_layer = Image.new("RGBA", canvas.size)
    text_draw = ImageDraw.Draw(text_layer)
    y = (region[1] + region[3] - total) / 2
    result = {"style": "clean-yellow", "font_size": best, "font": " ".join(font.getname()),
              "safe_region": region, "panel_bounds": None, "horizontal_scale": 1,
              "colors": [settings["color"]], "settings": settings, "outline_width": stroke, "lines": []}
    for text, box in zip(texts, boxes):
        row_width, row_height = box[2] - box[0], box[3] - box[1]
        x = (width - row_width) / 2
        origin = (x - box[0], y - box[1])
        shadow_draw.text((origin[0] + stroke, origin[1] + stroke * 1.5), text, font=font,
                         fill=(0, 0, 0, 145), stroke_width=stroke, stroke_fill=(0, 0, 0, 145))
        text_draw.text(origin, text, font=font, fill=settings["color"],
                       stroke_width=stroke, stroke_fill="#29251D")
        result["lines"].append({"text": text, "font_size": best, "color": settings["color"],
                                "bounds": [x, y, x + row_width, y + row_height]})
        y += row_height + gap
    combined = Image.alpha_composite(canvas.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(max(1, stroke * .7))))
    combined = Image.alpha_composite(combined, text_layer)
    canvas.paste(combined.convert(canvas.mode))
    return result


def draw_poster_title(canvas, title, secondary=""):
    """中央深色专栏：主标题前半红后半黄，副标题黄色，实心粗体。"""
    title, secondary = normalize_title(title), normalize_title(secondary)
    if not title and not secondary:
        return None
    if title_character_count(title, secondary) > 30:
        raise ValueError("主标题和副标题合计最多 30 字")
    width, height = canvas.size
    panel = [round(width * .32), 0, round(width * .68), height]
    region = [width * .335, height * .13, width * .665, height * .79]
    available_width, available_height = region[2] - region[0], region[3] - region[1]
    path, index, _ = find_font()
    draw = ImageDraw.Draw(canvas)

    def lines(text, limit):
        return [line[start:start + limit] for line in text.splitlines() for start in range(0, len(line), limit)]

    main_lines, secondary_lines = lines(title, 6), lines(secondary, 7)

    def measure(text, size):
        font = load_font(path, size, index)
        bold = max(1, size // 40)
        spacing = max(2, size // 70)
        cursor, glyphs = 0, []
        for ch in text:
            box = draw.textbbox((0, 0), ch, font=font, anchor="ls", stroke_width=bold)
            # 参考图字形略窄；统一压缩到 88% 宽，获得更高、更紧凑的粗体字。
            bounds = [cursor, box[1], cursor + (box[2] - box[0]) * .88, box[3]]
            glyphs.append((ch, box, bounds))
            cursor = bounds[2] + spacing
        box = [0, min(g[2][1] for g in glyphs), glyphs[-1][2][2], max(g[2][3] for g in glyphs)]
        return {"text": text, "font": font, "font_size": size, "bold_radius": bold, "box": box, "glyphs": glyphs}

    def fit(text, cap):
        lo, hi, best = 1, max(1, round(cap)), 1
        while lo <= hi:
            size = (lo + hi) // 2
            row = measure(text, size)
            if row["box"][2] <= available_width - 4:
                best, lo = size, size + 1
            else:
                hi = size - 1
        return measure(text, best)

    mains = [fit(text, height * .21) for text in main_lines]
    yellow_cap = min(height * .115, min((row["font_size"] for row in mains), default=height * .14) * .9)
    # 副标题统一字号，避免短行突然变成与主标题同样醒目的巨字。
    yellow_size = min((fit(text, yellow_cap)["font_size"] for text in secondary_lines), default=1)
    yellows = [measure(text, yellow_size) for text in secondary_lines]
    rows = [(row, "main", POSTER_COLORS[0]) for row in mains] + [(row, "secondary", POSTER_COLORS[1]) for row in yellows]

    def layout(scale):
        scaled = [(measure(row["text"], max(1, round(row["font_size"] * scale))), kind, color) for row, kind, color in rows]
        gaps = [height * (.034 if scaled[i][1] != scaled[i + 1][1] else .018) * scale for i in range(len(scaled) - 1)]
        total = sum(row["box"][3] - row["box"][1] for row, _, _ in scaled) + sum(gaps)
        return scaled, gaps, total

    scale = 1.0
    selected, gaps, total = layout(scale)
    while total > available_height - 4:
        scale *= .96
        selected, gaps, total = layout(scale)
    draw.rectangle((panel[0], 0, panel[2] - 1, height - 1), fill=POSTER_PANEL)
    y = (region[1] + region[3] - total) / 2
    result = {"style": "poster", "panel_bounds": panel, "safe_region": region,
              "colors": list(POSTER_COLORS), "outline_width": 0, "horizontal_scale": .88, "lines": []}
    main_split = (len(title.replace("\n", "")) + 1) // 2
    main_position = 0
    for i, (row, kind, color) in enumerate(selected):
        box, font = row["box"], row["font"]
        x, baseline = (width - box[2]) / 2, y - box[1]
        characters, character_colors = [], []
        for ch, glyph_box, bounds in row["glyphs"]:
            glyph_color = color
            if kind == "main":
                glyph_color = POSTER_COLORS[0 if main_position < main_split else 1]
                main_position += 1
            left, top = math.floor(bounds[0] + x), math.floor(bounds[1] + baseline)
            right, bottom = math.ceil(bounds[2] + x), math.ceil(bounds[3] + baseline)
            mask = Image.new("L", (glyph_box[2] - glyph_box[0] + 1, glyph_box[3] - glyph_box[1] + 1), 0)
            ImageDraw.Draw(mask).text((-glyph_box[0], -glyph_box[1]), ch, font=font,
                                     fill=255, anchor="ls", stroke_width=row["bold_radius"], stroke_fill=255)
            mask = mask.resize((right - left + 1, bottom - top + 1), Image.Resampling.LANCZOS)
            canvas.paste(glyph_color, (left, top), mask)
            characters.append([bounds[0] + x, bounds[1] + baseline, bounds[2] + x, bounds[3] + baseline])
            character_colors.append(glyph_color)
        row_height = box[3] - box[1]
        result["lines"].append({"text": row["text"], "kind": kind, "color": color,
                                "font_size": row["font_size"], "font": " ".join(font.getname()),
                                "bold_radius": row["bold_radius"], "bounds": [x, y, x + box[2], y + row_height],
                                "characters": characters, "character_colors": character_colors})
        y += row_height + (gaps[i] if i < len(gaps) else 0)
    return result


def log(msg):
    print(msg, file=sys.stderr)


def find_font():
    for path, index, name in FONT_CANDIDATES:
        if os.path.exists(path):
            return path, index, name
    return None, None, None


def load_font(path, size, index):
    try:
        if path and str(path).lower().endswith((".ttc", ".otc")) and index is not None:
            return ImageFont.truetype(path, size, index=index)
        if path:
            return ImageFont.truetype(path, size)
    except Exception as e:
        log(f"[warn] 字体加载失败 {path} index={index}: {e}")
    return ImageFont.load_default()


def draw_center_title(canvas, title):
    """按实际字形（含描边）选最大字号；居中、安全边距、字间距与行距均可检验。"""
    title = " ".join(title.split())
    if not title:
        return None
    if len(title) > 30:
        raise ValueError("视频标题最多 30 字")
    width, height = canvas.size
    region = [width * .05, height * .25, width * .95, height * .75]
    count = min(3, math.ceil(len(title) / 10))
    lengths = [len(title) // count + (i < len(title) % count) for i in range(count)]
    texts, offset = [], 0
    for length in lengths:
        texts.append(title[offset:offset + length])
        offset += length
    draw = ImageDraw.Draw(canvas)
    path, index, name = find_font()

    def measure(size):
        font = load_font(path, size, index)
        stroke = max(2, size // 35)
        bold = max(1, size // 60)
        spacing = max(2 * (stroke + bold) + 3, size // 10)
        gap = max(12, size // 5)
        rows = []
        for text in texts:
            cursor, glyphs = 0, []
            for ch in text:
                box = draw.textbbox((0, 0), ch, font=font, anchor="ls", stroke_width=stroke + bold)
                origin = cursor - box[0]
                bounds = [cursor, box[1], origin + box[2], box[3]]
                glyphs.append((ch, origin, bounds))
                cursor = bounds[2] + spacing
            bounds = [min(g[2][0] for g in glyphs), min(g[2][1] for g in glyphs),
                      max(g[2][2] for g in glyphs), max(g[2][3] for g in glyphs)]
            rows.append((text, glyphs, bounds))
        return font, stroke, bold, gap, rows

    lo, hi, best = 10, height, 10
    while lo <= hi:
        size = (lo + hi) // 2
        _, _, _, gap, rows = measure(size)
        widest = max(box[2] - box[0] for _, _, box in rows)
        total = sum(box[3] - box[1] for _, _, box in rows) + gap * (count - 1)
        if widest <= region[2] - region[0] - 4 and total <= region[3] - region[1] - 4:
            best, lo = size, size + 1
        else:
            hi = size - 1
    font, stroke, bold, gap, rows = measure(best)
    total = sum(box[3] - box[1] for _, _, box in rows) + gap * (count - 1)
    y = (height - total) / 2
    actual_font = " ".join(font.getname())
    result = {"font_size": best, "font": actual_font, "bold_radius": bold,
              "safe_region": region, "colors": list(TITLE_COLORS), "lines": []}
    position = 0
    for text, glyphs, box in rows:
        row_width, row_height = box[2] - box[0], box[3] - box[1]
        x, baseline = (width - row_width) / 2 - box[0], y - box[1]
        characters = []
        for ch, dx, bounds in glyphs:
            color = TITLE_COLORS[min(2, position * 3 // len(title))]
            # 字形只绘制一次，用小型蒙版加粗、扩出黑描边，不靠偏移叠印文字。
            left, top = math.floor(bounds[0] + x), math.floor(bounds[1] + baseline)
            right, bottom = math.ceil(bounds[2] + x), math.ceil(bounds[3] + baseline)
            mask = Image.new("L", (right - left + 1, bottom - top + 1), 0)
            ImageDraw.Draw(mask).text((x + dx - left, baseline - top), ch, font=font,
                                     fill=255, anchor="ls", stroke_width=bold, stroke_fill=255)
            outline = mask.filter(ImageFilter.MaxFilter(stroke * 2 + 1))
            canvas.paste("black", (left, top), outline)
            canvas.paste(color, (left, top), mask)
            characters.append([bounds[0] + x, bounds[1] + baseline,
                               bounds[2] + x, bounds[3] + baseline])
            position += 1
        result["lines"].append({"text": text, "bounds": [(width - row_width) / 2, y,
                                                          (width + row_width) / 2, y + row_height],
                                "characters": characters})
        y += row_height + gap
    return result


def wrap_text(text, font, max_w, draw):
    """按像素宽度折行（中文逐字）"""
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def draw_subtitle(canvas, subtitle, font_path, font_index, width, height):
    """副标题：下部约 1/3 处，白色小号字 + 黑描边 + 半透明黑圆角底条，最多 3 行"""
    if not subtitle:
        return
    draw = ImageDraw.Draw(canvas)
    # 字号自适应：先 52px，超 3 行则缩小，最小 28px
    size = 52
    lines = None
    while size >= 28:
        font = load_font(font_path, size, font_index)
        lines = wrap_text(subtitle, font, int(width * 0.9), draw)
        if len(lines) <= 3:
            break
        size -= 4
    font = load_font(font_path, size, font_index)
    asc, desc = font.getmetrics()
    line_h = asc + desc
    pad_x, pad_y = 28, 16
    w_max = max(draw.textlength(t, font=font) for t in lines)
    bw = w_max + pad_x * 2
    bh = len(lines) * line_h + pad_y * 2
    cx, cy = width // 2, int(height * 0.70)
    x0, y0 = cx - bw // 2, cy - bh // 2
    # 半透明黑圆角底条（提升可读性）
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=20, fill=(0, 0, 0, 110))
    canvas.paste(Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB"), (0, 0))
    draw = ImageDraw.Draw(canvas)
    # 白字 + 黑描边 + 阴影
    y = y0 + pad_y
    for t in lines:
        x = cx - draw.textlength(t, font=font) / 2
        draw.text((x + 2, y + asc + 2), t, font=font, fill=(0, 0, 0, 180),
                  stroke_width=5, stroke_fill=(0, 0, 0, 255))
        draw.text((x, y + asc), t, font=font, fill=(255, 255, 255, 255),
                  stroke_width=5, stroke_fill=(0, 0, 0, 255))
        y += line_h
    log(f"[info] 副标题: {size}px, {len(lines)} 行, {len(subtitle)} 字")
    log(f"[subtitle-region] {y0} {y0 + bh}")  # 副标题底条占位区域（供字幕避让定位）


def main():
    ap = argparse.ArgumentParser(description="视频首页背景图合成")
    ap.add_argument("--theme", default="", help="主题文字（可留空：图片本身已带文字则不加任何文字）")
    ap.add_argument("--title-secondary", default="", help="参考图样式的黄色副标题")
    ap.add_argument("--title-style", choices=["clean-yellow", "poster", "legacy"], default="clean-yellow")
    ap.add_argument("--title-font", choices=list(TITLE_FONTS), default=DEFAULT_TITLE_SETTINGS["font"])
    ap.add_argument("--title-color", default=YELLOW_TITLE_COLOR)
    ap.add_argument("--title-font-size", type=int, default=DEFAULT_TITLE_SETTINGS["size"])
    ap.add_argument("--bg", required=True, help="背景图路径（高清原图）")
    ap.add_argument("--out", required=True, help="输出路径")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--dim", type=float, default=0.0, help="背景压暗 0~1")
    ap.add_argument("--mode", default="cover", choices=["cover", "fit-blur"],
                    help="cover=铺满(比例相符时零裁剪)；fit-blur=模糊延展铺底+整图完整居中")
    ap.add_argument("--subtitle", default="", help="副标题（下部1/3处小字介绍，≤100字，可选）")
    ap.add_argument("--jpeg", action="store_true", help="输出 JPG(质量95)")
    args = ap.parse_args()

    theme = args.theme.strip()
    # 主题可为空（用户要求）：图片本身已带文字时，不添加任何主题文字

    # 1. 背景：整图铺满目标分辨率
    # 按 EXIF 方向先转正，避免手机竖图被当作横图处理。
    bg = ImageOps.exif_transpose(Image.open(args.bg)).convert("RGB")
    if bg.width < args.width or bg.height < args.height:
        log(f"[warn] 背景 {bg.width}x{bg.height} 小于输出 {args.width}x{args.height}，"
            f"将放大（画质受损，建议换更清晰的图）")
    if args.mode == "fit-blur":
        base = bg.resize((args.width, args.height), Image.LANCZOS)
        base = base.filter(ImageFilter.GaussianBlur(60))
        scale_fit = min(args.width / bg.width, args.height / bg.height)
        fg = bg.resize((math.ceil(bg.width * scale_fit), math.ceil(bg.height * scale_fit)),
                       Image.LANCZOS)
        canvas = base
        canvas.paste(fg, ((args.width - fg.width) // 2, (args.height - fg.height) // 2))
    else:
        scale = max(args.width / bg.width, args.height / bg.height)
        bg = bg.resize((math.ceil(bg.width * scale), math.ceil(bg.height * scale)), Image.LANCZOS)
        x0 = (bg.width - args.width) // 2
        y0 = (bg.height - args.height) // 2
        canvas = bg.crop((x0, y0, x0 + args.width, y0 + args.height))
    if args.dim > 0:
        overlay = Image.new("RGB", canvas.size, (0, 0, 0))
        canvas = Image.blend(canvas, overlay, min(1.0, max(0.0, args.dim)))
    draw = ImageDraw.Draw(canvas)

    # 2. 主题文字（可空：为空则图片上不加任何文字，直接使用原图）
    if theme or args.title_secondary:
        if args.title_style == "clean-yellow":
            settings = normalize_title_settings({"font": args.title_font, "color": args.title_color, "size": args.title_font_size})
            layout = draw_yellow_title(canvas, unified_title(theme, args.title_secondary), settings)
        elif args.title_style == "poster":
            layout = draw_poster_title(canvas, theme, args.title_secondary)
        else:
            layout = draw_center_title(canvas, theme)
        log("[title-layout] " + json.dumps(layout, ensure_ascii=False))
    else:
        log("[info] 主题为空：不加任何主题文字，直接使用原图（图片自带文字）")
    font_path, font_index, _ = find_font()

    # 3. 副标题（可选，下部 1/3 处）
    if args.subtitle:
        draw_subtitle(canvas, args.subtitle.strip(), font_path, font_index,
                      args.width, args.height)

    # 4. 校验 + 输出
    if args.jpeg:
        canvas.save(args.out, "JPEG", quality=95)
    else:
        canvas.save(args.out, "PNG")
    im = Image.open(args.out)
    log(f"[ok] 输出 {args.out} {im.size[0]}x{im.size[1]}")


if __name__ == "__main__":
    main()
