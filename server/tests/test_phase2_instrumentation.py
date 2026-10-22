"""Phase 2 tests: real execution is instrumented into task events.

No network, no LLM, no ERP. The graph is driven with fake callers, chat with a
fake model, and the scheduler with fake refresh/pipeline functions. Every event
is captured by a recording event service, so we assert that what the command
center will show is exactly what the backend actually did.

Run:  .venv/bin/python -m pytest tests/test_phase2_instrumentation.py -q
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.agents import graph
from app.agents.chat import execute_chat, run_chat
from app.agents.chat_tools import ChatTool, EmptyArgs
from app.services.events import set_event_service
from app.services.scheduler import DashboardScheduler

from test_agents_pipeline import (
    EPOCH,
    NOW,
    FakeCallerForTest,
    FakeDatabase,
    good_factory,
    lead_row,
    order_row,
    there_and_last_month,
)
from test_chat import FakeModel, plain_answer, tool_call


# ---------------------------------------------------------------------------
# recording event service
# ---------------------------------------------------------------------------


class RecordingEventService:
    def __init__(self):
        self.events: list[dict] = []

    async def emit(self, *, task_id, type, agent_id=None, payload=None):
        self.events.append(
            {"task_id": task_id, "type": type, "agent_id": agent_id, "payload": payload or {}}
        )
        return object()

    def types(self) -> list[str]:
        return [e["type"] for e in self.events]

    def of(self, event_type: str) -> list[dict]:
        return [e for e in self.events if e["type"] == event_type]

    # read API the endpoints/registry may touch
    async def last_status_by_agent(self, *_a, **_k):
        return {}

    async def get_events(self, *_a, **_k):
        return []

    async def recent_tasks(self, *_a, **_k):
        return []

    async def task_exists(self, *_a, **_k):
        return False

    async def ensure_indexes(self):
        return None


class BrokenEventService:
    """emit always raises - proving instrumentation cannot corrupt a run."""

    async def emit(self, **_kwargs):
        raise ConnectionError("event store is down")

    async def ensure_indexes(self):
        return None


# ---------------------------------------------------------------------------
# a bigger fixture so parallel branches have enough items to interleave
# ---------------------------------------------------------------------------


def big_db(per_branch: int = 6) -> FakeDatabase:
    quotes = [
        there_and_last_month(
            id=100 + i,
            amount=1000.0 + i,
            created_at=(NOW - timedelta(days=6)).isoformat(),
            customer={"id": 10 + i, "company_name": f"Co{i}"},
        )
        for i in range(per_branch)
    ]
    orders = [
        order_row(
            id=500 + i,
            total_amount=750.0 + i,
            invoice_date=(NOW - timedelta(days=35)).isoformat(),
            customer={"id": 20 + i, "company_name": f"Inv{i}"},
        )
        for i in range(per_branch)
    ]
    leads = [
        lead_row(zohoLeadId=f"L-{i}", firstName=f"Lead{i}")
        for i in range(per_branch)
    ]
    return FakeDatabase(quotes=quotes, orders=orders, payments=[], leads=leads)


def concurrency_probe(inner_factory, state):
    """A caller factory that records how many LLM calls run at once, and from
    which agent kinds - the only way to prove the branches are genuinely
    parallel and obey ONE shared bound."""

    def track(kind, coro_fn):
        async def run(messages):
            state["active"] += 1
            state["kinds"][kind] = state["kinds"].get(kind, 0) + 1
            state["peak"] = max(state["peak"], state["active"])
            if sum(1 for v in state["kinds"].values() if v > 0) >= 2:
                state["overlapping_kinds"] = True
            await asyncio.sleep(0.01)  # hold the slot so concurrency is real
            try:
                return await coro_fn(messages)
            finally:
                state["active"] -= 1
                state["kinds"][kind] -= 1

        class Wrapper:
            async def ainvoke(self, messages):
                return await run(messages)

        return Wrapper()

    def factory(kind):
        inner = inner_factory(kind)
        return track(kind, inner.ainvoke)

    return factory


# ---------------------------------------------------------------------------
# A. graph lifecycle + agent + per-item + cached events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graph_emits_task_and_agent_lifecycle_events():
    recorder = RecordingEventService()
    database = FakeDatabase(
        quotes=[there_and_last_month(id=1, amount=1000.0)],
        orders=[order_row(id=101, total_amount=750.0)],
        payments=[],
        leads=[lead_row()],
    )
    result = await graph.run_pipeline(
        run_id="task-run-1", database=database, caller_factory=good_factory(),
        now_epoch=EPOCH, events=recorder, trigger="manual",
    )

    # task_id is the real run_id
    assert all(e["task_id"] == "task-run-1" for e in recorder.events)
    assert recorder.of("task.created") and recorder.of("task.started")
    assert recorder.of("task.completed")
    for agent_id in ("negotiation", "collections", "lead_followup", "master"):
        assert any(e["agent_id"] == agent_id for e in recorder.of("agent.started")), agent_id
        assert any(e["agent_id"] == agent_id for e in recorder.of("agent.completed")), agent_id
    # per-item progress is real (fresh items are not cached)
    progress = recorder.of("agent.progress")
    assert progress and any(p["payload"].get("cached") is False for p in progress)
    # the task.completed summary carries the actual outcome, not decoration
    completion = recorder.of("task.completed")[0]
    assert completion["payload"]["counts"] == result["counts"]
    assert completion["payload"]["total_tokens"] >= 0
    assert "duration_ms" in completion["payload"]
    assert completion["payload"]["master_generated_by"] in ("model", "code_template")


@pytest.mark.asyncio
async def test_cached_items_emit_progress_without_an_llm_call():
    """Run twice: the second run's items are cached and must emit cached=true."""
    database = FakeDatabase(
        quotes=[there_and_last_month(id=1, amount=1000.0)],
        orders=[order_row(id=101, total_amount=750.0)],
        payments=[],
        leads=[lead_row()],
    )
    await graph.run_pipeline(run_id="seed", database=database, caller_factory=good_factory(), now_epoch=EPOCH)

    recorder = RecordingEventService()
    counting_calls: dict[str, int] = {}

    def counting_factory(kind):
        counting_calls[kind] = counting_calls.get(kind, 0) + 1
        return good_factory()(kind)

    await graph.run_pipeline(
        run_id="second", database=database, caller_factory=counting_factory,
        now_epoch=EPOCH + 60, events=recorder,
    )

    cached_progress = [p for p in recorder.of("agent.progress") if p["payload"].get("cached") is True]
    assert cached_progress, "cached items must still report real progress"
    # cached reuse costs no new LLM call for the sub-agents
    assert counting_calls.get("negotiation", 0) == 0
    assert counting_calls.get("collections", 0) == 0


