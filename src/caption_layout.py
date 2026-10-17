"""Lossless, font-measured body captions and FFmpeg geometry verification."""
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
import json
import re
import subprocess
import unicodedata

from PIL import Image, ImageFont
from fontTools.ttLib import TTFont

from src import caption_policy as policy


class LayoutError(ValueError):
    pass


def canonical(text):
    return " ".join(unicodedata.normalize("NFC", text).split())


def fact_phrases(snapshot, text):
    from src.market_facts import fact, KEYS
    return [value for key in KEYS for field in ('close', 'change_pct')
            if (snapshot.get(key) or {}).get(field) is not None
            for value in [fact(key, field, snapshot)] if value in text]


def font_identity():
    try:
        digest = sha256(policy.FONT_PATH.read_bytes()).hexdigest()
    except OSError as exc:
        raise LayoutError("caption_font_missing") from exc
    if digest != policy.FONT_SHA256:
        raise LayoutError("caption_font_checksum_mismatch")
    return digest


@lru_cache(maxsize=1)
def _charset(digest):
    with TTFont(str(policy.FONT_PATH)) as font:
        return frozenset(font.getBestCmap())


@lru_cache(maxsize=3)
def _font(size, digest):
    # BASIC layout avoids optional host-dependent RAQM shaping.
    return ImageFont.truetype(str(policy.FONT_PATH), size, layout_engine=ImageFont.Layout.BASIC)


def _atoms(text, protected):
    protected = sorted({canonical(p) for p in protected if canonical(p)}, key=lambda p: (-len(p), p))
    if not protected:
        return text.split()
    pattern = re.compile("|".join(re.escape(p) for p in protected))
    result, start = [], 0
    for match in pattern.finditer(text):
        # Bind an attached Korean particle to its preceding fact as well.
        end = match.end()
        while end < len(text) and not text[end].isspace():
            end += 1
        if match.start() < start:
            continue
        prefix = text[start:match.start()]
        # Bind a preceding unspaced word rather than inventing whitespace.
        parts = prefix.split()
        attached = parts.pop() if parts and prefix and not prefix[-1].isspace() else ''
        result.extend(parts)
        result.append(attached + text[match.start():end])
        start = end
    result.extend(text[start:].split())
    return result


def _wrap(atoms, font, max_lines):
    widths = {}
    for start in range(len(atoms)):
        for end in range(start + 1, len(atoms) + 1):
            width = float(font.getlength(" ".join(atoms[start:end]))) + 2 * policy.BORDER
            if width > policy.BOX["width"]:
                break
            widths[start, end] = width

    @lru_cache(None)
    def solve(start, remaining):
        if start == len(atoms):
            return 0.0, ()
        if remaining == 0:
            return None
        choices = []
        for end in range(start + 1, len(atoms) + 1):
            if (start, end) not in widths:
                break
            tail = solve(end, remaining - 1)
            if tail is not None:
                score = (policy.BOX["width"] - widths[start, end]) ** 2 + tail[0]
                choices.append((score, (end,) + tail[1]))
        return min(choices) if choices else None

    for count in range(1, max_lines + 1):
        solution = solve(0, count)
        if solution is not None:
            lines, start = [], 0
            for end in solution[1]:
                lines.append(" ".join(atoms[start:end]))
                start = end
            return lines
    return None


