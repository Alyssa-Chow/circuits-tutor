# Circuits Tutor — Claude Code Session

## Goal
Build Sections 1 & 2 of the V1 plan: a FastAPI backend with keyword-based model
routing (Opus/Sonnet/Haiku) and a Haiku-driven summary buffer that compresses
conversation history in place of passing full history on follow-ups. RAG and the
frontend were out of scope.

## What Was Built
- `router.py` — model classifier (Opus/Sonnet/Haiku)
- `summary_buffer.py` — conversation compression system
- `main.py` — FastAPI `/chat` endpoint wiring both together
- `benchmark.py` — cost comparison harness (routed+buffer vs. single-model)
- Test suite: 13 passing (5 router, 4 summary_buffer, 2 integration, 2 offline endpoint)

## Key Decisions & Why
(Pulled from `DECISIONS.md`; each lists the rejected alternative.)

> **Attribution note.** This summary was reconstructed from committed artifacts
> (`DECISIONS.md`, `benchmark_report.md`, the code, the tests), not from a
> transcript of the original session. Unless a decision is explicitly attributed
> below, treat it as **"Decided during the session"** with no recoverable actor —
> the record of who drove each call could not be reconstructed from the artifacts,
> and guessing would misrepresent it. Where a decision *is* evidence-backed, the
> attribution is stated inline in italics (look for "— the user's idea / call /
> request"); everything without such a note is neutral by design. The user is
> adding these from memory where they want them explicit.

**Architecture**
- **In-memory state** — buffer stored in a dict keyed by `conversation_id`. Rejected: client resends buffer each request / SQLite-backed store. Simplest for local V1; resets on restart, acceptable now.
- **Multipart image uploads** — *the user reasoned this through independently (leaning to FastAPI multipart for handling uploads) before I weighed in.* Images arrive as FastAPI `UploadFile`. Rejected: base64-encoded string in a JSON body. `python-multipart` already installed; keeps binary out of the JSON payload.
- **Blob schema as documented** — *the user considered amending it but chose to keep it matching CLAUDE.md.* Fields kept: `topic` / `concepts_covered` / `student_struggle_points` / `last_2_messages`. Rejected: amending fields. Already covers V1.
- **Summary-quality eval = known gap (deferred)** — mocked unit tests validate plumbing only (storage, schema, context fold-in); real-Haiku summary quality needs a live eval. Rejected: asserting quality in mocked tests / calling live Haiku in the unit suite. Options noted but not built: V1.1 manual-inspection harness, V2 LLM-as-judge.

**Files**
- **router.py — escalate on ambiguity + manual override** — *the user explicitly specified this: the selected model must correspond to the highest difficulty category the question could possibly fall into, unless the user requests a different model in the question.* `classify()` implements this by collecting every model a message qualifies for and returning the highest-capability one; a manual override ("use opus") is checked first and beats all heuristics. Rejected: first-match if/elif with no override path.
- **MODEL_IDS single source of truth** — model IDs defined once in `router.py`, imported elsewhere. Rejected: hardcoding IDs per module.
- **summary_buffer.py — structured outputs** — Haiku returns the blob via `output_config.format` with the JSON schema. Rejected: prompting for JSON and parsing a free-form reply.
- **Tests mock the Anthropic client** — *testing as you go, file by file, was the user's instinct, which I confirmed.* Implemented with a `FakeClient` that returns canned blobs and records the prompt sent. Rejected: hitting live Haiku in tests. Deterministic, offline, free; lets tests assert the follow-up prompt folds in the prior blob. (The mock-vs-live implementation choice is unattributed.)
- **main.py — tutor persona + call shape** — *the tutor persona and its core rule — guide methodology, withhold the answer unless explicitly asked — were the user's idea.* Implemented as a hardcoded system prompt. The call shape (`max_tokens=2048`, no extended thinking; rejected adaptive thinking / a larger token budget for V1) is unattributed.
- **main.py — built on Opus (plan deviation) — *the user's call.*** CLAUDE.md assigns Section 1 scaffolding to Sonnet, but the user chose to proceed on the Opus build-model rather than switch mid-flow. Rejected: switching to Sonnet first. Logged in `DECISIONS.md` as a conscious deviation.

**Integration**
- **Router sees the summary buffer** — *the user's call, discussed explicitly: they weighed the coupling tradeoff and chose to have the router compare the new message against the buffer's `topic` to catch follow-ups.* Rejected: the fully decoupled, message-text-only option.
- **conversation_id server-generated if omitted** — *the user reasoned this through themselves — a server-minted UUID is more reliable than assuming the frontend generates/tracks one accurately.* `/chat` mints a UUID when absent and echoes it back. Rejected: requiring the client to always supply it.
- **Buffer injected into the tutor call** — *the user's pick.* When a buffer exists, the blob is added to the tutor system prompt as context (compressed context replaces full history). Rejected: using the buffer for routing only.
- **Image base64-encoded into the message** — *the user's pick.* `UploadFile` bytes encoded into an image content block on the model call. Rejected: save-to-disk-and-reference.

## A Bug We Caught
*(This is the one part of the session with a clear who-did-what sequence, so it's
attributed directly.)*

**You raised it.** `update_buffer` was originally called only after Opus/Sonnet
turns — a literal reading of CLAUDE.md ("after Opus/Sonnet responses, Haiku
compresses"). You noticed the buffer wasn't updating on Haiku turns and raised it
as a concern.

**I confirmed it was a real bug and explained the mechanism.** The buffer is
injected into *every* tutor call but was only *refreshed* after substantive turns.
So a run of Haiku follow-ups would go uncaptured — `last_2_messages` and
`student_struggle_points` would freeze at the last Opus/Sonnet turn. If a student
surfaced a misconception in what looked like a throwaway clarification (exactly the
kind of turn routed to Haiku), it was lost, and the next turn would inject a stale
blob — undercutting the struggle-tracking the blob exists for.

**I laid out the tradeoff:** info-loss on follow-ups vs. the cost of always
updating. The compression step already runs on Haiku regardless of which model
answered, so updating after a Haiku turn adds one cheap Haiku call; the only real
cost is ~1s added latency per follow-up, not tokens.

**I proposed the fix; you approved it.** Call `update_buffer` on **every** turn,
Haiku follow-ups included. Code, both `DECISIONS.md` data-flow diagrams, and
CLAUDE.md's Summary Buffer section were updated to match. Verified end-to-end by
`test_endpoint.py`, which counts summary calls (2 after a Sonnet turn + a Haiku
follow-up).

## Verification
- **Live E2E** — real `uvicorn` server, real API calls, correct routing confirmed:
  - New-topic question ("equivalent resistance of 4Ω ∥ 12Ω") → routed to **Sonnet**; response guided methodology and withheld the numeric answer.
  - Short follow-up ("wait, why do we flip it?") on the same `conversation_id` → routed to **Haiku**, continuing the thread — confirming the buffer was created, seen by the router, and injected into the tutor call.
- **Test suite** — 13 passing: 5 router, 4 summary_buffer, 2 integration, 2 offline endpoint (all mocked/offline).
- **Benchmark** — *the user described this test: run the whole system end-to-end and compare cost across the three models on their own (no router) against the routed setup.* Realized as 4 conditions (Opus-only, Sonnet-only, Haiku-only, Routed+buffer) over 3 multi-turn RC-circuits conversations, each with at least one follow-up that needs Opus. Single-model conditions pass full history each turn (the naive default); the routed condition uses the real system and **counts its Haiku summary calls against itself**. Cost from real `usage` at sticker prices; full responses saved to `benchmark_report.md` for manual quality grading.

## Result
Cost per condition (real API usage, USD, sticker prices; 3-turn run):

| Conversation | Opus-only | Sonnet-only | Haiku-only | Routed+buffer |
|---|---|---|---|---|
| easy | $0.0402 | $0.0274 | $0.0063 | $0.0185 |
| medium | $0.0341 | $0.0260 | $0.0058 | $0.0285 |
| hard | $0.0493 | $0.0495 | $0.0124 | $0.0399 |
| **TOTAL** | **$0.1236** | **$0.1028** | **$0.0245** | **$0.0869** |

**Routed+buffer is 29.7% cheaper than Opus-only** (more once Sonnet's intro
pricing is applied), while spending Opus *only* on the turns that needed it — the
hard derivation/proof turns — and using Haiku for cheap clarifications. This is
the intended payoff: Opus-level capability on the turns that require it, without
paying Opus rates for every message.
