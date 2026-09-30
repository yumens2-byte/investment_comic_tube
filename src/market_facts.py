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
# Quantified Korean claims are forbidden outside deterministic fact templates.
NUMBER = re.compile(r'\d|(?:영|일|이|삼|사|오|육|칠|팔|구|십|백|천|만|억|한|두|세|네)\s*(?:퍼센트|프로|배|포인트|달러|원|bp|%)')


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


def resolve(line, snapshot):
    remainder = TOKEN.sub('', line)
    if '{{' in remainder or NUMBER.search(remainder):
        raise ValidationError('unregistered_market_numeric_claim')
    if re.search(r'상승|하락|보합|퍼센트|%|급등|급락', remainder):
        raise ValidationError('market_direction_or_unit_outside_fact')
    return TOKEN.sub(lambda m: fact(m[1], m[2], snapshot), line)


def prompt_contract():
    return ('\n시장 숫자·상승/하락 주장은 직접 쓰지 말 것. 사실은 '
            '{{FACT:TNX.close}}, {{FACT:VIX.close}}, {{FACT:SPX.change_pct}} 같은 토큰만 사용. '
            '토큰은 프로그램이 지표명·수치·단위·방향으로 대입한다. 토큰 바깥 숫자·수량 주장 금지.')
