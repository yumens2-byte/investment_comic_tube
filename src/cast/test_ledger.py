"""cast_runs 원장 테스트 — 가짜 Supabase 체인, DB 실호출 없음 (DN2026_0114)."""
import pytest

import src.cast.ledger as L


class Res:
    def __init__(self, data=None, count=None):
        self.data = data or []
        self.count = count


class FakeQuery:
    """table().select().eq()...execute() 체인 — 행 목록에 필터를 적용한다."""

    def __init__(self, db, op, payload=None, **kw):
        self.db, self.op, self.payload, self.kw = db, op, payload, kw
        self.filters = []
        self.orders, self._limit, self._negate = [], None, False  # DN2026_0115

    @property
    def not_(self):  # DN2026_0115 — postgrest .not_.is_(col, "null")
        self._negate = True
        return self

    def is_(self, k, v):
        assert v == "null"
        neg, self._negate = self._negate, False
        self.filters.append(lambda r: (r.get(k) is None) != neg)
        return self

    def order(self, k, desc=False):
        self.orders.append((k, desc))
        return self

    def eq(self, k, v):
        self.filters.append(lambda r: r.get(k) == v)
        return self

    def neq(self, k, v):
        self.filters.append(lambda r: r.get(k) != v)
        return self

    def gte(self, k, v):
        self.filters.append(lambda r: str(r.get(k)) >= v)
        return self

    def lte(self, k, v):
        self.filters.append(lambda r: str(r.get(k)) <= v)
        return self

    def in_(self, k, vs):
        self.filters.append(lambda r: r.get(k) in vs)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _match(self):
        return [r for r in self.db.rows if all(f(r) for f in self.filters)]

    def execute(self):
        self.db.calls.append((self.op, self.payload, self.kw))
        if self.db.fail:
            raise self.db.fail
        if self.op == "select":
            rows = self._match()
            for k, desc in reversed(self.orders):  # DN2026_0115
                rows = sorted(rows, key=lambda r: str(r.get(k) or ""), reverse=desc)
            if self._limit is not None:
                rows = rows[:self._limit]
            return Res([dict(r) for r in rows], len(rows) if self.kw.get("count") else None)
        if self.op == "update":
            for r in self._match():
                r.update(self.payload)
            return Res()
        if self.op == "upsert":
            for r in self.db.rows:
                if r["run_id"] == self.payload["run_id"]:
                    r.update(self.payload)
                    return Res()
            self.db.rows.append({"cost_usd": 0, **self.payload})
            return Res()
        raise AssertionError(self.op)


class FakeTable:
    def __init__(self, db):
        self.db = db

    def select(self, cols, count=None):
        return FakeQuery(self.db, "select", count=count)

    def update(self, payload):
        return FakeQuery(self.db, "update", payload)

    def upsert(self, payload, on_conflict=None):
        return FakeQuery(self.db, "upsert", payload, on_conflict=on_conflict)


class FakeDB:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []
        self.fail = None


@pytest.fixture
def db(monkeypatch):
    d = FakeDB()
    monkeypatch.setattr(L, "_table", lambda: FakeTable(d))
    return d


def test_open_run_new_row_upsert(db):
    assert L.open_run("r1", "2026-11-21", "T3", "shadow", {"SPX": {"close": 1}}) == 0.0
    op, payload, kw = db.calls[-1]
    assert op == "upsert" and kw["on_conflict"] == "run_id"
    assert "cost_usd" not in payload and payload["status"] == "generating"


def test_open_run_rerun_keeps_cost_and_date(db):
    db.rows.append({"run_id": "r1", "cost_usd": 1.8, "status": "gen_failed", "run_date_kst": "2026-11-21"})
    assert L.open_run("r1", "2026-11-22", "T3", "shadow", {}) == 1.8   # 기존 누적 반환
    assert db.rows[0]["cost_usd"] == 1.8 and db.rows[0]["run_date_kst"] == "2026-11-21"
    assert db.rows[0]["status"] == "generating"


@pytest.mark.parametrize("status", ["uploading", "published"])
def test_open_run_blocks_locked_row(db, status):
    """R6-01 #2 — Re-run all jobs가 published 행을 generating으로 되돌리지 못한다."""
    db.rows.append({"run_id": "r1", "cost_usd": 2.52, "status": status, "run_date_kst": "2026-11-21"})
    with pytest.raises(L.DuplicatePublish):
        L.open_run("r1", "2026-11-21", "T3", "live", {})
    assert db.rows[0]["status"] == status


def test_claim_upload_blocks_same_run_id_rerun(db):
    """R6-01 #1 — 업로드 후 mark 실패로 uploading이 남은 자기 행도 재업로드를 막는다."""
    db.rows.append({"run_id": "r1", "run_date_kst": "2026-11-21", "status": "uploading"})
    with pytest.raises(L.DuplicatePublish):
        L.claim_upload("r1", "2026-11-21")


