"""Install the reviewed Noto CJK font only when its exact SHA-256 matches."""
import hashlib
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.caption_policy import FONT_PATH, FONT_SHA256

URL = "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/OTF/Korean/NotoSansCJKkr-Bold.otf"


def main():
    if FONT_PATH.is_file() and hashlib.sha256(FONT_PATH.read_bytes()).hexdigest() == FONT_SHA256:
        print(f"caption font verified: {FONT_SHA256}")
        return
    with urllib.request.urlopen(URL, timeout=60) as response:
        data = response.read(20 * 1024 * 1024 + 1)
    if hashlib.sha256(data).hexdigest() != FONT_SHA256:
        raise RuntimeError("caption_font_download_checksum_mismatch")
    FONT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = FONT_PATH.with_suffix(".tmp")
    temporary.write_bytes(data)
    temporary.replace(FONT_PATH)
    print(f"caption font installed: {FONT_SHA256}")


if __name__ == "__main__":
    main()
