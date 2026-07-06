"""FastAPI backend for the Circuits Tutor.

Wires the router (model selection) and the summary buffer (conversation memory)
behind a single /chat endpoint:

    1. Pick the model for this turn (router.classify).
    2. Call that model with the tutor system prompt, injecting the summary
       buffer as context when one exists.
    3. Compress the exchange into the buffer via Haiku on every turn
       (summary_buffer.update_buffer), so a run of Haiku follow-ups can't leave
       the buffer stale.
"""

import base64
import json
import uuid
from typing import Optional

from anthropic import Anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, Form, UploadFile

from router import MODEL_IDS, classify
from summary_buffer import get_buffer, update_buffer

# Load ANTHROPIC_API_KEY (and anything else) from .env before creating the client.
load_dotenv()

app = FastAPI(title="Circuits Tutor")

# One shared client; reads ANTHROPIC_API_KEY from the environment.
client = Anthropic()

# How many tokens the tutor reply may use. Small keeps V1 responses snappy and
# well under the SDK's non-streaming timeout.
_MAX_TOKENS = 2048

# The tutor persona (from CLAUDE.md's product vision). Guides methodology and
# withholds the final answer unless the student explicitly asks for it.
_TUTOR_SYSTEM = (
    "You are a circuits tutor for ECE students. Act like a real tutor: identify "
    "the hardest part of the problem, guide the student through the methodology, "
    "and adapt to the complexity of the question. Do NOT give the final answer "
    "outright unless the student explicitly asks for it - ask guiding questions "
    "and explain the approach instead. Keep explanations clear and step-by-step."
)


def _build_system_prompt(buffer: Optional[dict]) -> str:
    """Tutor persona, plus the summary buffer as context when we have one."""
    if not buffer:
        return _TUTOR_SYSTEM
    context = json.dumps(buffer, indent=2)
    return (
        f"{_TUTOR_SYSTEM}\n\n"
        "Conversation context so far (use this instead of asking the student to "
        f"repeat earlier details):\n{context}"
    )


def _build_user_content(message: str, image_bytes: Optional[bytes], media_type: Optional[str]):
    """User turn: plain text, or an image block + text when an image is attached."""
    if not image_bytes:
        return message

    encoded = base64.standard_b64encode(image_bytes).decode("utf-8")
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type or "image/png",
                "data": encoded,
            },
        },
        {"type": "text", "text": message},
    ]


@app.post("/chat")
async def chat(
    message: str = Form(...),
    conversation_id: Optional[str] = Form(None),
    image: Optional[UploadFile] = None,
):
    """Handle one tutoring turn and return a structured response."""
    # New conversations get a server-minted id, echoed back to the caller.
    conversation_id = conversation_id or str(uuid.uuid4())

    # Read the optional image so both the router and the model call can see it.
    image_bytes = await image.read() if image is not None else None
    media_type = image.content_type if image is not None else None
    has_image = image_bytes is not None

    # 1. Pick the model, giving the router the current buffer for follow-up detection.
    buffer = get_buffer(conversation_id)
    model_key = classify(message, has_image=has_image, buffer=buffer)

    # 2. Call the chosen model with the tutor prompt (+ buffer context if present).
    response = client.messages.create(
        model=MODEL_IDS[model_key],
        max_tokens=_MAX_TOKENS,
        system=_build_system_prompt(buffer),
        messages=[{"role": "user", "content": _build_user_content(message, image_bytes, media_type)}],
    )
    reply = next((b.text for b in response.content if b.type == "text"), "")

    # 3. Compress the exchange into the buffer on every turn (Haiku included) so
    #    a run of follow-ups can't leave last_2_messages / struggle points stale.
    update_buffer(client, conversation_id, message, reply)

    return {
        "conversation_id": conversation_id,
        "model": model_key,
        "response": reply,
    }