@pytest.mark.asyncio
async def test_graph_failure_emits_task_failed_and_reraises():
    recorder = RecordingEventService()
    database = FakeDatabase(quotes=[], orders=[], payments=[], leads=[])

    async def explode(messages):
        raise RuntimeError("model is on fire")

    # break the load step so the whole run fails
    database.quotes_flat.find = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("mongo is down"))

    with pytest.raises(Exception):
        await graph.run_pipeline(
            run_id="fail-run", database=database, caller_factory=good_factory(),
            now_epoch=EPOCH, events=recorder,
        )
    assert recorder.of("task.failed")
    assert recorder.of("task.started")  # started before the failure
    assert not recorder.of("task.completed")


# ---------------------------------------------------------------------------
# B. parallel branches + a single shared concurrency bound
# ---------------------------------------------------------------------------


def test_graph_topology_fans_out_and_joins():
    compiled = graph.build_graph()
    pairs = {
        (getattr(edge, "source", None), getattr(edge, "target", None))
        for edge in compiled.get_graph().edges
    }
    # negotiation, collections and lead_followup all branch from load_signals
    # and all rejoin at group_by_customer.
    for node in ("negotiation", "collections", "lead_followup"):
        assert ("load_signals", node) in pairs
        assert (node, "group_by_customer") in pairs
    # the specialists are no longer chained to each other
    assert ("negotiation", "collections") not in pairs
    assert ("collections", "lead_followup") not in pairs


@pytest.mark.asyncio
async def test_branches_run_in_parallel_under_one_shared_bound(monkeypatch):
    monkeypatch.setattr(graph, "CONCURRENCY", 2)
    state = {"active": 0, "peak": 0, "kinds": {}, "overlapping_kinds": False}
    database = big_db(per_branch=5)  # 15 fresh items across 3 branches
    await graph.run_pipeline(
        run_id="parallel-run", database=database,
        caller_factory=concurrency_probe(good_factory(), state), now_epoch=EPOCH,
    )
    assert state["peak"] <= 2, "the shared semaphore is a system-wide bound, not per branch"
    assert state["overlapping_kinds"], "different specialists must run at the same time"


