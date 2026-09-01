"""세로형(1080x1920) 숏폼 영상 렌더링.

세 가지 모드:
  - 스토리보드 모드: scenes(이미지+자막+내레이션 음성)를 이어붙여 30초 내외 영상 생성.
    각 장면 길이는 내레이션 오디오 길이를 따라가며, 오디오가 없으면 고정 길이를 쓴다.
  - 슬라이드쇼 모드: 이미지 목록만 있을 때 고정 길이로 이어붙인다.
  - 텍스트카드 모드(최종 폴백): 이미지가 하나도 없거나 위 두 모드가 실패하면
    검은 배경 + 텍스트로 대체한다. 파이프라인은 렌더링 실패로 죽지 않는다.
"""

from __future__ import annotations

import json
import logging
import os
import random
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

SEGMENT_DURATION_SEC = 5.0
MIN_SEGMENT_SEC = 3.5
AUDIO_TAIL_PAD_SEC = 0.6

# Lyria 등 생성 도구가 .mp4 컨테이너로 오디오를 내보내는 경우가 있어 포함한다.
# ffmpeg 는 컨테이너와 무관하게 오디오 스트림을 뽑아낼 수 있다.
BGM_EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".mp4"}

# 빌런별 BGM 파일명 접두사. 예: bgm_debt_titan_01.mp4
BGM_VILLAIN_SLUG = {
    "Debt Titan": "debt_titan",
    "Chaos Reaper": "chaos_reaper",
    "Bull Brute": "bull_brute",
}
BGM_COMMON_PREFIX = "bgm_common"

# --- 아웃트로(엔드카드) ---
# 인트로가 아니라 아웃트로에만 넣는다. 쇼츠는 0~3초 이탈이 결정적이라
# 앞에 공통 브랜드 화면을 두면 훅이 밀려 이탈률이 올라간다.
OUTRO_DURATION_SEC = 2.0
OUTRO_ZOOM_SPEED = 0.0008
OUTRO_MAX_ZOOM = 1.08
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
LOGO_STEM = "logo"          # assets/brand/logo.png 는 오버레이 전용
LOGO_WIDTH_RATIO = 0.18     # 화면 폭 대비 로고 크기
LOGO_MARGIN_PX = 60

# 로고 배경 자동 제거.
# 이미지 생성 모델은 투명 배경을 만들지 못해 단색 배경이 딸려 오는데,
# 그대로 오버레이하면 표지 위에 색 사각형이 얹힌다.
# 알파 채널이 없는 로고에 한해 지정 색을 키잉해 투명화한다.
LOGO_CHROMA_KEY = os.getenv("LOGO_CHROMA_KEY", "0xFF00FF")
LOGO_CHROMA_SIMILARITY = "0.30"
LOGO_CHROMA_BLEND = "0.10"

# Ken Burns 효과: 같은 이미지를 여러 비트에 재사용해도 화면이 단조롭지 않도록
# 장면마다 줌 방향을 교대한다 (비용 절감을 위한 이미지 재사용의 보완책).
KEN_BURNS_FPS = 30
KEN_BURNS_MAX_ZOOM = 1.12
KEN_BURNS_SPEED = 0.0012

# --- 오프닝 훅(0~3초) 전용 연출 파라미터 ---
# 쇼츠 피드에서 스크롤을 멈추게 하는 구간이라 일반 비트와 다른 규칙을 쓴다.
HOOK_MAX_SEC = 3.0            # 비기능 요구사항: 훅은 3초를 절대 넘기지 않는다
HOOK_PUNCH_START_ZOOM = 1.35  # 크게 시작해 급속히 빠지는 펀치인
HOOK_PUNCH_SPEED = 0.11       # 프레임당 축소량 (일반 0.0012 대비 훨씬 공격적)
HOOK_SHAKE_PX = 18            # 화면 흔들림 진폭 (1080px 대비 1.7%)
HOOK_SHAKE_PAD = 1.10         # 흔들림 여유분만큼 확대 후 크롭
HOOK_FONT_SIZE = 86
HOOK_FONT_COLOR = "0xFFEE00"  # 강렬 옐로우
HOOK_WRAP_CHARS = 13          # 한국어 기준 1080px 에 들어가는 글자수
ATEMPO_MAX = 1.5              # 3초 초과 내레이션 압축 상한