def build_layout(text, protected=()):
    text = canonical(text)
    if not text or len(text) > 500:
        raise LayoutError("caption_text_empty_or_too_long")
    digest = font_identity()
    if any(ord(char) not in _charset(digest) for char in text if not char.isspace()):
        raise LayoutError("caption_unsupported_glyph")
    atoms = _atoms(text, protected)
    if canonical(" ".join(atoms)) != text:
        raise LayoutError("caption_text_changed")
    for size, line_limit in policy.CANDIDATES:
        font = _font(size, digest)
        lines = _wrap(atoms, font, line_limit)
        if lines is None:
            continue
        # Conservative line box; final FFmpeg mask is the authoritative gate.
        glyph_height = max(font.getbbox(line)[3] - font.getbbox(line)[1] for line in lines)
        height = len(lines) * glyph_height + (len(lines) - 1) * policy.LINE_SPACING + 2 * policy.BORDER
        if height > policy.BOX["height"]:
            continue
        return {
            "schema_version": 1, "policy_version": policy.ADAPTIVE,
            "canonical_text_hash": sha256(text.encode()).hexdigest(),
            "font_sha256": digest, "font_face_index": 0, "font_size_px": size,
            "box": dict(policy.BOX), "lines": lines, "line_count": len(lines),
            "protected": list(protected),
            "measured_widths_px": [float(font.getlength(line)) + 2 * policy.BORDER for line in lines],
        }
    raise LayoutError("caption_layout_overflow")


def validate_layout(text, layout):
    if not isinstance(layout, dict) or build_layout(text, layout.get("protected", [])) != layout:
        raise LayoutError("caption_manifest_changed")
    return layout


def drawtext_filter(layout, caption_file, *, mask=False):
    box = layout["box"]
    font = _font(layout['font_size_px'], layout['font_sha256'])
    heights = [font.getbbox(line)[3] - font.getbbox(line)[1] for line in layout['lines']]
    total = sum(heights) + (len(heights) - 1) * policy.LINE_SPACING + 2 * policy.BORDER
    top = box['y'] + (box['height'] - total) / 2 + policy.BORDER
    filters = []
    # FFmpeg's multiline step includes font ascent/descent and can exceed the
    # visible glyph height. Position each preserved line explicitly instead.
    for index, (line, height) in enumerate(zip(layout['lines'], heights, strict=True)):
        line_file = caption_file.with_name(f'{caption_file.stem}-line-{index}.txt')
        line_file.write_text(line, encoding='utf-8')
        center = top + height / 2
        filters.append(
            f"drawtext=fontfile='{policy.FONT_PATH.as_posix()}':textfile='{line_file.as_posix()}':"
            f"expansion=none:fontcolor=white:fontsize={layout['font_size_px']}:borderw={policy.BORDER}:"
            f"bordercolor={'white' if mask else 'black'}:"
            f"x={box['x']}+({box['width']}-text_w)/2:y={center}-text_h/2")
        top += height + policy.LINE_SPACING
    return ','.join(filters)


def verify_mask(text, layout, directory):
    """Render text on black; white stroke shares final-render stroke geometry."""
    validate_layout(text, layout)
    directory.mkdir(parents=True, exist_ok=True)
    name = sha256(json.dumps(layout, sort_keys=True).encode()).hexdigest()
    caption = directory / f"{name}.txt"
    caption.write_text("\n".join(layout["lines"]), encoding="utf-8")
    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=1080x1920",
             "-vf", drawtext_filter(layout, caption, mask=True), "-frames:v", "1", "-threads", "1",
             "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        raise LayoutError("caption_mask_render_failed") from exc
    with Image.open(BytesIO(result.stdout)) as image:
        # A threshold rejects compression/background noise, retaining stroke pixels.
        bbox = image.convert("L").point(lambda p: 255 if p > 20 else 0).getbbox()
    box = layout["box"]
    if (not bbox or bbox[0] < box["x"] or bbox[1] < box["y"] or
            bbox[2] > box["x"] + box["width"] or bbox[3] > box["y"] + box["height"]):
        raise LayoutError("caption_render_outside_box")
    predicted = max(layout["measured_widths_px"])
    # Pillow advances and FFmpeg stroke rasterization differed by up to 5px
    # in reviewed mixed/Korean fixtures. Geometry containment above is strict.
    if abs((bbox[2] - bbox[0]) - predicted) > 8:
        raise LayoutError("caption_measurement_mismatch")
    (directory / f"{name}.png").write_bytes(result.stdout)
    return list(bbox)
