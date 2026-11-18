"""공개 로그·artifact 용 식별자 마스킹 — DN2026_0117 publisher 에서 분리(DN2026_0118).

레포가 공개라 Actions 로그·annotation·artifact 를 외부에서 볼 수 있다(STD-SEC-01).
원장(DB)에는 원문을 그대로 저장하고, 로그·artifact 에는 앞 3자 + *** 만 남긴다.
"""


def mask_id(value) -> str:
    """식별자 → 앞 3자 + ***. 빈 값은 '-'."""
    text = str(value or "")
    return f"{text[:3]}***" if text else "-"


def redact(err, *ids) -> str:
    """예외 문자열(요청 URL 포함)에 든 식별자를 마스킹. 6자 미만 값은 제외, 긴 값부터 치환."""
    text = str(err)
    for v in sorted({str(i) for i in ids if i and len(str(i)) >= 6}, key=len, reverse=True):
        text = text.replace(v, mask_id(v))
    return text