SFX_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".mp4"}

# 한국어 자막 폰트.
# drawtext 에 fontfile 을 지정하지 않으면 fontconfig 가 기본 폰트를 고르는데,
# 그 폰트에 한글 글리프가 없으면 전부 두부(□)로 렌더링된다.
# fonts-noto-cjk 가 설치돼 있어도 이 현상이 발생하므로 경로를 명시해야 한다.
KR_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-DemiLight.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
]

# 경로가 배포판마다 다를 수 있으므로 파일명 패턴으로도 탐색한다
KR_FONT_PATTERNS = ["NotoSansCJK*", "NotoSerifCJK*", "NanumGothic*", "*CJK*"]
FONT_SEARCH_ROOTS = ["/usr/share/fonts", "/usr/local/share/fonts", str(Path.home() / ".fonts")]


def _font_from_fc_match() -> str | None:
    """fontconfig 에게 한글을 실제로 그릴 수 있는 폰트를 직접 물어본다."""
    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{file}", ":lang=ko"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    path = result.stdout.strip()
    return path if path and Path(path).exists() else None


def find_kr_font() -> str | None:
    """한글 글리프를 가진 폰트 경로를 찾는다.

    drawtext 에 fontfile 을 지정하지 않으면 fontconfig 가 기본 폰트를 고르는데,
    그 폰트에 한글 글리프가 없으면 전부 두부(□)로 렌더링된다.
    fonts-noto-cjk 가 설치돼 있어도 발생하므로 경로를 반드시 명시해야 한다.

    탐색 순서:
      1) KR_FONT_PATH 환경변수 (운영 중 강제 지정용)
      2) 알려진 고정 경로
      3) 폰트 디렉터리 재귀 탐색 (배포판별 경로 차이 흡수)
      4) fc-match 질의 (최후의 수단)
    """
    override = os.getenv("KR_FONT_PATH")
    if override and Path(override).exists():
        return override

    for candidate in KR_FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate

    for root in FONT_SEARCH_ROOTS:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for pattern in KR_FONT_PATTERNS:
            for path in sorted(root_path.rglob(pattern)):
                if path.suffix.lower() in {".ttc", ".otf", ".ttf"}:
                    return str(path)

    matched = _font_from_fc_match()
    if matched:
        return matched

    logger.warning("kr_font_not_found -- 한글 자막이 두부로 렌더링될 수 있다")
    return None


def _escape_drawtext(text: str) -> str:
    """구형 인라인 drawtext 용 이스케이프. 신규 경로는 textfile 을 쓴다."""
    return text.replace("\\", r"\\").replace("'", r"\'").replace(":", r"\:")


def _wrap_korean(text: str, width: int) -> str:
    """한국어 자막을 폭에 맞춰 줄바꿈한다.

    기존 자막 깨짐 원인 중 하나가 40자 이상 한 줄이 화면 밖으로 넘친 것이었다.
    어절 단위로 나누되 한 어절이 폭보다 길면 강제로 자른다.
    """
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        while len(word) > width:
            lines.append(word[:width])
            word = word[width:]
        current = word
    if current:
        lines.append(current)
    return "\n".join(lines[:2])  # 최대 2줄


def _write_caption_file(text: str, path: Path, width: int) -> Path:
    """자막을 파일로 쓴다.

    인라인 text= 대신 textfile= 을 쓰는 이유:
      1) '%' 가 strftime 으로 확장돼 텍스트가 깨지던 문제를 원천 차단
         (실제 사고: "금리가 4.76%까지" -> 깨짐)
      2) 따옴표/콜론/쉼표 이스케이프가 아예 필요 없어진다
    """
    path.write_text(_wrap_korean(text, width), encoding="utf-8")
    return path


def _run_ffmpeg(cmd: list[str], ffmpeg_log: Path, append: bool = False) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    mode = "a" if append else "w"
    with ffmpeg_log.open(mode, encoding="utf-8") as f:
        f.write("$ " + " ".join(cmd) + "\n\nSTDOUT\n" + result.stdout + "\nSTDERR\n" + result.stderr + "\n\n")
    if result.returncode != 0:
        logger.error(
            "ffmpeg_step_failed exit_code=%s stderr_tail=%s",
            result.returncode,
            result.stderr[-1000:].replace("\n", " | "),
        )
        raise subprocess.CalledProcessError(result.returncode, cmd)