def test_add_cost_accumulates(db):
    db.rows.append({"run_id": "r1", "cost_usd": 0})
    assert L.add_cost("r1", 0.36) == 0.36
    assert L.add_cost("r1", 0.36) == 0.72
    assert db.rows[0]["cost_usd"] == 0.72


def test_add_cost_missing_row_fail_closed(db):
    with pytest.raises(L.CastLedgerUnavailable):
        L.add_cost("nope", 0.36)


def test_month_spent_month_boundary(db):
    db.rows += [
        {"run_id": "a", "run_date_kst": "2026-10-31", "cost_usd": 9.0},
        {"run_id": "b", "run_date_kst": "2026-11-01", "cost_usd": 2.5},
        {"run_id": "c", "run_date_kst": "2026-11-30", "cost_usd": 1.5},
        {"run_id": "d", "run_date_kst": "2026-12-01", "cost_usd": 7.0},
    ]
    assert L.month_spent_usd("2026-11-21") == 4.0
    assert L._month_bounds("2028-02-10") == ("2028-02-01", "2028-02-29")


def test_mark_sets_status_and_fields(db):
    db.rows.append({"run_id": "r1", "status": "generating"})
    L.mark("r1", "gen_failed", error="GenerationError")
    assert db.rows[0]["status"] == "gen_failed" and db.rows[0]["error"] == "GenerationError"
    assert "updated_at" in db.rows[0]


def test_claim_upload_blocks_same_day_duplicate(db):
    db.rows += [
        {"run_id": "old", "run_date_kst": "2026-11-21", "status": "published"},
        {"run_id": "new", "run_date_kst": "2026-11-21", "status": "generated"},
    ]
    with pytest.raises(L.DuplicatePublish):
        L.claim_upload("new", "2026-11-21")
    assert db.rows[1]["status"] == "generated"


def test_claim_upload_ok_marks_uploading(db):
    db.rows += [
        {"run_id": "old", "run_date_kst": "2026-11-14", "status": "published"},
        {"run_id": "new", "run_date_kst": "2026-11-21", "status": "generated"},
    ]
    L.claim_upload("new", "2026-11-21")
    assert db.rows[1]["status"] == "uploading"


def test_claim_upload_unique_violation_converted(db, monkeypatch):
    db.rows.append({"run_id": "new", "run_date_kst": "2026-11-21", "status": "generated"})
    orig = FakeQuery.execute

    def racing(self):
        if self.op == "update":
            raise RuntimeError("duplicate key value violates unique constraint (23505)")
        return orig(self)

    monkeypatch.setattr(FakeQuery, "execute", racing)
    with pytest.raises(L.DuplicatePublish):
        L.claim_upload("new", "2026-11-21")


def test_next_episode_no(db):
    db.rows += [{"run_id": "a", "status": "published"}, {"run_id": "b", "status": "published"},
                {"run_id": "c", "status": "gen_failed"}]
    assert L.next_episode_no() == 3


@pytest.mark.parametrize("call", [
    lambda: L.open_run("r", "2026-11-21", "T3", "live", None),
    lambda: L.add_cost("r", 1),
    lambda: L.month_spent_usd("2026-11-21"),
    lambda: L.mark("r", "generated"),
    lambda: L.claim_upload("r", "2026-11-21"),
    lambda: L.next_episode_no(),
])
def test_db_errors_fail_closed(db, call):
    db.fail = ConnectionError("network down")
    with pytest.raises(L.CastLedgerUnavailable):
        call()


def test_client_unavailable_fail_closed(monkeypatch):
    import src.db_client as dbc
    monkeypatch.setattr(dbc, "get_client", lambda: (_ for _ in ()).throw(RuntimeError("no env")))
    with pytest.raises(L.CastLedgerUnavailable):
        L.month_spent_usd("2026-11-21")


def test_claim_upload_uses_stored_run_date(db):
    """R6-02 #3 — 재실행으로 plan 날짜가 바뀌어도 잠금은 원장 최초 날짜 기준."""
    db.rows += [
        {"run_id": "old", "run_date_kst": "2026-11-21", "status": "published"},
        {"run_id": "r2", "run_date_kst": "2026-11-21", "status": "generated"},
    ]
    with pytest.raises(L.DuplicatePublish):
        L.claim_upload("r2", "2026-11-22")      # plan 재계산 날짜를 넘겨도 저장 날짜(11-21)로 판정


