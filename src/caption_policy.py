"""Shared body-caption policy. Adaptive layout is opt-in until visual review."""
import os
from pathlib import Path

LEGACY = "legacy-v1"
ADAPTIVE = "caption-layout-v1"
FONT_PATH = Path("/usr/share/fonts/opentype/noto/NotoSansCJKkr-Bold.otf")
FONT_SHA256 = "26d0c6748500a0444844280b308f5b62c7ae92ac6c6ac88148e502dd211eb52a"
BOX = {"x": 90, "y": 1146, "width": 900, "height": 320}
CANDIDATES = ((60, 3), (60, 4), (58, 4), (56, 4))
BORDER = 5
LINE_SPACING = 12


def policy_name():
    name = os.getenv("CAPTION_LAYOUT_POLICY", LEGACY)
    if name not in (LEGACY, ADAPTIVE):
        raise ValueError("unknown_caption_policy")
    return name


def adaptive():
    return policy_name() == ADAPTIVE