@pytest.mark.asyncio
async def test_one_specialist_failure_does_not_discard_the_others():
    recorder = RecordingEventService()
    base = good_factory()

    def factory(kind):
        if kind == "collections":
            async def boom(messages):
                raise RuntimeError("collections down")

            class Failing:
                async def ainvoke(self, messages):
                    raise RuntimeError("collections down")

            return Failing()
        return base(kind)

    database = FakeDatabase(
        quotes=[there_and_last_month(id=1, amount=1000.0)],
        orders=[order_row(id=101, total_amount=750.0)],
        payments=[],
        leads=[lead_row()],
    )
    result = await graph.run_pipeline(
        run_id="partial-run", database=database, caller_factory=factory,
        now_epoch=EPOCH, events=recorder,
    )
    # the other two specialists still produced and saved their drafts
    doc = database.agent_runs.saved[0]
    assert doc["negotiation"][0]["source"] == "agent"
    assert doc["lead_followup"][0]["lead_id"] == "L-1"
    # the failing collections item is recorded as an error, not silently lost
    assert "error" in doc["collections"][0]["flags"]
    # the run still reached the master + save (partial failure, not a crash)
    assert recorder.of("task.completed")
    assert any("collections" in (e["payload"].get("error", "")) for e in recorder.of("agent.failed")) or doc["errors"]


# ---------------------------------------------------------------------------
# observability robustness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_service_failure_does_not_corrupt_the_pipeline():
    """A completely broken event service must not change what the run saves."""
    database = FakeDatabase(
        quotes=[there_and_last_month(id=1, amount=1000.0)],
        orders=[order_row(id=101, total_amount=750.0)],
        payments=[],
        leads=[lead_row()],
    )
    result = await graph.run_pipeline(
        run_id="broken-events", database=database, caller_factory=good_factory(),
        now_epoch=EPOCH, events=BrokenEventService(),
    )
    assert result["counts"]["negotiation_total"] == 1
    assert database.agent_runs.saved[0]["run_id"] == "broken-events"


# ---------------------------------------------------------------------------
# C. chat turn + real tool events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_emits_task_and_tool_events_for_real_calls():
    recorder = RecordingEventService()
    set_event_service(recorder)

    async def read_tool(database=None):
        return {"count": 4}

    tool = ChatTool("fake_read", "Return a fixed count", EmptyArgs, read_tool)
    model = FakeModel([tool_call(), plain_answer("There are 4 open quotes.")])
    outcome = await run_chat(
        "thread-p2", "How many open quotes?", model=model, tools=[tool],
        database=_FakeDb(),
    )

    ids = {e["task_id"] for e in recorder.events}
    assert len(ids) == 1, "one unique task id per chat turn"
    assert recorder.of("task.created") and recorder.of("task.started")
    assert recorder.of("task.completed")
    assert recorder.of("agent.started") and recorder.of("agent.completed")
    started = recorder.of("tool.started")
    completed = recorder.of("tool.completed")
    assert started and completed
    assert started[0]["agent_id"] == "chat"
    assert completed[0]["payload"] == {"tool": "fake_read", "ok": True, "duration_ms": completed[0]["payload"]["duration_ms"]}
    # the streamed answer + validation are untouched
    assert outcome["text"] == "There are 4 open quotes."


@pytest.mark.asyncio
async def test_chat_records_a_failed_tool_call():
    recorder = RecordingEventService()
    set_event_service(recorder)

    async def broken_tool(database=None):
        raise ValueError("bad")

    tool = ChatTool("broken", "Always fails", EmptyArgs, broken_tool)
    model = FakeModel([tool_call(), plain_answer("I could not load that.")])
    await run_chat("thread-fail", "do it", model=model, tools=[tool], database=_FakeDb())

    completed = recorder.of("tool.completed")
    assert completed and completed[0]["payload"]["ok"] is False


# ---------------------------------------------------------------------------
# D. ERP refresh events
# ---------------------------------------------------------------------------


class _TinyDb:
    def __init__(self):
        self.agent_triggers = _Collection()
        self.agent_runs = _Collection()

    def __bool__(self):
        raise NotImplementedError  # mirror AsyncDatabase: never truth-tested


class _Collection:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    def find(self, *a, **k):
        return self

    def sort(self, *a, **k):
        return self

    async def to_list(self, n):
        return []


class _FakeRedis:
    def __init__(self):
        self.published = []

    async def publish(self, channel, message):
        self.published.append((channel, message))


def _make_scheduler(refresh_func, redis=None, pipeline=None):
    return DashboardScheduler(
        database=_TinyDb(),
        redis=redis or _FakeRedis(),
        refresh_func=refresh_func,
        pipeline_func=pipeline,
        snapshot_func=None,
        interval_seconds=60,
    )