def _probe_duration(path: str) -> float | None:
    """미디어 파일 길이(초)를 반환한다. 실패 시 None."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-print_format", "json",
        path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return None
        return float(json.loads(result.stdout)["format"]["duration"])
    except (ValueError, KeyError, json.JSONDecodeError, FileNotFoundError):
        return None


def _has_audio_stream(path: str) -> bool:
    """파일에 실제 오디오 스트림이 있는지 확인한다.

    .mp4 를 허용하면서 필요해졌다. 오디오가 없는 파일을 BGM/SFX 로 넘기면
    filter_complex 가 'matches no streams' 로 실패해 렌더링 전체가 무너진다.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0",
        path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=15)
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return "audio" in result.stdout


def _audio_candidates(directory: Path, extensions: set[str]) -> list[Path]:
    """오디오 스트림이 실제로 있는 파일만 골라 반환한다."""
    files = [p for p in sorted(directory.iterdir()) if p.suffix.lower() in extensions]
    usable = []
    for path in files:
        if _has_audio_stream(str(path)):
            usable.append(path)
        else:
            logger.warning("audio_file_skipped path=%s reason=no_audio_stream", path.name)
    return usable


def _find_bgm(villain: str | None = None) -> str | None:
    """BGM 디렉터리에서 배경음 파일을 하나 무작위로 고른다.

    파일이 없으면 None -- 이 경우 내레이션만 남는다.
    저작권 리스크를 피하기 위해 코드가 음원을 자동으로 받아오지 않는다.
    무작위 선택은 X 안티봇 원칙(동일 패턴 반복 금지)에 따른 것이다.
    """
    bgm_dir = Path(os.getenv("BGM_DIR", "assets/bgm"))
    if not bgm_dir.is_dir():
        return None

    candidates = _audio_candidates(bgm_dir, BGM_EXTENSIONS)
    if not candidates:
        return None

    # 1순위: 이번 회차 빌런 전용 곡 (bgm_debt_titan_01 등)
    slug = BGM_VILLAIN_SLUG.get(villain or "")
    if slug:
        matched = [p for p in candidates if p.stem.startswith(f"bgm_{slug}")]
        if matched:
            chosen = random.choice(matched)
            logger.info("bgm_matched villain=%s file=%s", villain, chosen.name)
            return str(chosen)

    # 2순위: 공통 곡
    common = [p for p in candidates if p.stem.startswith(BGM_COMMON_PREFIX)]
    if common:
        chosen = random.choice(common)
        logger.info("bgm_matched villain=%s file=%s (common)", villain, chosen.name)
        return str(chosen)

    # 3순위: 아무거나
    chosen = random.choice(candidates)
    logger.info("bgm_matched villain=%s file=%s (any)", villain, chosen.name)
    return str(chosen)


def _find_sfx(name: str | None) -> str | None:
    """훅 효과음을 찾는다. 유형별 파일이 없으면 임의 1개, 그것도 없으면 None.

    BGM/레퍼런스와 동일한 opt-in 슬롯 패턴이다. 코드가 음원을 자동으로
    받아오지 않으므로 저작권 리스크가 없고, 파일이 없어도 무음으로 동작한다.
    """
    sfx_dir = Path(os.getenv("SFX_DIR", "assets/sfx"))
    if not sfx_dir.is_dir():
        return None
    candidates = _audio_candidates(sfx_dir, SFX_EXTENSIONS)
    if not candidates:
        return None
    if name:
        exact = [p for p in candidates if p.stem == name]
        if exact:
            return str(exact[0])
    return str(random.choice(candidates))


def _find_brand_asset(stem_is_logo: bool) -> str | None:
    """브랜드 디렉터리에서 아웃트로 표지 또는 로고를 찾는다.

    'logo' 라는 이름의 파일은 오버레이 전용으로 예약하고,
    나머지 이미지는 전부 아웃트로 표지 후보로 본다(여러 장이면 무작위 회전).
    """
    brand_dir = Path(os.getenv("BRAND_DIR", "assets/brand"))
    if not brand_dir.is_dir():
        return None

    images = [p for p in sorted(brand_dir.iterdir()) if p.suffix.lower() in IMAGE_EXTENSIONS]
    if stem_is_logo:
        logos = [p for p in images if p.stem.lower() == LOGO_STEM]
        return str(logos[0]) if logos else None

    covers = [p for p in images if p.stem.lower() != LOGO_STEM]
    return str(random.choice(covers)) if covers else None


