"""Integration tests: router.py + summary_buffer.py working together.

The router runs for real (pure logic). The summarization step uses a fake
Anthropic client so we don't hit the API. We reuse FakeClient from the
summary_buffer unit tests to avoid duplicating the stub.
"""

import pytest

import summary_buffer as sb
from router import classify
from test_summary_buffer import FakeClient


@pytest.fixture(autouse=True)
def _clear_store():
    sb._BUFFERS.clear()
    yield
    sb._BUFFERS.clear()


def test_new_topic_routes_to_sonnet_then_summarizes_into_buffer():
    conversation_id = "conv1"
    question = "How do I find the equivalent resistance of resistors in parallel?"

    # 1. A new-topic question (no image, no buffer yet) routes to Sonnet.
    model = classify(question, has_image=False, buffer=sb.get_buffer(conversation_id))
    assert model == "sonnet"

    # 2. Its result can be summarized into the buffer via the summary buffer.
    blob = {
        "topic": "equivalent resistance",
        "concepts_covered": ["parallel resistors"],
        "student_struggle_points": [],
        "last_2_messages": [question, "Use 1/Req = 1/R1 + 1/R2..."],
    }
    client = FakeClient([blob])
    sb.update_buffer(client, conversation_id, question, "Use 1/Req = 1/R1 + 1/R2...")

    stored = sb.get_buffer(conversation_id)
    assert stored is not None
    assert stored["topic"] == "equivalent resistance"


def test_followup_is_routed_using_existing_buffer_context():
    conversation_id = "conv1"

    # Establish an existing buffer (a topic is now in play).
    blob = {
        "topic": "equivalent resistance",
        "concepts_covered": ["parallel resistors"],
        "student_struggle_points": [],
        "last_2_messages": ["...", "..."],
    }
    client = FakeClient([blob])
    sb.update_buffer(client, conversation_id, "q", "a")
    buffer = sb.get_buffer(conversation_id)

    # A short follow-up, WITH the buffer, routes to Haiku (the router uses the
    # buffer's topic to recognize this as a continuation, not a new topic).
    followup = "wait, why?"
    assert classify(followup, has_image=False, buffer=buffer) == "haiku"

    # Control: the SAME message with no buffer is treated as a new topic ->
    # Sonnet. This proves the buffer context is what changed the routing.
    assert classify(followup, has_image=False, buffer=None) == "sonnet"
