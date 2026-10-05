"""Daily publication hero policy (GOC rotation paused on 2026-10-05)."""


def select_daily_hero(day=None):
    # GOC random rotation is paused; keep the date argument for existing callers.
    # day = day or datetime.now(ZoneInfo("Asia/Seoul")).date()
    # return Random(f"hero-v1:{day.isoformat()}").choice(("EDT", "GOC"))
    return "EDT"
