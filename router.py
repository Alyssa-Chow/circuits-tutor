"""Model routing classifier for the Circuits Tutor.

Given an incoming student message (plus whether an image was attached and the
current summary buffer), decide which Anthropic model should handle the turn:

    - "opus"   -> hardest problems: image attached OR multi-step derivation
    - "sonnet" -> moderate complexity / a new-topic question without an image
    - "haiku"  -> short follow-ups, clarifications, single-definition lookups

This is a plain rule-based classifier. It makes NO API calls itself; it just
inspects the text and returns a model name string. Keeping it pure makes each
rule easy to read and unit-test in isolation.
"""

from typing import Literal, Optional

# The concrete model IDs live in one place so main.py and this module agree.
MODEL_IDS = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5-20251001",
}

ModelName = Literal["opus", "sonnet", "haiku"]

# Capability ranking. When a message matches more than one rule, we always
# route to the *highest* capability it could plausibly need (escalate on
# ambiguity), so a short follow-up that also contains a hard-derivation keyword
# still goes to Opus rather than Haiku.
_CAPABILITY_RANK = {"haiku": 1, "sonnet": 2, "opus": 3}

# --- Keyword signals -------------------------------------------------------
# Words/phrases that suggest a hard, multi-step derivation worthy of Opus.
_COMPLEX_KEYWORDS = (
    "derive",
    "derivation",
    "prove",
    "step by step",
    "step-by-step",
    "transient",
    "transfer function",
    "laplace",
    "differential equation",
    "nodal analysis",
    "mesh analysis",
    "thevenin",
    "norton",
    "superposition",
    "phasor",
    "second order",
    "second-order",
)

# Phrases that signal a short clarification / follow-up better served by Haiku.
_FOLLOWUP_KEYWORDS = (
    "what does",
    "what is",
    "define",
    "definition",
    "why",
    "clarify",
    "can you explain",
    "what do you mean",
    "remind me",
    "again",
    "so you're saying",
)

# Below this word count a message is treated as "short" (a candidate follow-up).
_SHORT_MESSAGE_WORDS = 12

# Phrases a student might use to force a specific model, mapped to that model.
# Anything here overrides the automatic heuristics entirely (see classify()).
_OVERRIDE_PHRASES = {
    "use opus": "opus",
    "use sonnet": "sonnet",
    "use haiku": "haiku",
    "with opus": "opus",
    "with sonnet": "sonnet",
    "with haiku": "haiku",
}


def _detect_override(message: str) -> Optional[ModelName]:
    """Return an explicitly requested model, or None if the user didn't ask.

    Lets a student pin a specific model, e.g. "please use opus for this
    question". This takes precedence over every automatic rule below.
    """
    text = message.lower()
    for phrase, model in _OVERRIDE_PHRASES.items():
        if phrase in text:
            return model  # type: ignore[return-value]
    return None


def _has_complex_signal(message: str) -> bool:
    """True if the message reads like a hard, multi-step derivation problem."""
    text = message.lower()
    return any(keyword in text for keyword in _COMPLEX_KEYWORDS)


def _looks_like_followup(message: str, buffer: Optional[dict]) -> bool:
    """True if the message reads like a short follow-up to an ongoing topic.

    Two things must hold:
      1. There is an existing conversation (a non-empty summary buffer), so
         "follow-up" is even meaningful.
      2. The message is short and/or opens with a clarification phrase.
    """
    # No prior context => this is the first turn, so it cannot be a follow-up.
    if not buffer or not buffer.get("topic"):
        return False

    text = message.lower().strip()
    is_short = len(text.split()) <= _SHORT_MESSAGE_WORDS
    starts_like_clarification = any(text.startswith(k) for k in _FOLLOWUP_KEYWORDS)

    return is_short or starts_like_clarification


def classify(
    message: str,
    has_image: bool = False,
    buffer: Optional[dict] = None,
) -> ModelName:
    """Route a turn to "opus", "sonnet", or "haiku".

    We collect every model the message could reasonably qualify for, then
    escalate to the most capable one. This guarantees "highest complexity
    wins" structurally, so the rules can be listed in any order without
    changing behavior:

        - Image attached            -> Opus (needs vision + careful reasoning)
        - Multi-step derivation     -> Opus (hardest conceptual problems)
        - Short follow-up on-topic  -> Haiku (cheap clarifications)

    If none of the positive signals fire, we fall back to Sonnet, the default
    for a moderate or new-topic question.
    """
    # 0. A manual override ("please use opus for this") beats every heuristic.
    override = _detect_override(message)
    if override is not None:
        return override

    candidates: list[ModelName] = []

    # Any attached image needs vision + careful reasoning -> Opus.
    if has_image:
        candidates.append("opus")

    # Hard, multi-step derivations go to Opus even without an image.
    if _has_complex_signal(message):
        candidates.append("opus")

    # A short clarification while a topic is already in play -> Haiku.
    if _looks_like_followup(message, buffer):
        candidates.append("haiku")

    # No positive signal => moderate / new-topic question -> Sonnet default.
    if not candidates:
        return "sonnet"

    # Escalate on ambiguity: pick the highest-capability candidate.
    return max(candidates, key=_CAPABILITY_RANK.__getitem__)
