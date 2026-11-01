"""파이프라인 로그에서 결과/중단 사유를 뽑아 GitHub annotation 과 job summary 로 올린다.

2026-10-28 사고: 실패 원인(Gemini 429, input_context_changed)이 로그인해야 보이는
step 로그에만 있어 공개 화면에는 'exit code 1' 만 남았다. annotation 과 summary 는
공개되므로 이벤트 이름과 reason 만(시세 원문·비밀값 없이) 올린다.
항상 0 으로 종료해 원래 step 의 실패 판정을 바꾸지 않는다.

DN2026_0089(2026-10-30·31 사고): 대본 문장 탈락 사유가 공개 화면에 없어 artifact 를
받아야 원인을 알 수 있었다. 문장 번호·사유만 올린다(문장 원문 line= 은 올리지 않는다).
"""
import os
import re
import sys

VERSION = "1.1.0"

LINE = re.compile(r"^(?P<ts>\S+)\s+(?P<level>[A-Z]+)\s+(?P<logger>\S+)\s+(?P<event>[a-z_]+)(?P<rest>.*)$")
FIELD = re.compile(r"\b(reason|details|changed|status|video_id|reasons|track|video_pilot|beat|beats|type)="
                   r"(\{[^}]*\}|\[[^\]]*\]|\S+)")
# 이벤트 → (annotation 수준, 의미)
EVENTS = {
    "pipeline_aborted_validation": ("error", "검증 실패로 발행 중단"),
    "pipeline_control_hold": ("error", "운영 안전장치가 실행을 보류"),
    "duplicate_publish_blocked": ("notice", "오늘 이미 발행됨 -- 중복 발행 방지"),
    "pipeline_failed": ("error", "예외로 실패"),
    "operational_safety_failed": ("error", "운영 안전장치 초기화 실패"),
    "story_generation_aborted": ("warning", "스토리 생성 중단"),
    "story_fallback_blocked": ("error", "스토리 폴백 발행 차단"),
    "story_line_rejected": ("warning", "대본 문장 탈락(수정 요청 대상)"),
    "story_fact_gate_failed": ("error", "수정 후에도 탈락한 대본 문장"),
    "story_line_rule_fallback_applied": ("warning", "2번 문장을 규칙 문장으로 대체"),
    "story_line_rule_fallback_rejected": ("error", "2번 규칙 문장도 검사 탈락"),
    "hook_line_fallback_applied": ("warning", "훅을 규칙 문장으로 대체"),
    "image_generation_aborted": ("warning", "이미지 생성 중단"),
    "tts_aborted": ("warning", "TTS 중단"),
    "market_context_pinned": ("warning", "재시도: 첫 시도의 동결 시세를 재사용"),
    "pipeline_degraded": ("warning", "품질 저하 상태로 진행"),
    "pipeline_started": ("notice", "시작"),
    "pipeline_finished": ("notice", "완료"),
    "video_pilot_finished": ("notice", "파일럿 완료"),
    "budget_pilot_finished": ("notice", "파일럿 완료"),
}
# 사유 → 운영자가 할 일
HINTS = {
    "quota_exhausted": "Gemini 한도/지출 상한 소진. Google AI Studio 에서 quota·billing 을 확인한 뒤 재실행하세요.",
    "input_context_changed": "같은 날 동결된 입력과 현재 입력이 다릅니다. 트랙 변경 또는 동결 시세가 만료된 경우입니다.",
    "daily_hero_conflict": "오늘 이미 다른 히어로 트랙으로 claim 되었습니다.",
    "upload_unknown_manual_review_required": "업로드 결과가 불명확합니다. YouTube Studio 에서 중복 업로드 여부를 먼저 확인하세요.",
    "generation_limit_reached": "오늘 유료 생성 한도에 도달했습니다. 내일 다시 실행됩니다.",
    "duplicate_publish_blocked": "오늘 이미 발행된 회차가 있습니다. 재실행이 필요 없습니다.",
    "caption_over": "대본 문장이 자막 한도(legacy 19자×3줄)를 넘었습니다. 같은 날 재실행은 캐시된 같은 응답을 다시 검사하므로 결과가 같습니다(자막 정책 Variable 확인).",
    "fact_gate_rejected": "수정 호출 뒤에도 대본 문장이 사실·형식 규칙을 못 지켰습니다. story_fact_gate_failed 의 beats 와 story_line_rejected 사유를 확인하세요.",
}


def _escape(text):
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def parse(lines):
    events = []
    for raw in lines:
        match = LINE.match(raw.strip())
        if not match or match["event"] not in EVENTS:
            continue
        # DN2026_0089 R6-07-01: 'line=' 이후는 모델 원문이다. 원문 속 'beat='·'reason=' 이
        # 실제 값을 덮어쓰거나 원문 조각이 공개 annotation 으로 나가지 않도록 잘라 낸다.
        rest = match["rest"].split(" line=", 1)[0]
        fields = {}
        for key, value in FIELD.findall(rest):
            fields.setdefault(key, value[:300])
        events.append({"ts": match["ts"], "event": match["event"], "fields": fields})
    return events


def hints_for(events):
    # reason 값은 첫 단어까지만 잡히므로 이벤트 이름도 함께 대조한다
    text = " ".join(f"{e['event']} {e['fields']}" for e in events)
    # DN2026_0089 R6-07-03: 규칙 문장 대체로 회복된 회차에는 자막 초과 안내를 내지 않는다
    gate_failed = any(e["event"] == "story_fact_gate_failed" for e in events)
    return [hint for key, hint in HINTS.items()
            if key in text and (key != "caption_over" or gate_failed)]


def main(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            events = parse(handle)
    except OSError:
        print(f"::warning title=Pipeline log missing::{_escape(path)} 이 없습니다. 파이프라인 step 이 실행되지 않았거나 로그 설정 전에 실패했습니다.")
        return 0

    for event in events:
        level, meaning = EVENTS[event["event"]]
        if level == "notice" and event["event"] == "pipeline_started":
            continue
        detail = " ".join(f"{k}={v}" for k, v in event["fields"].items())
        print(f"::{level} title={event['event']}::{_escape(f'{meaning}. {detail}'.strip())}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        rows = ["## 파이프라인 결과", "", "| 시각 (UTC) | 이벤트 | 의미 | 세부 |", "|---|---|---|---|"]
        for event in events:
            detail = " ".join(f"`{k}={v}`" for k, v in event["fields"].items()).replace("|", "\\|")
            rows.append(f"| {event['ts']} | `{event['event']}` | {EVENTS[event['event']][1]} | {detail} |")
        if not events:
            rows.append("| - | - | 기록된 결과 이벤트 없음 | - |")
        hints = hints_for(events)
        if hints:
            rows += ["", "### 조치 안내", ""] + [f"- {hint}" for hint in hints]
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n".join(rows) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "logs/pipeline.log"))
