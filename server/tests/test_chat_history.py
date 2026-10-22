"""Stored conversations: titles, the chats list/get/delete services, the
/api/chats routes, and recall_conversations - the model's memory of past
chats, whose numbers deliberately never become fresh evidence.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from app.agents.chat import (
    delete_chat_thread,
    get_chat_thread,
    list_chat_threads,
    run_chat,
)
from app.agents.chat_tools import recall_conversations
from app.api import routes
from test_chat import FakeCollection, FakeDatabase, FakeModel
from test_chat_data_tools import business_db, tool_call


class DeletableCollection(FakeCollection):
    async def update_one(self, query, update, upsert=False):
        # Real $set merges into the stored document; the shared fake
        # replaces the row, which would drop fields set by earlier turns.
        existing = next(
            (row for row in self.rows if row.get("thread_id") == query.get("thread_id")), None
        )
        merged = {**(existing or {"thread_id": query.get("thread_id")}), **update["$set"]}
        self.rows = [row for row in self.rows if row.get("thread_id") != query.get("thread_id")]
        self.rows.append(merged)

    async def delete_one(self, query):
        before = len(self.rows)
        self.rows = [row for row in self.rows if row.get("thread_id") != query.get("thread_id")]

        class Result:
            pass

        result = Result()
        result.deleted_count = before - len(self.rows)
        return result


def chat_db():
    database = business_db()
    database.chat_threads = DeletableCollection([])
    return database


def stored_thread(thread_id: str, title: str, question: str, answer: str, updated: str) -> dict:
    return {
        "thread_id": thread_id,
        "title": title,
        "turns": [{"question": question, "answer": answer, "created_at": updated}],
        "updated_at": updated,
    }


@pytest.mark.anyio
async def test_title_is_set_once_from_the_first_question():
    database = chat_db()
    first = FakeModel([AIMessage(content="Answer one.")])
    await run_chat("thread-1", "Give me all details for Garcoa Inc.", database=database, model=first)
    second = FakeModel([AIMessage(content="Answer two.")])
    await run_chat("thread-1", "And what about their payments?", database=database, model=second)

    doc = next(row for row in database.chat_threads.rows if row["thread_id"] == "thread-1")
    assert doc["title"] == "Give me all details for Garcoa Inc."

    listed = await list_chat_threads(database=database)
    assert len(listed) == 1
    assert listed[0]["title"] == "Give me all details for Garcoa Inc."
    assert listed[0]["turn_count"] == 2

    full = await get_chat_thread("thread-1", database=database)
    assert [turn["question"] for turn in full["turns"]] == [
        "Give me all details for Garcoa Inc.",
        "And what about their payments?",
    ]
    assert await get_chat_thread("missing", database=database) is None

    assert await delete_chat_thread("thread-1", database=database) is True
    assert await get_chat_thread("thread-1", database=database) is None
    assert await delete_chat_thread("thread-1", database=database) is False


@pytest.mark.anyio
async def test_list_orders_newest_first_and_skips_empty_threads():
    database = chat_db()
    database.chat_threads.rows = [
        stored_thread("old", "Old chat", "q1", "a1", "2026-10-20T10:00:00+00:00"),
        stored_thread("new", "New chat", "q2", "a2", "2026-10-22T10:00:00+00:00"),
        {"thread_id": "empty", "turns": [], "updated_at": "2026-10-23T10:00:00+00:00"},
    ]
    listed = await list_chat_threads(database=database)
    assert [item["thread_id"] for item in listed] == ["new", "old"]


@pytest.mark.anyio
async def test_routes_read_the_same_store(monkeypatch):
    database = chat_db()
    database.chat_threads.rows = [
        stored_thread("t-1", "Garcoa chat", "Garcoa?", "Details.", "2026-10-22T10:00:00+00:00"),
    ]
    monkeypatch.setattr(routes, "get_db", lambda: database)
    payload = await routes.list_chats()
    assert payload["chats"][0]["title"] == "Garcoa chat"
    full = await routes.get_chat("t-1")
    assert full["turns"][0]["answer"] == "Details."
    with pytest.raises(Exception):
        await routes.get_chat("nope")


@pytest.mark.anyio
async def test_recall_searches_past_conversations():
    database = chat_db()
    database.chat_threads.rows = [
        stored_thread(
            "t-1", "Garcoa details", "Give me all details for Garcoa Inc.",
            "Garcoa Inc has 60 unpaid invoices on record.", "2026-10-22T10:00:00+00:00",
        ),
        stored_thread(
            "t-2", "Lead check", "Which leads are stuck?",
            "Three leads are stuck.", "2026-10-21T10:00:00+00:00",
        ),
    ]
    result = await recall_conversations("Garcoa", database=database)
    assert result["conversations_matched"] == 1
    hit = result["conversations"][0]
    assert hit["title"] == "Garcoa details"
    assert "60 unpaid invoices" in hit["matches"][0]["answer_excerpt"]

    everything = await recall_conversations("", database=database)
    assert [item["title"] for item in everything["conversations"]] == ["Garcoa details", "Lead check"]


@pytest.mark.anyio
async def test_recalled_figures_do_not_become_evidence():
    """A number that only exists in an old chat's answer must still fail
    the numeric guard in a new chat - recall is context, not evidence."""
    database = chat_db()
    database.chat_threads.rows = [
        stored_thread(
            "t-old", "Old payments", "What did customers pay?",
            "Customers paid $999,999 in total.", "2026-10-20T10:00:00+00:00",
        ),
    ]
    model = FakeModel([
        tool_call("recall_conversations", {"query": "customers pay"}),
        AIMessage(content="Earlier, customers paid $999,999 in total."),
        AIMessage(content="Earlier, customers paid $999,999 in total."),
    ])
    result = await run_chat("thread-new", "What did customers pay before?", database=database, model=model)
    assert "999,999" not in result["text"]
