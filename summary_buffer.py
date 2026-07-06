"""Summary buffer for the Circuits Tutor.

Instead of resending the full conversation history on every follow-up, we keep a
small structured "blob" per conversation. After each Opus or Sonnet turn, Haiku
compresses the latest exchange (plus the previous blob) into an updated blob.

Blob schema (from CLAUDE.md):
    {
      "topic": "...",
      "concepts_covered": [...],
      "student_struggle_points": [...],
      "last_2_messages": [...]
    }

State lives in an in-memory dict keyed by conversation_id. This resets on server
restart, which is fine for local V1 development.
"""

import json
from typing import Optional

from anthropic import Anthropic

# Reuse the model IDs defined alongside the router so the two modules agree.
from router import MODEL_IDS

# --- In-memory store -------------------------------------------------------
# conversation_id -> latest blob. The router reads this to tell a follow-up
# from a new topic; main.py updates it after each Opus/Sonnet turn.
_BUFFERS: dict[str, dict] = {}

# JSON schema for the blob. Passed to Haiku via output_config so the model is
# constrained to return exactly this shape (no free-form parsing needed).
_BLOB_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "concepts_covered": {"type": "array", "items": {"type": "string"}},
        "student_struggle_points": {"type": "array", "items": {"type": "string"}},
        "last_2_messages": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "topic",
        "concepts_covered",
        "student_struggle_points",
        "last_2_messages",
    ],
    "additionalProperties": False,
}


def get_buffer(conversation_id: str) -> Optional[dict]:
    """Return the current blob for a conversation, or None if it's new."""
    return _BUFFERS.get(conversation_id)


def _build_compression_prompt(
    previous_blob: Optional[dict],
    student_message: str,
    assistant_message: str,
) -> str:
    """Assemble the instruction we hand to Haiku to produce the updated blob.

    We give Haiku the prior blob (or a note that this is the first turn) plus the
    latest student/tutor exchange, and ask it to fold them together.
    """
    if previous_blob:
        prior = json.dumps(previous_blob, indent=2)
    else:
        prior = "(none - this is the first turn of the conversation)"

    return (
        "You maintain a running summary of a circuits tutoring conversation.\n"
        "Update the structured summary to fold in the latest exchange.\n\n"
        f"Previous summary:\n{prior}\n\n"
        f"Latest student message:\n{student_message}\n\n"
        f"Latest tutor reply:\n{assistant_message}\n\n"
        "Produce the updated summary. Keep 'topic' to a short phrase, list the "
        "concepts that have come up, note where the student is struggling, and "
        "put the two most recent messages (student then tutor) in "
        "'last_2_messages'."
    )


def update_buffer(
    client: Anthropic,
    conversation_id: str,
    student_message: str,
    assistant_message: str,
) -> dict:
    """Compress the latest exchange into the blob via Haiku, store it, return it.

    Called after an Opus or Sonnet turn. Uses structured outputs so Haiku's reply
    is guaranteed to match the blob schema.
    """
    previous_blob = get_buffer(conversation_id)
    prompt = _build_compression_prompt(
        previous_blob, student_message, assistant_message
    )

    response = client.messages.create(
        model=MODEL_IDS["haiku"],
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": _BLOB_SCHEMA}},
    )

    # With output_config.format set, the first text block is valid JSON matching
    # our schema, so we can parse it directly.
    text = next(block.text for block in response.content if block.type == "text")
    updated_blob = json.loads(text)

    _BUFFERS[conversation_id] = updated_blob
    return updated_blob
