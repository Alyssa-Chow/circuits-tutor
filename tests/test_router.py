"""Tests for router.classify().

Covers the four core routing rules from CLAUDE.md's model routing logic:
image -> Opus, complex derivation -> Opus, short follow-up -> Haiku,
moderate new-topic question -> Sonnet.
"""

from router import classify


def test_image_attached_routes_to_opus():
    message = "Here's a photo of the circuit diagram from my homework, can you help me analyze it?"
    assert classify(message, has_image=True) == "opus"


def test_derivation_keyword_routes_to_opus():
    message = "Can you derive the transfer function for this second-order RLC circuit?"
    assert classify(message, has_image=False) == "opus"


def test_multistep_keyword_routes_to_opus():
    message = "Please walk me through this step by step using mesh analysis."
    assert classify(message, has_image=False) == "opus"


def test_short_followup_routes_to_haiku():
    buffer = {"topic": "RC circuits", "concepts_covered": ["time constant"]}
    message = "what does that mean?"
    assert classify(message, has_image=False, buffer=buffer) == "haiku"


def test_moderate_new_topic_routes_to_sonnet():
    message = "How do I find the equivalent resistance for resistors combined in a mix of series and parallel?"
    assert classify(message, has_image=False) == "sonnet"
