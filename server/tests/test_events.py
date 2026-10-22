"""Phase 1 tests: the real-time task-event foundation.

Everything is offline - a fake Mongo-style store (with atomic $inc counters and
unique-index behaviour) and a controllable fake Redis stand in for the real
services. No network, no keys, no LLM.

Run:  .venv/bin/python -m pytest tests/test_events.py -q
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app import db as app_db
from app.api import live as live_api
from app.api import routes
from app.config import settings
from app.main import app
from app.schemas.events import SCHEMA_VERSION, TaskEvent
from app.services.events import EventService, set_event_service
from app.agents.registry import agents_with_status, list_capabilities


# ---------------------------------------------------------------------------
# fake store: unique-index + atomic-counter semantics
# ---------------------------------------------------------------------------


def _matches(doc: dict, query: dict) -> bool:
    for field, rule in query.items():
        value = doc.get(field)
        if isinstance(rule, dict):
            if "$gt" in rule and not (value is not None and value > rule["$gt"]):
                return False
            if "$in" in rule and value not in rule["$in"]:
                return False
        elif field == "task_id" and rule and value != rule:
            return False
    return True


class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, field, direction=1):
        self.docs.sort(key=lambda d: d.get(field) or 0, reverse=direction < 0)
        return self

    async def to_list(self, limit):
        return list(self.docs[:limit])


class FakeEventsCollection:
    def __init__(self):
        self.docs: list[dict] = []
        self.indexes: list[tuple] = []

    async def create_index(self, keys, name=None, unique=False, **kwargs):
        self.indexes.append((tuple(tuple(k) for k in keys), name, bool(unique)))
        return name

    async def insert_one(self, doc: dict) -> None:
        # unique event identity (_id) and unique (task_id, seq): like the real
        # unique indexes the event service creates.
        for existing in self.docs:
            if existing["_id"] == doc["_id"]:
                raise DuplicateKeyError("duplicate event_id")
            if (
                existing["task_id"] == doc["task_id"]
                and existing["seq"] == doc["seq"]
            ):
                raise DuplicateKeyError("duplicate (task_id, seq)")
        self.docs.append(dict(doc))

    def find(self, query=None, projection=None):
        query = query or {}
        return FakeCursor([row for row in self.docs if _matches(row, query)])


class FakeCountersCollection:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    async def find_one_and_update(self, flt, update, upsert=False, return_document=None, **_kw):
        key = flt["_id"]
        if key not in self.docs:
            if not upsert:
                return None
            self.docs[key] = {"_id": key, "seq": 0}
        self.docs[key]["seq"] += int(update["$inc"]["seq"])
        if return_document == ReturnDocument.BEFORE:
            return {"_id": key, "seq": self.docs[key]["seq"] - 1}
        return dict(self.docs[key])


class FailingEvents(FakeEventsCollection):
    def __init__(self, error):
        super().__init__()
        self._error = error

    async def insert_one(self, doc):
        raise self._error


class FakeStore:
    def __init__(self, events=None):
        self.task_events = events if events is not None else FakeEventsCollection()
        self.task_event_counters = FakeCountersCollection()

    def __getitem__(self, name):
        return getattr(self, name)


class FakeDatabase:
    def __init__(self, store: FakeStore):
        self._store = store

    def __getitem__(self, name):
        return getattr(self._store, name)

    def __getattr__(self, name):
        return getattr(self._store, name)


class RecordingRedis:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel, message):
        self.published.append((channel, message))
        return 1

    def pubsub(self):
        raise AssertionError("not used by the emit path")


class DeadPublishRedis:
    async def publish(self, channel, message):
        raise ConnectionError("redis is down")


def make_service(store=None) -> tuple[EventService, FakeStore, RecordingRedis]:
    store = store or FakeStore()
    redis = RecordingRedis()
    service = EventService(database=FakeDatabase(store), redis_factory=lambda: redis)
    return service, store, redis


# ---------------------------------------------------------------------------
# A. schema
# ---------------------------------------------------------------------------


def test_event_schema_valid_and_versioned():
    event = TaskEvent(
        task_id="task-1",
        seq=1,
        ts=datetime.now(timezone.utc),
        type="agent.started",
        agent_id="negotiation",
        payload={"item": 3},
    )
    assert event.v == SCHEMA_VERSION
    assert event.event_id
    doc = event.to_document()
    assert doc["seq"] == 1 and doc["type"] == "agent.started"
    round_trip = TaskEvent.from_document({**doc, "_id": event.event_id})
    assert round_trip.task_id == "task-1"


def test_event_schema_accepts_chat_data_lifecycle_types():
    # the chat loop's real stages: paged filtering, Python aggregation, the
    # numeric guard's verdict, and answer generation
    for event_type in (
        "data.filter_completed",
        "data.aggregation_completed",
        "chat.validation_started",
        "chat.validation_completed",
        "chat.response_started",
    ):
        event = TaskEvent(
            task_id="t", seq=1, ts=datetime.now(timezone.utc),
            type=event_type, agent_id="chat", payload={"tool": "get_payments"},
        )
        assert event.type == event_type


def test_event_schema_rejects_unknown_type_and_bad_seq():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TaskEvent(task_id="t", seq=1, ts=datetime.now(timezone.utc), type="task.disintegrated")
    with pytest.raises(ValidationError):
        TaskEvent(task_id="t", seq=0, ts=datetime.now(timezone.utc), type="task.started")


def test_event_schema_normalises_ts_to_utc():
    from datetime import timedelta

    eastern = timezone(timedelta(hours=-5))
    moment = datetime(2026, 10, 21, 12, tzinfo=eastern)
    event = TaskEvent(task_id="t", seq=1, ts=moment, type="task.created")
    assert event.ts.tzinfo == timezone.utc
    assert event.ts.hour == 17  # 12:00 -05:00 == 17:00 UTC


def test_event_schema_blocks_sensitive_and_oversized_payloads():
    from pydantic import ValidationError

    for bad in ({"refresh_token": "x"}, {"nested": {"api_key": "x"}}, {"password": "x"}):
        with pytest.raises(ValidationError):
            TaskEvent(task_id="t", seq=1, ts=datetime.now(timezone.utc), type="tool.completed", payload=bad)
    with pytest.raises(ValidationError):
        TaskEvent(
            task_id="t", seq=1, ts=datetime.now(timezone.utc),
            type="agent.progress", payload={"blob": "x" * 4000},
        )


# ---------------------------------------------------------------------------
# B. sequence allocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seq_is_monotonic_and_unique_across_concurrent_emissions():
    service, store, _ = make_service()
    results = await asyncio.gather(
        *(service.emit(task_id="run-1", type="agent.progress", payload={"i": i}) for i in range(30))
    )
    seqs = sorted(event.seq for event in results if event is not None)
    assert seqs == list(range(1, 31))  # contiguous, no gaps, no dup
    assert len(store.task_events.docs) == 30


@pytest.mark.asyncio
async def test_two_workers_share_one_atomic_sequence():
    """Two EventService instances on one store never reuse a (task_id, seq)."""
    store = FakeStore()
    a = EventService(database=FakeDatabase(store), redis_factory=RecordingRedis)
    b = EventService(database=FakeDatabase(store), redis_factory=RecordingRedis)
    results = await asyncio.gather(
        *[a.emit(task_id="shared", type="agent.progress") for _ in range(10)],
        *[b.emit(task_id="shared", type="agent.progress") for _ in range(10)],
    )
    seqs = sorted(event.seq for event in results if event is not None)
    assert seqs == list(range(1, 21))
    assert len({doc["_id"] for doc in store.task_events.docs}) == 20


@pytest.mark.asyncio
async def test_event_identity_and_task_seq_are_unique():
    service, store, _ = make_service()
    event = await service.emit(task_id="run-2", type="task.started")
    assert event is not None
    # a manual insert of the same (task_id, seq) collides with the unique index
    with pytest.raises(DuplicateKeyError):
        await store.task_events.insert_one(
            {**event.to_document(), "_id": "different-id"}
        )
    # and the same event identity (_id == event_id) collides too
    duplicate = event.to_document()
    duplicate["_id"] = duplicate.pop("event_id")
    duplicate["task_id"] = "other"  # so only the identity clashes, not (task,seq)
    with pytest.raises(DuplicateKeyError):
        await store.task_events.insert_one(duplicate)


# ---------------------------------------------------------------------------
# B. failure isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_publish_failure_does_not_break_emit():
    store = FakeStore()
    service = EventService(
        database=FakeDatabase(store), redis_factory=lambda: DeadPublishRedis()
    )
    event = await service.emit(task_id="run-3", type="agent.completed", agent_id="collections")
    assert event is not None
    assert store.task_events.docs and store.task_events.docs[0]["task_id"] == "run-3"  # still durable


@pytest.mark.asyncio
async def test_persistence_failure_returns_none_and_publishes_nothing():
    store = FakeStore(events=FailingEvents(RuntimeError("mongo is down")))
    redis = RecordingRedis()
    service = EventService(database=FakeDatabase(store), redis_factory=lambda: redis)
    event = await service.emit(task_id="run-4", type="task.created")
    assert event is None  # not recorded -> caller is told, nothing crashes
    assert redis.published == []  # we never publish an event that was not persisted


@pytest.mark.asyncio
async def test_published_event_keeps_snapshot_and_briefing_formats():
    service, _, redis = make_service()
    await service.emit(task_id="run-5", type="tool.started", agent_id="chat", payload={"tool": "get_revenue"})
    channel, message = redis.published[0]
    assert channel == "dashboard:updates"  # the existing update channel
    envelope = json.loads(message)
    assert envelope == {"event": "task_event", "data": envelope["data"]}
    assert envelope["data"]["task_id"] == "run-5"
    assert envelope["data"]["seq"] == 1


# ---------------------------------------------------------------------------
# D. replay + dedup (service level)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_events_after_cursor_supports_reconnect_dedup():
    service, _, _ = make_service()
    for i in range(5):
        await service.emit(task_id="run-6", type="agent.progress", payload={"i": i})
    full = await service.get_events("run-6", after=0)
    assert [e.seq for e in full] == [1, 2, 3, 4, 5]
    # a reconnecting client after seq 3 gets only 4 and 5 (no overlap)
    tail = await service.get_events("run-6", after=3)
    assert [e.seq for e in tail] == [4, 5]
    assert await service.last_seq("run-6") == 5
    assert await service.task_exists("run-6") is True
    assert await service.task_exists("ghost") is False


# ---------------------------------------------------------------------------
# D. replay-to-live stream (generator)
# ---------------------------------------------------------------------------


class ControlPubSub:
    def __init__(self, queue: deque):
        self.queue = queue
        self.channels: set[str] = set()
        self.closed = False

    async def subscribe(self, channel):
        self.channels.add(channel)

    async def unsubscribe(self, channel=None):
        self.channels.discard(channel) if channel else self.channels.clear()

    async def aclose(self):
        self.closed = True

    async def get_message(self, ignore_subscribe_messages=True, timeout=1.0):
        if self.queue:
            return self.queue.popleft()
        await asyncio.sleep(0.01)
        return None


class ControlRedis:
    def __init__(self):
        self.queue: deque = deque()
        self.sub: list[ControlPubSub] = []

    def pubsub(self):
        sub = ControlPubSub(self.queue)
        self.sub.append(sub)
        return sub

    def push(self, task_id, seq, event_id=None):
        self.queue.append(
            {
                "type": "message",
                "channel": "dashboard:updates",
                "data": json.dumps(
                    {
                        "event": "task_event",
                        "data": {
                            "v": SCHEMA_VERSION,
                            "event_id": event_id or f"e{seq}",
                            "task_id": task_id,
                            "seq": seq,
                            "type": "agent.progress",
                            "ts": "2026-10-21T00:00:00Z",
                            "payload": {},
                        },
                    }
                ),
            }
        )


async def _collect(stream, count: int) -> list[int]:
    seqs: list[int] = []
    agen = stream.__aiter__()
    while len(seqs) < count:
        frame = await asyncio.wait_for(agen.__anext__(), timeout=2)
        if frame.startswith("event: task_event"):
            data = json.loads(frame.split("data: ", 1)[1].split("\n", 1)[0])
            seqs.append(data["seq"])
    await stream.aclose()
    return seqs


@pytest.mark.asyncio
async def test_replay_to_live_transition_has_no_gaps_or_duplicates(monkeypatch):
    service, _, _ = make_service()
    for i in range(1, 4):  # persisted 1,2,3
        await service.emit(task_id="run-live", type="agent.progress", payload={"i": i})

    control = ControlRedis()
    # arrive live during the transition: a duplicate of 3, then 4 and 5
    control.push("run-live", 3, event_id="e3-dup")
    control.push("run-live", 4)
    control.push("run-live", 5)
    # another task's event must be ignored
    control.queue.append(
        {"type": "message", "channel": "dashboard:updates",
         "data": json.dumps({"event": "task_event", "data": {"task_id": "other", "seq": 9, "event_id": "x"}})}
    )

    monkeypatch.setattr(live_api, "get_event_service", lambda: service)
    monkeypatch.setattr(live_api, "get_redis", lambda: control)

    seqs = await _collect(live_api._task_event_stream("run-live", 0, "tok"), 5)
    assert seqs == [1, 2, 3, 4, 5]  # replayed, then live, ordered, deduped, other-task ignored
    assert control.sub[-1].closed is True  # subscription cleaned up on disconnect


@pytest.mark.asyncio
async def test_out_of_order_live_events_are_delivered_in_sequence(monkeypatch):
    service, _, _ = make_service()
    await service.emit(task_id="ooo", type="task.started")  # persisted seq 1

    control = ControlRedis()
    control.push("ooo", 3)  # arrives before its predecessor
    control.push("ooo", 2)

    monkeypatch.setattr(live_api, "get_event_service", lambda: service)
    monkeypatch.setattr(live_api, "get_redis", lambda: control)

    seqs = await _collect(live_api._task_event_stream("ooo", 0, "tok"), 3)
    assert seqs == [1, 2, 3]  # reordered, never emitted out of turn


@pytest.mark.asyncio
async def test_a_lost_head_does_not_stall_the_stream(monkeypatch):
    service, _, _ = make_service()
    await service.emit(task_id="gap", type="task.started")  # persisted seq 1

    control = ControlRedis()
    # seq 2 is genuinely lost (never persisted, never published); 3 arrives live
    control.push("gap", 3)

    monkeypatch.setattr(live_api, "get_event_service", lambda: service)
    monkeypatch.setattr(live_api, "get_redis", lambda: control)
    # shorten the stall so the test is quick
    monkeypatch.setattr(live_api, "_TASK_EVENT_GAP_SECONDS", 0.05)

    seqs = await _collect(live_api._task_event_stream("gap", 0, "tok"), 2)
    assert seqs == [1, 3]  # advances past the missing 2 instead of hanging


# ---------------------------------------------------------------------------
# C + D. registry + HTTP endpoints
# ---------------------------------------------------------------------------


def _install(store_service):
    set_event_service(store_service)


@pytest.fixture
def clean_service():
    yield
    set_event_service(None)


@pytest.mark.asyncio
async def test_registry_lists_only_real_capabilities_and_starts_idle(clean_service):
    service, _, _ = make_service()
    _install(service)
    agents = await agents_with_status(service)
    assert [a["id"] for a in agents] == ["master", "negotiation", "collections", "lead_followup", "chat"]
    for agent in agents:
        assert agent["status"] == "idle"
        assert agent["status_source"] == "none"
    chat = next(a for a in agents if a["id"] == "chat")
    assert "get_revenue" in chat["tools"]  # the single chat agent's real tools
    assert not any("subagent" in str(tool).lower() for tool in chat["tools"])


@pytest.mark.asyncio
async def test_registry_status_is_derived_from_events(clean_service):
    service, _, _ = make_service()
    await service.emit(task_id="r", type="agent.started", agent_id="collections")
    await service.emit(task_id="r", type="agent.completed", agent_id="collections")
    await service.emit(task_id="r", type="agent.started", agent_id="negotiation")
    agents = {a["id"]: a for a in await agents_with_status(service)}
    assert agents["collections"]["status"] == "completed"
    assert agents["collections"]["status_source"] == "events"
    assert agents["negotiation"]["status"] == "running"
    assert agents["master"]["status"] == "idle"  # never ran -> idle, not running


def prepare_test_client(monkeypatch, store_service):
    monkeypatch.setattr(app_db, "get_client", lambda: object())
    monkeypatch.setattr(app_db, "get_db", lambda: object())

    async def close_db():
        return None

    monkeypatch.setattr(app_db, "close", close_db)
    _install(store_service)


def test_agents_and_tasks_endpoints_return_real_state(monkeypatch, clean_service):
    service, store, _ = make_service()
    asyncio.run(_seed(service))
    prepare_test_client(monkeypatch, service)
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)

    with TestClient(app) as client:
        agents = client.get("/api/agents").json()["agents"]
        tasks = client.get("/api/tasks").json()["tasks"]

    assert {a["id"] for a in agents} == {"master", "negotiation", "collections", "lead_followup", "chat"}
    assert tasks and tasks[0]["task_id"] == "seed-task"


async def _seed(service):
    await service.emit(task_id="seed-task", type="task.created", payload={"trigger": "manual"})
    await service.emit(task_id="seed-task", type="agent.started", agent_id="negotiation")


def test_new_endpoints_require_an_api_key(monkeypatch, clean_service):
    service, _, _ = make_service()
    prepare_test_client(monkeypatch, service)
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "API_KEYS", "the-key")

    with TestClient(app) as client:
        assert client.get("/api/agents").status_code == 401
        assert client.get("/api/tasks").status_code == 401
        assert client.get("/api/agents", headers={"X-API-Key": "the-key"}).status_code == 200


def test_unknown_task_is_404_and_bad_cursor_is_rejected(monkeypatch, clean_service):
    service, _, _ = make_service()
    prepare_test_client(monkeypatch, service)
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)

    with TestClient(app) as client:
        assert client.get("/api/tasks/does-not-exist/events").status_code == 404
        # a negative cursor fails validation before any streaming starts
        assert client.get("/api/tasks/does-not-exist/events?after=-1").status_code == 422


def test_connection_limit_returns_503_for_task_events(monkeypatch, clean_service):
    service, _, _ = make_service()
    prepare_test_client(monkeypatch, service)
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)

    async def no_slot():
        return None

    monkeypatch.setattr(live_api._task_event_slots, "acquire", no_slot)

    with TestClient(app) as client:
        response = client.get("/api/tasks/whatever/events")
    assert response.status_code == 503


def test_expected_new_routes_are_registered():
    paths = set(app.openapi()["paths"])
    assert {"/api/agents", "/api/tasks", "/api/tasks/{task_id}/events"} <= paths


# ---------------------------------------------------------------------------
# Phase 1 integration review regressions
# ---------------------------------------------------------------------------


def test_events_endpoint_requires_an_api_key(monkeypatch, clean_service):
    service, _, _ = make_service()
    prepare_test_client(monkeypatch, service)
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "API_KEYS", "the-key")

    with TestClient(app) as client:
        # no key -> 401 before the endpoint ever runs
        assert client.get("/api/tasks/ghost/events").status_code == 401
        # with a key auth passes and the endpoint runs (unknown task -> 404,
        # which never opens the never-ending SSE stream)
        assert client.get(
            "/api/tasks/ghost/events", headers={"X-API-Key": "the-key"}
        ).status_code == 404


def test_emit_creates_the_unique_index_once(monkeypatch, clean_service):
    service, store, _ = make_service()
    await_seq = asyncio.run(_seed(service))
    unique = [spec for spec in store.task_events.indexes if spec[2]]  # unique=True
    assert (("task_id", 1), ("seq", 1)) in [spec[0] for spec in unique]
    # a second emit must not re-run index creation (attempted-once guard)
    before = len(store.task_events.indexes)
    asyncio.run(service.emit(task_id="seed-task", type="agent.progress"))
    assert len(store.task_events.indexes) == before


def test_index_creation_failure_does_not_break_emit():
    class IndexRefusingEvents(FakeEventsCollection):
        async def create_index(self, *a, **k):
            raise RuntimeError("index build failed")

    store = FakeStore(events=IndexRefusingEvents())
    service = EventService(database=FakeDatabase(store), redis_factory=RecordingRedis)
    event = asyncio.run(service.emit(task_id="idx", type="task.created"))
    # the atomic counter still produced an ordered, persisted event despite no index
    assert event is not None and event.seq == 1
    assert store.task_events.docs[0]["task_id"] == "idx"


@pytest.mark.asyncio
async def test_live_dashboard_stream_does_not_forward_task_events(monkeypatch):
    """A task_event on the shared channel must not reach the dashboard stream."""
    snapshot = {"generated_at": "2026-10-21T00:00:00Z", "revenue_today": 1.0}

    async def fake_snapshot():
        return snapshot

    async def fake_briefing():
        return None

    monkeypatch.setattr(live_api, "_current_snapshot", fake_snapshot)
    monkeypatch.setattr(live_api, "_latest_briefing", fake_briefing)

    control = ControlRedis()
    control.push("t-1", 1)  # interleaved task event -> dropped
    control.queue.append(
        {
            "type": "message",
            "channel": "dashboard:updates",
            "data": json.dumps({"event": "snapshot", "data": {**snapshot, "revenue_today": 2.0}}),
        }
    )
    monkeypatch.setattr(live_api, "get_redis", lambda: control)

    stream = live_api._event_stream("tok")
    agen = stream.__aiter__()
    frames: list[str] = []
    while len(frames) < 2:
        frames.append(await asyncio.wait_for(agen.__anext__(), timeout=2))
    await stream.aclose()

    text = "".join(frames)
    assert text.count("event: snapshot") == 2
    assert "task_event" not in text


@pytest.mark.asyncio
async def test_recent_tasks_carry_the_recorded_prompt_and_trigger():
    service, _, _ = make_service()
    await service.emit(
        task_id="chat-task",
        type="task.created",
        agent_id="chat",
        payload={"prompt": "Compare the last two lead pulls", "thread_id": "thread-1"},
    )
    await service.emit(task_id="chat-task", type="agent.started", agent_id="chat")
    await service.emit(
        task_id="pipeline-task",
        type="task.created",
        payload={"trigger": "scheduler"},
    )
    await service.emit(task_id="pipeline-task", type="agent.started", agent_id="negotiation")

    tasks = {row["task_id"]: row for row in await service.recent_tasks()}

    assert tasks["chat-task"]["prompt"] == "Compare the last two lead pulls"
    assert tasks["chat-task"]["trigger"] is None
    assert tasks["pipeline-task"]["prompt"] is None
    assert tasks["pipeline-task"]["trigger"] == "scheduler"


# ---------------------------------------------------------------------------
# Phase 4: history rows expose only what the events actually prove
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_row_reports_recorded_timing_agents_and_failure_kind():
    service, store, _ = make_service()
    base = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)

    async def stamp(offset_seconds, event_type, **kw):
        event = await service.emit(task_id="sync-1", type=event_type, **kw)
        for doc in store.task_events.docs:
            if doc["_id"] == event.event_id:
                doc["ts"] = base + timedelta(seconds=offset_seconds)

    await stamp(0, "task.created")
    await stamp(4, "agent.started", agent_id="master")
    await stamp(
        20, "task.failed", agent_id="master", payload={"error_kind": "ConnectionError"}
    )

    row = (await service.recent_tasks())[0]
    assert row["status"] == "failed"
    assert row["created_at"] == "2026-10-22T09:00:00+00:00"
    assert row["updated_at"] == "2026-10-22T09:00:20+00:00"
    assert row["duration_ms"] == 20_000
    assert row["agents"] == ["master"]
    # the exception type only - never the message, which can echo data
    assert row["error_kind"] == "ConnectionError"


@pytest.mark.asyncio
async def test_history_reports_no_duration_for_a_task_that_never_ended():
    service, _, _ = make_service()
    await service.emit(task_id="half-1", type="task.created", agent_id="chat")
    await service.emit(task_id="half-1", type="task.started", agent_id="chat")

    row = (await service.recent_tasks())[0]
    assert row["status"] == "running"
    assert row["duration_ms"] is None
    assert row["error_kind"] is None
    assert row["agents"] == ["chat"]


@pytest.mark.asyncio
async def test_a_stale_task_without_a_terminal_event_is_unknown_not_running():
    service, store, _ = make_service()
    await service.emit(task_id="dead-1", type="task.started", agent_id="chat")
    store.task_events.docs[0]["ts"] = datetime.now(timezone.utc) - timedelta(hours=2)

    row = (await service.recent_tasks())[0]
    assert row["status"] == "unknown"
    assert row["duration_ms"] is None


@pytest.mark.asyncio
async def test_history_agents_and_error_kind_survive_a_missing_agent_id():
    service, _, _ = make_service()
    await service.emit(task_id="noid-1", type="task.created", payload={})
    await service.emit(
        task_id="noid-1", type="task.completed", payload={"tools_used": 1}
    )

    row = (await service.recent_tasks())[0]
    assert row["status"] == "completed"
    assert row["agents"] == []
    assert row["error_kind"] is None


# ---------------------------------------------------------------------------
# RC1 regression: token *counts* are telemetry, not credentials
# ---------------------------------------------------------------------------


def test_event_schema_accepts_token_counts():
    """`total_tokens` used to be rejected because it contains "token", which
    silently dropped every task.completed event."""
    from app.schemas.events import is_sensitive_key

    for key in ("total_tokens", "input_tokens", "output_tokens", "prompt_tokens", "completion_tokens"):
        assert is_sensitive_key(key) is False, key
    event = TaskEvent(
        task_id="t", seq=1, ts=datetime.now(timezone.utc), type="task.completed",
        agent_id="chat", payload={"tools_used": 2, "total_tokens": 812, "duration_ms": 4200},
    )
    assert event.payload["total_tokens"] == 812


def test_event_schema_still_rejects_real_secrets():
    from pydantic import ValidationError

    from app.schemas.events import is_sensitive_key

    for key in (
        "token", "access_token", "refresh_token", "id_token", "bearer_token",
        "api_key", "apikey", "secret", "client_secret", "password", "credential",
        "authorization", "refresh",
    ):
        assert is_sensitive_key(key) is True, key
        with pytest.raises(ValidationError):
            TaskEvent(
                task_id="t", seq=1, ts=datetime.now(timezone.utc),
                type="task.started", payload={key: "x"},
            )
    # nested too, and an allow-listed name does not open a hole for others
    with pytest.raises(ValidationError):
        TaskEvent(
            task_id="t", seq=1, ts=datetime.now(timezone.utc), type="task.started",
            payload={"metrics": {"nested": {"refresh_token": "x"}}},
        )
    with pytest.raises(ValidationError):
        TaskEvent(
            task_id="t", seq=1, ts=datetime.now(timezone.utc), type="task.started",
            payload={"total_tokens_but_really_a_secret": "x"},
        )


@pytest.mark.asyncio
async def test_task_completed_with_token_count_is_persisted():
    """The exact event that was being lost end-to-end."""
    service, store, redis = make_service()
    await service.emit(task_id="life", type="task.created", agent_id="chat", payload={"prompt": "hi"})
    await service.emit(task_id="life", type="task.started", agent_id="chat", payload={})
    done = await service.emit(
        task_id="life", type="task.completed", agent_id="chat",
        payload={"tools_used": 1, "total_tokens": 812, "duration_ms": 90},
    )
    assert done is not None
    assert service.rejected == 0
    persisted = [doc["type"] for doc in store.task_events.docs]
    assert persisted == ["task.created", "task.started", "task.completed"]
    assert store.task_events.docs[-1]["payload"]["total_tokens"] == 812
    # and it reached the live channel too
    assert '"task.completed"' in redis.published[-1][1]


@pytest.mark.asyncio
async def test_a_rejected_event_is_counted_and_logged_loudly(caplog):
    """A dropped event must never be silent again (RC1 hid it for a whole phase)."""
    import logging

    service, store, _ = make_service()
    with caplog.at_level(logging.ERROR, logger="events"):
        dropped = await service.emit(
            task_id="bad", type="task.started", payload={"refresh_token": "leak"}
        )
    assert dropped is None
    assert service.rejected == 1
    assert store.task_events.docs == []
    assert "DROPPED task event" in caplog.text
    # the key name is named for diagnosis; the value never is
    assert "refresh_token" in caplog.text
    assert "leak" not in caplog.text
