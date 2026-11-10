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
# DN2026_0113: 배치 측정(Pillow BASIC, 커닝 미적용)과 실제 렌더(FFmpeg 6.1 HarfBuzz 커닝)는
# 문장마다 다르게 어긋난다. 임의 문장 400건 실측: 폭 차이 -17~+12px, 박스 이탈 6건(최대 4px).
# 줄바꿈은 박스보다 WRAP_MARGIN 만큼 좁게 하고(이탈 0건 확인), 폭 일치 검사는 허용치를 넓힌다.
# 박스 이탈 검사(verify_mask containment)는 그대로 엄격하다.
WRAP_MARGIN = 24
MEASURE_TOLERANCE = 24
LAYOUT_REVISION = 2  # 배치 결과가 바뀌는 변경마다 올린다(캐시 대본 manifest 무효화)


def policy_name():
    name = os.getenv("CAPTION_LAYOUT_POLICY", LEGACY)
    if name not in (LEGACY, ADAPTIVE):
        raise ValueError("unknown_caption_policy")
    return name


def adaptive():
    return policy_name() == ADAPTIVE
