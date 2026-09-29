"""Reproducible daily random hero selection for a single publication slot."""
from datetime import datetime
from random import Random
from zoneinfo import ZoneInfo


def select_daily_hero(day=None):
    day = day or datetime.now(ZoneInfo("Asia/Seoul")).date()
    return Random(f"hero-v1:{day.isoformat()}").choice(("EDT", "GOC"))