@pytest.mark.asyncio
async def test_erp_sync_completed_event_from_real_counts():
    recorder = RecordingEventService()
    set_event_service(recorder)

    async def ok_refresh(database=None):
        return {
            "ok": True,
            "window": {"from": "2026-10-19", "to": "2026-10-20"},
            "quotes": {"new": 3, "updated": 1, "unchanged": 5},
            "orders": {"new": 2},
            "payments": {"new": 1},
        }

    scheduler = _make_scheduler(ok_refresh)
    report = await scheduler._job_refresh()
    assert report["ok"] is True
    assert recorder.of("erp.sync_started")
    completed = recorder.of("erp.sync_completed")
    assert completed
    payload = completed[0]["payload"]
    assert payload["new_quotes"] == 3 and payload["new_orders"] == 2 and payload["new_payments"] == 1
    assert payload["window"] == {"from": "2026-10-19", "to": "2026-10-20"}


@pytest.mark.asyncio
async def test_erp_sync_failed_event_on_exception_and_on_soft_error():
    recorder = RecordingEventService()
    set_event_service(recorder)

    async def raise_refresh(database=None):
        raise ConnectionError("ERP unreachable")

    await _make_scheduler(raise_refresh)._job_refresh()
    failed = recorder.of("erp.sync_failed")
    assert failed and failed[0]["payload"]["error_kind"] == "ConnectionError"
    # the exception message (which could carry a URL) never reaches the event
    assert "unreachable" not in str(recorder.events)

    recorder2 = RecordingEventService()
    set_event_service(recorder2)

    async def soft_fail(database=None):
        return {"ok": False, "error": "ERPError: token rejected"}

    await _make_scheduler(soft_fail)._job_refresh()
    assert recorder2.of("erp.sync_failed")[0]["payload"]["error_kind"] == "ERPError"


@pytest.mark.asyncio
async def test_scheduler_passes_trigger_to_the_pipeline():
    captured: dict = {}

    async def capture_pipeline(**kwargs):
        captured.update(kwargs)
        return {"run_id": kwargs.get("run_id"), "counts": {}}

    recorder = RecordingEventService()
    set_event_service(recorder)
    scheduler = _make_scheduler(
        refresh_func=lambda database=None: asyncio.sleep(0, {"ok": True}),
        pipeline=capture_pipeline,
    )
    await scheduler._run_agent("new_lead", NOW, new_leads=[{"x": 1}])
    assert captured["trigger"] == "new_lead"


# ---------------------------------------------------------------------------
# chat persistence: the turn's task id is stored server-side
# ---------------------------------------------------------------------------


class _FakeThreads:
    def __init__(self):
        self.rows = []

    async def find_one(self, query):
        return next((r for r in self.rows if r.get("thread_id") == query.get("thread_id")), None)

    async def update_one(self, query, update, upsert=False):
        row = {"thread_id": query["thread_id"], **update["$set"]}
        self.rows[:] = [r for r in self.rows if r.get("thread_id") != query.get("thread_id")]
        self.rows.append(row)


class _FakeLogs:
    def __init__(self):
        self.inserted = []

    async def insert_one(self, doc):
        self.inserted.append(doc)


class _FakeDb:
    def __init__(self):
        self.chat_threads = _FakeThreads()
        self.chat_logs = _FakeLogs()


@pytest.mark.asyncio
async def test_chat_turn_task_id_is_linked_to_the_stored_turn_and_log():
    set_event_service(RecordingEventService())
    db = _FakeDb()
    outcome = await run_chat("thread-link", "hello", model=FakeModel([plain_answer("Hi.")]), tools=[], database=db)
    turn = db.chat_threads.rows[0]["turns"][-1]
    assert turn["task_id"] == outcome["task_id"]
    assert db.chat_logs.inserted[-1]["task_id"] == outcome["task_id"]


@pytest.mark.asyncio
async def test_chat_stream_announces_the_task_id_of_its_own_events():
    """The task id on the SSE stream is the id the events were stored under."""
    from app.agents.chat import stream_chat
    from test_chat import FakeCollection, FakeDatabase

    recorder = RecordingEventService()
    set_event_service(recorder)
    db = FakeDatabase(chat_threads=FakeCollection(), chat_logs=FakeCollection())

    events = [
        event
        async for event in stream_chat(
            "thread-corr", "hi", db, model=FakeModel([plain_answer("Hello.")]), tools=[]
        )
    ]

    assert events[0]["event"] == "task"
    announced = events[0]["data"]["task_id"]
    assert announced and events[-1]["data"]["task_id"] == announced
    # the persisted task events for this turn carry exactly that task id
    assert {e["task_id"] for e in recorder.events} == {announced}
    assert recorder.of("task.started") and recorder.of("task.completed")


