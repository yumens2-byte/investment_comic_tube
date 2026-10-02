"""Source provenance and deterministic, typed market claims."""
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from src.validation import ValidationError
from src.pipeline_control import fingerprint

KEYS = ('TNX', 'VIX', 'NASDAQ', 'SPX', 'DXY', 'GOLD', 'OIL')
REQUIRED = KEYS[:5]
LABELS = {'TNX': '미 10년물 금리', 'VIX': 'VIX', 'NASDAQ': '나스닥',
          'SPX': 'S&P 500', 'DXY': '달러인덱스', 'GOLD': '금 선물', 'OIL': 'WTI 선물'}
TOKEN = re.compile(r'\{\{FACT:([A-Z]+)\.(close|change_pct)\}\}')
# 지표 이름 자체에 포함된 숫자. 수치 주장이 아니므로 숫자 검사 전에 제거한다.
LABEL_NUMERALS = re.compile(r'10\s*년물|S\s*&\s*P\s*500')
# 수량 주장 검사(2026-10-03 오탐 수정).
# - 한자어 숫자 한 글자 + 원/배는 '구원', '영원', '일원' 같은 일반 어휘와 구분할 수 없어
#   두 글자 이상이거나 자릿수(십·백·천·만·억)를 포함할 때만 수량으로 본다.
# - 단어 앞뒤 경계를 요구해 '지배', '영원한' 같은 단어 내부 일치를 막는다.
_PARTICLE = r'(?:으로|에서|까지|이나|이|가|을|를|은|는|의|로|에|도|만|씩|와|과|나|대|째)?'
_SINO = r'[영일이삼사오육칠팔구]'
_SINO_MULTI = rf'(?:{_SINO}?[십백천만억][영일이삼사오육칠팔구십백천만억]*|{_SINO}{{2,}})'
_NATIVE = r'(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)'
NUMBER = re.compile(
    r'\d'
    rf'|(?<![가-힣A-Za-z])(?:{_SINO_MULTI}|{_NATIVE})\s*(?:퍼센트|프로|배|포인트|달러|원|bp|%){_PARTICLE}(?![가-힣])'
    rf'|(?<![가-힣A-Za-z]){_SINO}\s*(?:퍼센트|프로|포인트|달러|bp|%){_PARTICLE}(?![가-힣])'
)
LABEL_SYNONYMS = {
    'TNX': r'(?:미\s*)?(?:10\s*년물\s*)?(?:국채\s*)?금리|(?:미\s*)?10\s*년물',
    'VIX': r'VIX|공포\s*지수|변동성\s*지수',
    'NASDAQ': r'나스닥(?:\s*지수)?',
    'SPX': r'S\s*&\s*P\s*500|S\s*&\s*P|에스앤피',
    'DXY': r'달러\s*인덱스|달러\s*지수',
    'GOLD': r'금\s*선물|금값|금',
    'OIL': r'WTI(?:\s*선물)?|국제\s*유가|유가',
}
# 단위는 토큰 밖에서 쓸 수 없다.
UNIT_OUTSIDE = re.compile(r'퍼센트|%')
DIRECTION_WORDS = r'(?:상승|하락|보합|급등|급락)'
# 토큰은 방향을 이미 포함한다. 토큰 직후 방향어는 중복이거나 모순이므로 차단한다.
DIRECTION_AFTER_TOKEN = re.compile(r'\}\}\s*(?:[가-힣]{0,2}\s*)?' + DIRECTION_WORDS)