def _has_alpha_channel(path: str) -> bool:
    """이미지에 알파 채널이 있는지 확인한다."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=pix_fmt",
        "-of", "csv=p=0",
        path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=15)
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    pix_fmt = result.stdout.strip()
    return any(tag in pix_fmt for tag in ("rgba", "bgra", "argb", "abgr", "ya", "pal8"))


def _render_outro_segment(segment_path: Path, ffmpeg_log: Path, append: bool) -> bool:
    """아웃트로 엔드카드를 렌더링한다. 표지 이미지가 없으면 건너뛴다."""
    cover = _find_brand_asset(stem_is_logo=False)
    if not cover:
        logger.info("outro_skipped reason=no_cover_image")
        return False

    logo = _find_brand_asset(stem_is_logo=True)
    frames = max(1, int(OUTRO_DURATION_SEC * KEN_BURNS_FPS))
    zoom = f"min(1+{OUTRO_ZOOM_SPEED}*on,{OUTRO_MAX_ZOOM})"
    base = (
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s=1080x1920:fps={KEN_BURNS_FPS}"
    )

    cmd = ["ffmpeg", "-y", "-loop", "1", "-t", f"{OUTRO_DURATION_SEC:.3f}", "-i", cover]
    cmd += ["-f", "lavfi", "-t", f"{OUTRO_DURATION_SEC:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]

    if logo:
        # 로고는 AI 생성 글자가 뭉개지는 문제를 피하려고 고정 PNG 를 오버레이한다
        cmd += ["-i", logo]
        logo_w = int(1080 * LOGO_WIDTH_RATIO)

        # 알파가 없으면 단색 배경이 사각형으로 얹히므로 키잉으로 제거한다
        if _has_alpha_channel(logo):
            logo_chain = f"[2:v]scale={logo_w}:-1[lg]"
        else:
            logger.info("logo_chroma_key_applied color=%s", LOGO_CHROMA_KEY)
            logo_chain = (
                f"[2:v]colorkey={LOGO_CHROMA_KEY}:{LOGO_CHROMA_SIMILARITY}:{LOGO_CHROMA_BLEND},"
                f"format=rgba,scale={logo_w}:-1[lg]"
            )

        filter_complex = (
            f"[0:v]{base}[bg];"
            f"{logo_chain};"
            f"[bg][lg]overlay=W-w-{LOGO_MARGIN_PX}:H-h-{LOGO_MARGIN_PX}[vout]"
        )
    else:
        filter_complex = f"[0:v]{base}[vout]"

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "1:a",
        "-c:v", "libx264", "-t", f"{OUTRO_DURATION_SEC:.3f}", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        str(segment_path),
    ]
    _run_ffmpeg(cmd, ffmpeg_log, append=append)
    logger.info("outro_rendered cover=%s logo=%s duration=%.2f",
                Path(cover).name, Path(logo).name if logo else "none", OUTRO_DURATION_SEC)
    return True


def _hook_video_filter(duration: float, caption_file: Path) -> str:
    """훅 전용 영상 필터: 흔들림 + 펀치인 + 중앙 대형 자막."""
    frames = max(1, int(duration * KEN_BURNS_FPS))
    pad_w = int(1080 * HOOK_SHAKE_PAD)
    pad_h = int(1920 * HOOK_SHAKE_PAD)
    off_x = (pad_w - 1080) // 2
    off_y = (pad_h - 1920) // 2
    zoom = f"max({HOOK_PUNCH_START_ZOOM}-{HOOK_PUNCH_SPEED}*on,1.0)"

    font = find_kr_font()
    font_opt = f"fontfile='{font}':" if font else ""

    return (
        f"scale={pad_w}:{pad_h},"
        # 비동기 주파수(9Hz/11Hz)로 흔들어 기계적 반복감을 없앤다
        f"crop=1080:1920:x='{off_x}+{HOOK_SHAKE_PX}*sin(2*PI*t*9)'"
        f":y='{off_y}+{HOOK_SHAKE_PX}*cos(2*PI*t*11)',"
        f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s=1080x1920:fps={KEN_BURNS_FPS},"
        f"drawtext={font_opt}textfile='{caption_file.as_posix()}':expansion=none:"
        f"fontcolor={HOOK_FONT_COLOR}:fontsize={HOOK_FONT_SIZE}:"
        "borderw=8:bordercolor=black:line_spacing=14:"
        "x=(w-text_w)/2:y=(h-text_h)/2"
    )


def _hook_audio_filter(
    narration_dur: float | None,
    target: float,
    audio_index: int,
    sfx_index: int | None,
) -> str:
    """훅 전용 오디오 필터: 3초 초과 내레이션을 속도로 압축하고 SFX를 얹는다.

    입력 0번은 이미지(영상)이므로 오디오 스트림 인덱스는 1부터 시작한다.
    인덱스를 하드코딩하면 SFX 유무에 따라 매핑이 어긋난다.
    """
    chain = []
    if narration_dur and narration_dur > target:
        tempo = min(ATEMPO_MAX, narration_dur / target)
        chain.append(f"[{audio_index}:a]atempo={tempo:.3f},aformat=channel_layouts=stereo[n]")
    else:
        chain.append(f"[{audio_index}:a]aformat=channel_layouts=stereo[n]")

    if sfx_index is not None:
        chain.append(f"[{sfx_index}:a]aformat=channel_layouts=stereo,volume=0.7[s]")
        chain.append("[n][s]amix=inputs=2:duration=first:dropout_transition=0[aout]")
    else:
        chain.append("[n]anull[aout]")
    return ";".join(chain)


def _apply_bgm(video_path: str, bgm_path: str, ffmpeg_log: Path) -> None:
    """기존 오디오(내레이션) 위에 BGM을 낮은 볼륨으로 깐다.

    내레이션 트랙이 무음이면 결과적으로 BGM만 들린다.
    실패 시 원본을 그대로 두어 영상 자체는 보존한다.
    """
    mixed_path = f"{video_path}.bgm.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-stream_loop", "-1", "-i", bgm_path,
        "-filter_complex",
        "[1:a]volume=0.18[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0[aout]",
        "-map", "0:v:0", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        mixed_path,
    ]
    try:
        _run_ffmpeg(cmd, ffmpeg_log, append=True)
    except Exception as e:  # noqa: BLE001 - BGM 합성 실패는 원본 유지로 폴백
        logger.warning("bgm_mix_failed reason=%s: %s -- keeping original audio", type(e).__name__, e)
        if os.path.exists(mixed_path):
            os.remove(mixed_path)
        return

    os.replace(mixed_path, video_path)
    logger.info("bgm_applied source=%s", bgm_path)


def _font_opt() -> str:
    font = find_kr_font()
    return f"fontfile='{font}':" if font else ""


def _render_text_card(script_data: dict, output_path: str, ffmpeg_log: Path) -> None:
    ep_num = script_data.get("episode", 101)
    villain = script_data.get("villain", "Unknown")
    text_content = f"EDT Universe Ep.{ep_num}\nVs. {villain}"

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=8",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-vf", (
            f"drawtext={_font_opt()}text='{_escape_drawtext(text_content)}':"
            "fontcolor=orange:fontsize=64:x=(w-text_w)/2:y=(h-text_h)/2"
        ),
        "-c:v", "libx264", "-t", "8", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        output_path,
    ]
    try:
        _run_ffmpeg(cmd, ffmpeg_log)
    except FileNotFoundError:
        ffmpeg_log.write_text(
            "$ " + " ".join(cmd) + "\n\nERROR\nffmpeg executable was not found in PATH\n",
            encoding="utf-8",
        )
        logger.exception("render_failed reason=ffmpeg_not_found ffmpeg_log=%s", ffmpeg_log)
        raise


def _ken_burns_filter(duration: float, zoom_in: bool) -> str:
    """장면 길이에 맞춘 zoompan 필터식을 만든다."""
    frames = max(1, int(duration * KEN_BURNS_FPS))
    if zoom_in:
        z = f"min(1+{KEN_BURNS_SPEED}*on,{KEN_BURNS_MAX_ZOOM})"
    else:
        z = f"max({KEN_BURNS_MAX_ZOOM}-{KEN_BURNS_SPEED}*on,1.0)"
    return (
        f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s=1080x1920:fps={KEN_BURNS_FPS}"
    )


def _render_hook_segment(
    image_path: str,
    caption: str,
    audio_path: str | None,
    sfx_name: str | None,
    segment_path: Path,
    ffmpeg_log: Path,
    append: bool,
) -> float:
    """오프닝 훅 장면을 렌더링한다. 반환값은 실제 장면 길이(초)."""
    duration = HOOK_MAX_SEC
    narration_dur = _probe_duration(audio_path) if audio_path else None

    caption_file = segment_path.parent / "hook_caption.txt"
    _write_caption_file(caption, caption_file, HOOK_WRAP_CHARS)

    sfx_path = _find_sfx(sfx_name)

    # 입력 0 = 이미지, 1 = 내레이션(또는 무음), 2 = SFX(있을 때만)
    cmd = ["ffmpeg", "-y", "-loop", "1", "-t", f"{duration:.3f}", "-i", image_path]
    if audio_path:
        cmd += ["-i", audio_path]
    else:
        cmd += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
    audio_index = 1
    sfx_index = None
    if sfx_path:
        cmd += ["-i", sfx_path]
        sfx_index = 2

    # -vf 와 -filter_complex 는 동시 사용할 수 없으므로 영상 필터도 filter_complex 안에 넣는다
    filter_complex = (
        f"[0:v]{_hook_video_filter(duration, caption_file)}[vout];"
        + _hook_audio_filter(narration_dur, duration, audio_index, sfx_index)
    )
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-t", f"{duration:.3f}", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        str(segment_path),
    ]
    _run_ffmpeg(cmd, ffmpeg_log, append=append)
    logger.info(
        "hook_rendered duration=%.2f narration=%.2f sfx=%s caption='%s'",
        duration, narration_dur or 0.0, sfx_path or "none", caption,
    )
    return duration


def _render_segment(
    image_path: str,
    caption: str,
    audio_path: str | None,
    duration: float,
    segment_path: Path,
    ffmpeg_log: Path,
    append: bool,
    zoom_in: bool = True,
) -> None:
    """이미지 1장 + (있으면) 내레이션으로 장면 하나를 렌더링한다.

    자막(drawtext)은 사용하지 않는다. 한국어 폰트/개행 처리에서 깨짐이 발생했고,
    내레이션 음성이 같은 내용을 전달하므로 화면에는 이미지만 남긴다.
    caption 인자는 호출부 호환을 위해 남겨두되 렌더링에는 쓰지 않는다.
    """
    video_filter = (
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"{_ken_burns_filter(duration, zoom_in)}"
    )

    cmd = ["ffmpeg", "-y", "-loop", "1", "-t", f"{duration:.3f}", "-i", image_path]
    if audio_path:
        cmd += ["-i", audio_path]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]

    cmd += [
        "-vf", video_filter,
        "-c:v", "libx264", "-t", f"{duration:.3f}", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        "-af", f"apad=whole_dur={duration:.3f}",
        str(segment_path),
    ]
    _run_ffmpeg(cmd, ffmpeg_log, append=append)


def _concat_segments(segment_paths: list[Path], output_path: str, tmp_dir: Path, ffmpeg_log: Path) -> None:
    filelist = tmp_dir / "filelist.txt"
    filelist.write_text("\n".join(f"file '{p.resolve()}'" for p in segment_paths), encoding="utf-8")
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(filelist),
        "-c", "copy",
        output_path,
    ]
    _run_ffmpeg(cmd, ffmpeg_log, append=True)


def _render_storyboard(scenes: list[dict], output_path: str, ffmpeg_log: Path) -> None:
    """스토리보드 장면 목록을 이어붙여 영상을 만든다.

    scenes 항목: {"image", "caption", "audio"}
    """
    tmp_dir = Path(output_path).parent / "_segments"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    segment_paths: list[Path] = []

    for idx, scene in enumerate(scenes):
        image_path = scene.get("image")
        if not image_path:
            continue

        audio_path = scene.get("audio")
        duration = SEGMENT_DURATION_SEC
        if audio_path:
            probed = _probe_duration(audio_path)
            if probed:
                duration = max(MIN_SEGMENT_SEC, probed + AUDIO_TAIL_PAD_SEC)

        segment_path = tmp_dir / f"segment_{idx}.mp4"
        if scene.get("is_hook"):
            _render_hook_segment(
                image_path=image_path,
                caption=scene.get("caption", ""),
                audio_path=audio_path,
                sfx_name=scene.get("sfx"),
                segment_path=segment_path,
                ffmpeg_log=ffmpeg_log,
                append=bool(segment_paths),
            )
            segment_paths.append(segment_path)
            continue

        _render_segment(
            image_path=image_path,
            caption=scene.get("caption", ""),
            audio_path=audio_path,
            duration=duration,
            segment_path=segment_path,
            ffmpeg_log=ffmpeg_log,
            append=bool(segment_paths),
            zoom_in=(idx % 2 == 0),
        )
        segment_paths.append(segment_path)

    if not segment_paths:
        raise ValueError("no renderable scenes")

    # 아웃트로는 실패해도 본편을 살린다(브랜딩은 부가 요소다)
    outro_path = tmp_dir / "segment_outro.mp4"
    try:
        if _render_outro_segment(outro_path, ffmpeg_log, append=True):
            segment_paths.append(outro_path)
    except Exception as e:  # noqa: BLE001
        logger.warning("outro_render_failed reason=%s: %s -- 본편만 사용", type(e).__name__, e)

    _concat_segments(segment_paths, output_path, tmp_dir, ffmpeg_log)


def _render_slideshow(script_data: dict, image_paths: list[str], output_path: str, ffmpeg_log: Path) -> None:
    """이미지만 있을 때 고정 길이 슬라이드쇼로 렌더링한다."""
    ep_num = script_data.get("episode", 101)
    villain = script_data.get("villain", "Unknown")
    narration = script_data.get("narration", "")

    captions = [f"EDT Universe Ep.{ep_num}\nVs. {villain}"]
    if narration:
        captions.append(narration)
    while len(captions) < len(image_paths):
        captions.append(captions[-1])

    scenes = [
        {"image": path, "caption": captions[idx], "audio": None}
        for idx, path in enumerate(image_paths)
    ]
    _render_storyboard(scenes, output_path, ffmpeg_log)


def render_video(
    script_data: dict,
    image_paths: list[str] | None = None,
    scenes: list[dict] | None = None,
) -> str:
    output_path = "output_short.mp4"
    if os.path.exists(output_path):
        os.remove(output_path)

    if scenes and any(s.get("image") for s in scenes):
        mode = "storyboard"
    elif image_paths:
        mode = "slideshow"
    else:
        mode = "text_card"
    logger.info("render_started output=%s mode=%s", output_path, mode)

    log_dir = Path(os.getenv("LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_log = log_dir / "ffmpeg.log"

    if mode == "storyboard":
        try:
            _render_storyboard(scenes, output_path, ffmpeg_log)
        except Exception as e:  # noqa: BLE001 - 스토리보드 실패는 텍스트카드로 폴백
            logger.warning(
                "storyboard_render_failed reason=%s: %s -- falling back to text_card",
                type(e).__name__, e,
            )
            _render_text_card(script_data, output_path, ffmpeg_log)
    elif mode == "slideshow":
        try:
            _render_slideshow(script_data, image_paths, output_path, ffmpeg_log)
        except Exception as e:  # noqa: BLE001 - 슬라이드쇼 실패는 텍스트카드로 폴백
            logger.warning(
                "slideshow_render_failed reason=%s: %s -- falling back to text_card",
                type(e).__name__, e,
            )
            _render_text_card(script_data, output_path, ffmpeg_log)
    else:
        _render_text_card(script_data, output_path, ffmpeg_log)

    bgm_path = _find_bgm(script_data.get("villain"))
    if bgm_path:
        _apply_bgm(output_path, bgm_path, ffmpeg_log)
    else:
        logger.info("bgm_skipped reason=no_bgm_file")

    duration = _probe_duration(output_path)
    logger.info(
        "render_finished output=%s bytes=%s duration=%s ffmpeg_log=%s",
        output_path,
        os.path.getsize(output_path),
        f"{duration:.2f}" if duration else "unknown",
        ffmpeg_log,
    )
    return output_path