# ---------------------------------------------------------------------------
# chat data-lifecycle events: filter / aggregation / validation / response
# ---------------------------------------------------------------------------


def _paged_tool():
    async def read_tool(database=None):
        return {
            "offset": 0, "limit": 10, "total_count": 25, "returned": 10,
            "remaining_count": 15, "has_more": True, "next_offset": 10,
            "all_records_returned": False,
            "sum_amount": 4321.0, "sum_amount_covers": "all matched records",
            "records": [],
        }

    return ChatTool("fake_read", "Return one page of matched records", EmptyArgs, read_tool)


@pytest.mark.asyncio
async def test_chat_emits_filter_and_aggregation_events_with_real_counts():
    recorder = RecordingEventService()
    set_event_service(recorder)
    model = FakeModel([tool_call(), plain_answer("Done.")])
    await run_chat(
        "thread-data", "Show payments", model=model, tools=[_paged_tool()],
        database=_FakeDb(),
    )

    types = recorder.types()
    # the data stages sit between the tool's own lifecycle and the answer's
    assert types.index("tool.completed") < types.index("data.filter_completed")
    assert types.index("data.filter_completed") < types.index("chat.response_started")

    filtered = recorder.of("data.filter_completed")
    assert len(filtered) == 1
    assert filtered[0]["payload"] == {
        "tool": "fake_read", "matched": 25, "returned": 10,
        "remaining": 15, "complete": False,
    }
    aggregated = recorder.of("data.aggregation_completed")
    assert len(aggregated) == 1
    # the aggregate's key name only - the money value itself never rides an event
    assert aggregated[0]["payload"] == {"tool": "fake_read", "aggregate": "sum_amount"}


@pytest.mark.asyncio
async def test_chat_emits_validation_passed_for_a_clean_answer():
    recorder = RecordingEventService()
    set_event_service(recorder)
    model = FakeModel([plain_answer("Hello there.")])
    await run_chat("thread-val", "hi", model=model, tools=[], database=_FakeDb())

    assert len(recorder.of("chat.response_started")) == 1
    assert len(recorder.of("chat.validation_started")) == 1
    completed = recorder.of("chat.validation_completed")
    assert len(completed) == 1
    assert completed[0]["payload"] == {"outcome": "passed"}


@pytest.mark.asyncio
async def test_chat_emits_validation_corrected_after_a_regeneration():
    recorder = RecordingEventService()
    set_event_service(recorder)

    async def read_tool(database=None):
        return {"count": 4}

    tool = ChatTool("fake_read", "Return a fixed count", EmptyArgs, read_tool)
    model = FakeModel([
        tool_call(),
        plain_answer("There are 999 open quotes."),
        plain_answer("There are 4 open quotes."),
    ])
    outcome = await run_chat(
        "thread-fix", "How many open quotes?", model=model, tools=[tool],
        database=_FakeDb(),
    )

    assert outcome["text"] == "There are 4 open quotes."
    # two answer generations, one validation stage, verdict: corrected
    assert len(recorder.of("chat.response_started")) == 2
    assert len(recorder.of("chat.validation_started")) == 1
    completed = recorder.of("chat.validation_completed")
    assert len(completed) == 1
    assert completed[0]["payload"] == {"outcome": "corrected"}


@pytest.mark.asyncio
async def test_chat_emits_validation_replaced_when_prose_is_discarded():
    recorder = RecordingEventService()
    set_event_service(recorder)

    async def read_tool(database=None):
        return {"count": 4}

    tool = ChatTool("fake_read", "Return a fixed count", EmptyArgs, read_tool)
    model = FakeModel([
        tool_call(),
        plain_answer("There are 999 open quotes."),
        plain_answer("There are 888 open quotes."),
    ])
    outcome = await run_chat(
        "thread-repl", "How many open quotes?", model=model, tools=[tool],
        database=_FakeDb(),
    )

    # the unsupported prose never reached the user
    assert "999" not in outcome["text"] and "888" not in outcome["text"]
    completed = recorder.of("chat.validation_completed")
    assert len(completed) == 1
    assert completed[0]["payload"] == {"outcome": "replaced"}
