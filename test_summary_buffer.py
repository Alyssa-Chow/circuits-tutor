"""Standalone unit tests for summary_buffer.py.

These do NOT hit the Anthropic API. We inject a fake client whose
messages.create() returns a canned blob and records the prompt it was called
with, so the tests are deterministic and can run offline.
"""

import json

import pytest

import summary_buffer as sb

# Fields every blob must carry, per CLAUDE.md.
EXPECTED_FIELDS = {
    "topic",
    "concepts_covered",
    "student_struggle_points",
    "last_2_messages",
}


# --- Fake Anthropic client -------------------------------------------------
class _FakeBlock:
    """Stands in for a response content block (type == 'text')."""

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
        # Record the call so tests can inspect what was sent to Haiku.
        self._parent.calls.append(kwargs)
        # Return the next queued blob, serialized like a structured-output reply.
        blob = self._parent.blobs.pop(0)
        return _FakeResponse(json.dumps(blob))


class FakeClient:
    """Minimal stand-in for anthropic.Anthropic with a queue of blobs to return."""

    def __init__(self, blobs):
        self.blobs = list(blobs)
        self.calls = []
        self.messages = _FakeMessages(self)


@pytest.fixture(autouse=True)
def _clear_store():
    """Reset the in-memory buffer store before each test."""
    sb._BUFFERS.clear()
    yield
    sb._BUFFERS.clear()


# --- Tests -----------------------------------------------------------------
def test_buffer_starts_empty_for_new_conversation():
    assert sb.get_buffer("conv-new") is None


def test_turn_stores_blob_with_expected_schema():
    blob = {
        "topic": "RC circuits",
        "concepts_covered": ["capacitor charging"],
        "student_struggle_points": ["time constant"],
        "last_2_messages": ["How does an RC circuit charge?", "It charges..."],
    }
    client = FakeClient([blob])

    result = sb.update_buffer(client, "conv1", "How does an RC circuit charge?", "It charges...")

    # Returned blob and stored blob match, and both carry the schema fields.
    assert set(result.keys()) == EXPECTED_FIELDS
    assert sb.get_buffer("conv1") == result


def test_followup_updates_existing_blob_rather_than_starting_fresh():
    first = {
        "topic": "RC circuits",
        "concepts_covered": ["capacitor charging"],
        "student_struggle_points": ["time constant"],
        "last_2_messages": ["How does an RC circuit charge?", "It charges..."],
    }
    second = {
        "topic": "RC circuits",
        "concepts_covered": ["capacitor charging", "time constant tau=RC"],
        "student_struggle_points": [],
        "last_2_messages": ["Why is it RC?", "Because tau = R times C."],
    }
    client = FakeClient([first, second])

    sb.update_buffer(client, "conv1", "How does an RC circuit charge?", "It charges...")
    sb.update_buffer(client, "conv1", "Why is it RC?", "Because tau = R times C.")

    # The latest blob is stored (not lost), and it reflects the second turn.
    stored = sb.get_buffer("conv1")
    assert stored == second

    # Crucially: the second call folded in the FIRST blob. The compression
    # prompt for turn 2 must contain the prior blob's content, proving this is
    # an update rather than a from-scratch summary.
    second_prompt = client.calls[1]["messages"][0]["content"]
    assert "capacitor charging" in second_prompt  # a concept from the first blob


def test_conversations_stay_separate():
    blob_a = {
        "topic": "RC circuits",
        "concepts_covered": ["capacitor"],
        "student_struggle_points": [],
        "last_2_messages": ["a", "b"],
    }
    blob_b = {
        "topic": "Thevenin equivalents",
        "concepts_covered": ["source transformation"],
        "student_struggle_points": [],
        "last_2_messages": ["c", "d"],
    }
    client = FakeClient([blob_a, blob_b])

    sb.update_buffer(client, "conv-A", "q", "a")
    sb.update_buffer(client, "conv-B", "q", "a")

    assert sb.get_buffer("conv-A")["topic"] == "RC circuits"
    assert sb.get_buffer("conv-B")["topic"] == "Thevenin equivalents"
    assert sb.get_buffer("conv-A") != sb.get_buffer("conv-B")
