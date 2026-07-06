"""Offline end-to-end test for the /chat endpoint.

Uses FastAPI's TestClient to drive the real endpoint (real routing, real buffer
wiring) while swapping the Anthropic client for a fake, so no API calls happen.

The endpoint makes two kinds of model calls per turn:
    - the tutor reply (plain text)
    - the summary-buffer compression (identified by the output_config kwarg)
The fake serves both and counts the compression calls, so we can assert that
the buffer is updated on EVERY turn (Haiku follow-ups included).
"""

import json

import pytest
from fastapi.testclient import TestClient

import main
import summary_buffer as sb


class _FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, parent):
        self._parent = parent

    def create(self, **kwargs):
        # A call carrying output_config is the summary compression step; anything
        # else is the tutor reply.
        if "output_config" in kwargs:
            self._parent.summary_calls += 1
            blob = self._parent.blobs.pop(0)
            return _FakeResponse(json.dumps(blob))
        self._parent.tutor_calls += 1
        return _FakeResponse("TUTOR REPLY")


class FakeClient:
    def __init__(self, blobs):
        self.blobs = list(blobs)
        self.summary_calls = 0
        self.tutor_calls = 0
        self.messages = _FakeMessages(self)


def _blob(topic, last_msg):
    return {
        "topic": topic,
        "concepts_covered": ["parallel resistors"],
        "student_struggle_points": [],
        "last_2_messages": [last_msg, "TUTOR REPLY"],
    }


@pytest.fixture(autouse=True)
def _clean():
    sb._BUFFERS.clear()
    yield
    sb._BUFFERS.clear()


def test_chat_new_topic_then_haiku_followup_updates_buffer_every_turn():
    # Two summary blobs queued: one per turn (new-topic, then follow-up).
    fake = FakeClient([_blob("equivalent resistance", "new q"), _blob("equivalent resistance", "why flip?")])
    main.client = fake  # swap in the fake; endpoint passes main.client to update_buffer
    client = TestClient(main.app)

    # --- Turn 1: new topic, no conversation_id, no image ---
    r1 = client.post("/chat", data={"message": "How do I combine 4 and 12 ohm in parallel?"})
    assert r1.status_code == 200
    body1 = r1.json()
    cid = body1["conversation_id"]
    assert cid  # server minted one
    assert body1["model"] == "sonnet"          # new topic -> Sonnet
    assert body1["response"] == "TUTOR REPLY"
    assert sb.get_buffer(cid) is not None       # buffer created on turn 1
    assert fake.summary_calls == 1              # update_buffer ran once

    # --- Turn 2: short follow-up, same conversation_id ---
    r2 = client.post("/chat", data={"message": "wait, why?", "conversation_id": cid})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["conversation_id"] == cid      # reused, echoed back
    assert body2["model"] == "haiku"            # short + buffer -> Haiku
    # The key assertion: the Haiku turn ALSO refreshed the buffer.
    assert fake.summary_calls == 2
    assert sb.get_buffer(cid)["last_2_messages"][0] == "why flip?"


def test_chat_generates_distinct_conversation_ids():
    fake = FakeClient([_blob("t", "a"), _blob("t", "b")])
    main.client = fake
    client = TestClient(main.app)

    id1 = client.post("/chat", data={"message": "first question about resistors"}).json()["conversation_id"]
    id2 = client.post("/chat", data={"message": "second unrelated question about capacitors"}).json()["conversation_id"]
    assert id1 != id2
    assert sb.get_buffer(id1) is not None
    assert sb.get_buffer(id2) is not None