def decimal(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError()
        return result
    except Exception as exc:
        raise ValidationError('market_nonfinite_value') from exc


def display(value):
    return format(decimal(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP), 'f')


def validate_provenance(snapshot, now=None):
    import exchange_calendars as xcals
    import pandas as pd
    now = now or datetime.now(ZoneInfo('America/New_York'))
    local = now.astimezone(ZoneInfo('America/New_York'))
    calendar = xcals.get_calendar('XNYS')
    sessions = calendar.sessions_in_range(pd.Timestamp(local.date()).normalize() - pd.Timedelta(days=20),
                                           pd.Timestamp(local.date()).normalize())
    completed = [s for s in sessions if calendar.session_close(s).to_pydatetime() <= now]
    if not completed:
        raise ValidationError('market_calendar_unavailable')
    expected = completed[-1].date()
    for key in KEYS:
        metric = snapshot.get(key) or {}
        try:
            if metric.get('close') is None or decimal(metric['close']) <= 0:
                raise ValidationError('market_close_missing')
            observed = datetime.fromisoformat(metric['observed_date']).date()
            if observed > local.date():
                raise ValidationError('market_future_observation')
            if not metric.get('source_symbol') or not metric.get('source'):
                raise ValidationError('market_source_missing')
            lag = len([s for s in sessions if observed < s.date() <= expected])
            if lag > 1:
                raise ValidationError('market_stale_observation')
            if observed > expected and metric.get('observation_kind') == 'daily_close':
                raise ValidationError('market_uncompleted_close')
            metric['stale_sessions'] = lag
            if metric.get('change_pct') is not None:
                prev = metric.get('prev_close_raw')
                if prev is None or decimal(prev) <= 0:
                    raise ValidationError('market_change_without_previous')
                change = (decimal(metric['close_raw']) / decimal(prev) - 1) * 100
                if display(change) != display(metric['change_pct']):
                    raise ValidationError('market_change_mismatch')
        except (KeyError, ValueError, TypeError, ValidationError) as exc:
            if key in REQUIRED:
                raise ValidationError(f'{key}: {exc}') from exc
            snapshot[key] = {'close': None, 'change_pct': None, 'source': None}
    return fingerprint(snapshot)


def fact(key, field, snapshot):
    metric = snapshot.get(key) or {}
    value = metric.get(field)
    if key not in KEYS or value is None:
        raise ValidationError('market_fact_missing')
    number = decimal(value)
    if field == 'change_pct':
        direction = '상승' if number > 0 else '하락' if number < 0 else '보합'
        return f'{LABELS[key]} {display(abs(number))}% {direction}'
    unit = '%' if key == 'TNX' else '달러' if key in ('GOLD', 'OIL') else ''
    return f'{LABELS[key]} {display(number)}{unit}'


def check_line(line):
    """토큰 대입 전 문장의 수치·단위·방향 규칙 위반 사유를 반환한다. 통과 시 None."""
    if DIRECTION_AFTER_TOKEN.search(line):
        return 'market_direction_duplicated_after_fact'
    remainder = LABEL_NUMERALS.sub('', TOKEN.sub('', line))
    if '{{' in remainder or NUMBER.search(remainder):
        return 'unregistered_market_numeric_claim'
    if UNIT_OUTSIDE.search(remainder):
        return 'market_unit_outside_fact'
    return None


def strip_label_before_fact(line):
    """토큰 직전의 같은 지표명(+조사)을 제거한다. 토큰이 지표명을 다시 대입하므로
    '10년물 금리 {{FACT:TNX.close}}' → '미 10년물 금리 미 10년물 금리 5.29%' 중복을 막는다."""
    for key, synonyms in LABEL_SYNONYMS.items():
        line = re.sub(rf'(?<![가-힣A-Za-z])(?:{synonyms})\s*(?:은|는|이|가)?\s*(?=\{{\{{FACT:{key}\.)', '', line)
    return line


def resolve(line, snapshot):
    reason = check_line(line)
    if reason:
        raise ValidationError(reason)
    line = strip_label_before_fact(line)
    return TOKEN.sub(lambda m: fact(m[1], m[2], snapshot), line)


def direction_facts(snapshot):
    """프롬프트용 지표별 방향(숫자 없음). change_pct가 없는 지표는 제외한다."""
    parts = []
    for key in KEYS:
        value = (snapshot.get(key) or {}).get('change_pct')
        if value is None:
            continue
        try:
            number = decimal(value)
        except ValidationError:
            continue
        direction = '상승' if number > 0 else '하락' if number < 0 else '보합'
        parts.append(f'{LABELS[key]}={direction}')
    return ', '.join(parts)


def token_catalog(snapshot):
    """프롬프트용 사용 가능 토큰 목록. 값이 있는 지표만 노출한다."""
    items = []
    for key in KEYS:
        metric = snapshot.get(key) or {}
        for field, meaning in (('close', '수준'), ('change_pct', '등락률과 방향')):
            if metric.get(field) is not None:
                items.append(f'{{{{FACT:{key}.{field}}}}}={LABELS[key]} {meaning}')
    return '; '.join(items)


def prompt_contract(snapshot=None):
    contract = ('\n[수치 규칙] 시장 숫자·단위(%, 퍼센트)는 직접 쓰지 말 것. 사실은 '
                '{{FACT:TNX.close}}, {{FACT:VIX.close}}, {{FACT:SPX.change_pct}} 같은 토큰만 사용. '
                '토큰은 프로그램이 지표명·수치·단위·방향으로 대입하므로 토큰 앞뒤에 지표명이나 '
                '상승/하락을 반복하지 말 것. 토큰 바깥 숫자·수량(세 배, 두 배 등) 주장 금지.')
    if snapshot:
        catalog = token_catalog(snapshot)
        if catalog:
            contract += f' 사용 가능 토큰: {catalog}.'
        directions = direction_facts(snapshot)
        if directions:
            contract += (f' 오늘 방향 사실: {directions}. 상승·하락 같은 방향 표현을 쓸 때는 '
                         '이 방향과 반드시 일치시킬 것.')
    return contract