def test_generate_pre_open_failure_keeps_published_row(db, monkeypatch, tmp_path):
    """R6-02 #1 — 실제 ledger + FakeDB: open_run 이전 실패가 published 행을 강등하지 않는다."""
    import json as _json
    import src.cast.__main__generate as gen_main
    from src.cast import generate as G
    db.rows.append({"run_id": "900", "run_date_kst": "2026-11-21", "status": "published", "cost_usd": 2.52})
    monkeypatch.chdir(tmp_path)
    (tmp_path / "artifacts_cast").mkdir()
    plan = [{"cut": i + 1, "role": "ESTABLISH_THREAT", "characters": ["Debt Titan"]} for i in range(3)]
    pf = tmp_path / "artifacts_cast" / "cast_plan.json"
    pf.write_text(_json.dumps({"tier": "T3", "plan": plan, "run_id": "900", "run_date_kst": "2026-11-21"}))
    monkeypatch.setenv("CAST_PLAN_FILE", str(pf))
    monkeypatch.setenv("CAST_REFSHEET_FILE", str(tmp_path / "none.json"))
    monkeypatch.setenv("CAST_EXECUTION_MODE", "live")
    monkeypatch.setenv("FAL_AI_KEY", "test-key")
    monkeypatch.setenv("CAST_MONTH_CAP_USD", "3")          # 2.52 + 1.08 > 3 → open_run 전 cost_cap_month
    monkeypatch.delenv("CAST_CUT_SEC", raising=False)
    monkeypatch.setattr(G, "generate_cut", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fal 호출")))
    with pytest.raises(G.GenerationError, match="cost_cap_month"):
        gen_main.main()
    assert db.rows[0]["status"] == "published"


def test_generate_rerun_of_published_run_blocked_without_demotion(db, monkeypatch, tmp_path):
    """R6-01 #2 — 실제 ledger: published run 재실행은 open_run에서 차단, 상태 유지."""
    import json as _json
    import src.cast.__main__generate as gen_main
    from src.cast import generate as G
    db.rows.append({"run_id": "901", "run_date_kst": "2026-11-21", "status": "published", "cost_usd": 0})
    monkeypatch.chdir(tmp_path)
    (tmp_path / "artifacts_cast").mkdir()
    plan = [{"cut": i + 1, "role": "ESTABLISH_THREAT", "characters": ["Debt Titan"]} for i in range(3)]
    pf = tmp_path / "artifacts_cast" / "cast_plan.json"
    pf.write_text(_json.dumps({"tier": "T3", "plan": plan, "run_id": "901", "run_date_kst": "2026-11-21"}))
    monkeypatch.setenv("CAST_PLAN_FILE", str(pf))
    monkeypatch.setenv("CAST_REFSHEET_FILE", str(tmp_path / "none.json"))
    monkeypatch.setenv("CAST_EXECUTION_MODE", "live")
    monkeypatch.setenv("FAL_AI_KEY", "test-key")
    for k in ("CAST_MONTH_CAP_USD", "CAST_RUN_CAP_USD", "CAST_CUT_SEC"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(G, "generate_cut", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fal 호출")))
    with pytest.raises(L.DuplicatePublish):
        gen_main.main()
    assert db.rows[0]["status"] == "published"


def test_claim_upload_without_own_row_fail_closed(db):
    """R6-03 #1 — open_run 미실행(자기 행 없음)이면 잠금 없이 통과하지 않는다."""
    with pytest.raises(L.CastLedgerUnavailable):
        L.claim_upload("ghost", "2026-11-21")
    assert db.rows == []


@pytest.mark.parametrize("case,expect", [
    ("duration", "duration_out_of_range"), ("run_cap", "cost_cap_run"), ("fal_key", "FAL_AI_KEY"),
    ("month_spent_down", "down"), ("open_run_check_down", "open_run_check")])
def test_generate_pre_open_failures_never_touch_ledger(db, monkeypatch, tmp_path, case, expect):
    """R6-03 #6 — 실제 ledger + FakeDB: open_run 이전/진입 실패 경로 전부 published 행 불변."""
    import json as _json
    import src.cast.__main__generate as gen_main
    from src.cast import generate as G
    db.rows.append({"run_id": "902", "run_date_kst": "2026-11-21", "status": "published", "cost_usd": 0})
    monkeypatch.chdir(tmp_path)
    (tmp_path / "artifacts_cast").mkdir()
    plan = [{"cut": i + 1, "role": "ESTABLISH_THREAT", "characters": ["Debt Titan"]} for i in range(3)]
    pf = tmp_path / "artifacts_cast" / "cast_plan.json"
    pf.write_text(_json.dumps({"tier": "T3", "plan": plan, "run_id": "902", "run_date_kst": "2026-11-21"}))
    monkeypatch.setenv("CAST_PLAN_FILE", str(pf))
    monkeypatch.setenv("CAST_REFSHEET_FILE", str(tmp_path / "none.json"))
    monkeypatch.setenv("CAST_EXECUTION_MODE", "live")
    monkeypatch.setenv("FAL_AI_KEY", "test-key")
    for k in ("CAST_MONTH_CAP_USD", "CAST_RUN_CAP_USD", "CAST_CUT_SEC"):
        monkeypatch.delenv(k, raising=False)
    if case == "duration":
        monkeypatch.setenv("CAST_CUT_SEC", "5")              # 3×5=15s < 16s
    elif case == "run_cap":
        monkeypatch.setenv("CAST_RUN_CAP_USD", "1")
    elif case == "fal_key":
        monkeypatch.delenv("FAL_AI_KEY", raising=False)
        monkeypatch.delenv("FAL_KEY", raising=False)
    elif case == "month_spent_down":
        monkeypatch.setattr(L, "month_spent_usd", lambda d: (_ for _ in ()).throw(L.CastLedgerUnavailable("down")))
    elif case == "open_run_check_down":
        orig = FakeQuery.execute

        def flaky(self):
            if self.op == "select" and self.kw.get("count") is None and any(True for _ in self.filters) \
                    and self.db.calls and self.db.calls[-1][0] == "select":
                raise RuntimeError("db down")
            return orig(self)
        monkeypatch.setattr(FakeQuery, "execute", flaky)
    monkeypatch.setattr(G, "generate_cut", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fal 호출")))
    with pytest.raises((G.GenerationError, L.CastLedgerUnavailable), match=expect):
        gen_main.main()
    assert db.rows[0]["status"] == "published"


# ---------- DN2026_0115 P2 스토리 레이어 ----------

def test_record_story_updates_columns(db):
    db.rows.append({"run_id": "r1", "status": "generating"})
    L.record_story("r1", "Debt Titan", {"season_week": 2}, {"version": 1, "beats": []})
    assert db.rows[0]["villain"] == "Debt Titan"
    assert db.rows[0]["story_state"] == {"season_week": 2}
    assert db.rows[0]["script"]["version"] == 1 and db.rows[0]["status"] == "generating"


def test_record_story_fail_closed(db):
    db.fail = RuntimeError("down")
    with pytest.raises(L.CastLedgerUnavailable):
        L.record_story("r1", "Debt Titan", {}, {})


def _story_rows():
    return [
        {"run_id": "a", "run_date_kst": "2026-11-07", "created_at": "1", "status": "published",
         "villain": "Debt Titan", "story_state": {"season_week": 1}},
        {"run_id": "b", "run_date_kst": "2026-11-14", "created_at": "1", "status": "published",
         "villain": None, "story_state": {"season_week": 2, "villain": "Debt Titan"}},
        {"run_id": "c", "run_date_kst": "2026-11-21", "created_at": "1", "status": "generated",
         "villain": "Debt Titan", "story_state": {"season_week": 3}},
        {"run_id": "d", "run_date_kst": "2026-11-28", "created_at": "1", "status": "published",
         "villain": "Debt Titan", "story_state": None},
        {"run_id": "e", "run_date_kst": "2026-11-28", "created_at": "2", "status": "gen_failed",
         "villain": "Debt Titan", "story_state": {"season_week": 9}},
    ]


def test_fetch_last_story_published_only(db):
    db.rows.extend(_story_rows())
    prev = L.fetch_last_story(include_generated=False)
    assert prev == {"run_id": "b", "run_date_kst": "2026-11-14", "villain": "Debt Titan",
                    "story_state": {"season_week": 2, "villain": "Debt Titan"}}


def test_fetch_last_story_include_generated(db):
    db.rows.extend(_story_rows())
    prev = L.fetch_last_story(include_generated=True)
    assert prev["run_id"] == "c" and prev["story_state"] == {"season_week": 3}


def test_fetch_last_story_created_at_tiebreak(db):
    db.rows.extend([
        {"run_id": "x", "run_date_kst": "2026-11-21", "created_at": "1", "status": "published",
         "villain": "Debt Titan", "story_state": {"n": 1}},
        {"run_id": "y", "run_date_kst": "2026-11-21", "created_at": "2", "status": "published",
         "villain": "Debt Titan", "story_state": {"n": 2}},
    ])
    assert L.fetch_last_story(include_generated=False)["run_id"] == "y"


def test_fetch_last_story_none(db):
    assert L.fetch_last_story(include_generated=True) is None


def test_fetch_last_story_fail_closed(db):
    db.fail = RuntimeError("down")
    with pytest.raises(L.CastLedgerUnavailable):
        L.fetch_last_story(include_generated=False)


def test_fetch_last_story_client_unavailable(monkeypatch):
    def boom():
        raise L.CastLedgerUnavailable("no env")
    monkeypatch.setattr(L, "_table", boom)
    with pytest.raises(L.CastLedgerUnavailable):
        L.fetch_last_story(include_generated=False)
