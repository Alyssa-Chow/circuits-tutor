# Architectural Decisions

A running log of the calls we make while building. Each entry: what we decided,
the alternative we rejected, and one line on why. Organized into three groups —
**Architecture** (system-wide), **Files** (scoped to one module), and
**Integration** (how the pieces wire together).

---

## Data Flow

How a request travels through the system, from client to tutor model and back.
Image (if any) is read and base64-encoded in `main._build_user_content` just
before the model call; `conversation_id` is minted at the top of `/chat` if the
client didn't send one.

### New-topic question (no existing buffer)

```
Client → POST /chat (conversation_id?, message, image?)
  → main.py: conversation_id = provided OR new uuid4()
  → get_buffer(conversation_id) → None            (nothing stored yet)
  → [if image] read UploadFile bytes → base64-encode into image block
  → router.py: classify(message, has_image, buffer=None) → model choice
        (no buffer → new topic → Sonnet; image/complex → Opus)
  → system prompt = tutor persona only            (no blob to inject)
  → tutor model call (Opus/Sonnet) → response text
  → summary_buffer.py: update_buffer() → FIRST blob stored under conversation_id
  → { conversation_id, model, response } → Client
```

### Follow-up question (buffer already exists)

```
Client → POST /chat (conversation_id, message, image?)
  → main.py: reuse the provided conversation_id
  → get_buffer(conversation_id) → existing blob
  → [if image] read UploadFile bytes → base64-encode into image block
  → router.py: classify(message, has_image, buffer) → model choice
        (short + on-topic → Haiku; complex/image → escalates to Opus)
  → inject blob into tutor system prompt          (context replaces full history)
  → tutor model call (Opus/Sonnet/Haiku) → response text
  → summary_buffer.py: update_buffer() on EVERY turn → blob refreshed
        (Haiku follow-ups included, so the buffer never goes stale)
  → { conversation_id, model, response } → Client
```

---

## Architecture

### In-memory state storage
- **Decided:** Store the summary buffer in an in-memory dict keyed by `conversation_id`.
- **Rejected:** Client resends buffer each request; file/SQLite-backed store.
- **Why:** Simplest for local V1; resets on restart, which is acceptable now.

### Multipart image uploads
- **Decided:** Images arrive at `/chat` as `multipart/form-data` file uploads (FastAPI `UploadFile`).
- **Rejected:** Base64-encoded image string in a JSON body.
- **Why:** `python-multipart` is already installed; keeps binary data out of the JSON payload.

### Blob schema as documented
- **Decided:** Keep the summary-buffer blob exactly as specified in CLAUDE.md (`topic`, `concepts_covered`, `student_struggle_points`, `last_2_messages`).
- **Rejected:** Amending the schema (adding/renaming fields).
- **Why:** The documented schema already covers V1 needs; no reason to diverge yet.

### Summary-quality eval — known gap (deferred)
- **Decided:** Keep the mocked summary_buffer unit tests as-is (they validate plumbing: storage, schema, context fold-in) and track summary *quality* as a separate, unbuilt eval.
- **Rejected:** Extending the mocked tests to assert quality, or calling live Haiku inside the unit suite.
- **Why:** The mock returns ideal blobs, so nothing checks whether real Haiku captures the right info from a real conversation. That needs a live API call — an eval, not a unit test.
- **Options (not built):** V1.1 — a lightweight manual-inspection harness (`eval_summary.py`) that prints real blobs for a human to eyeball; V2 — an LLM-as-judge eval scoring blob quality automatically.

---

## Files

### router.py
- **Decided:** `classify()` collects every model a message qualifies for and returns the highest-capability one; a manual override (e.g. "use opus") is checked first and beats every heuristic.
- **Rejected:** First-match `if/elif` ordering with no explicit override path.
- **Why:** Makes "escalate on ambiguity" structural, and lets the student pin a model when they want to.

### router.py — MODEL_IDS as single source of truth
- **Decided:** Define the `MODEL_IDS` map (opus/sonnet/haiku → concrete IDs) in `router.py`; other modules import it.
- **Rejected:** Hardcoding model ID strings separately in each module.
- **Why:** One place to change model IDs; keeps router and summary buffer in sync.

### summary_buffer.py — structured outputs for the blob
- **Decided:** Ask Haiku for the updated blob via `output_config.format` with the blob JSON schema.
- **Rejected:** Prompt for JSON and parse a free-form reply.
- **Why:** Guarantees the reply matches the schema; no brittle string-scraping.

### test_router.py
- **Decided:** Split the "derivation / multi-step" case into two tests (one per keyword) and install `pytest` into the venv.
- **Rejected:** A single combined keyword test.
- **Why:** Each keyword path is covered independently; pytest wasn't present yet.

### Tests mock the Anthropic client
- **Decided:** In `test_summary_buffer.py` (and reused in `test_integration.py`), inject a `FakeClient` whose `messages.create()` returns a canned blob and records the prompt sent.
- **Rejected:** Hitting the live Haiku API from tests.
- **Why:** Keeps tests deterministic, offline, and free; lets us assert the follow-up prompt folds in the prior blob.

### test_integration.py — control-pair assertion
- **Decided:** Prove buffer-driven routing by classifying the same follow-up both with and without the buffer (Haiku vs Sonnet).
- **Rejected:** Asserting only the with-buffer case routes to Haiku.
- **Why:** The contrast shows the buffer context is what changes the routing, not the message text alone.

### test_endpoint.py — offline TestClient E2E
- **Decided:** Drive the real `/chat` via FastAPI `TestClient` with a fake Anthropic client that branches on the `output_config` kwarg to serve tutor replies vs. summary blobs; assert the buffer updates on every turn by counting summary calls.
- **Rejected:** Only testing units, or relying on the live server for E2E coverage.
- **Why:** Gives repeatable, offline, no-cost coverage of the full endpoint wiring — including the "update on every turn" behavior the live run can't assert without a buffer-inspection endpoint.

### benchmark.py — cost comparison methodology
- **Decided:** Compare 4 conditions (Opus-only, Sonnet-only, Haiku-only, Routed+buffer) over 3 multi-turn RC-circuits conversations, each with at least one follow-up that needs Opus. Single-model conditions pass full history each turn (the naive default); the routed condition uses the real system and **counts its Haiku summary calls against itself**. Cost from real `usage` at sticker prices; a `TrackingClient` wrapper records every call.
- **Rejected:** Single-turn questions (routing wouldn't differ); automated quality scoring (user grades responses manually); excluding summary-call cost from the routed condition.
- **Why:** Multi-turn is the only setting where routing/buffer matter; counting summary cost keeps the comparison honest.

### benchmark.py — result (2026-07-05 run)
- **Finding:** Routed+buffer totaled $0.0869 vs Opus-only $0.1236 — **29.7% cheaper** at sticker prices (more with Sonnet's intro pricing), while routing spent Opus only on the hard derivation/proof turns. Full responses in `benchmark_report.md` for manual quality judging.
- **Note:** App-side tutor calls set no `effort`/thinking, so the build agent's effort mode does not affect the measured responses or costs — the numbers stand regardless of when `/effort high` is set.

### benchmark.py — refinement (not yet re-run)
- **Decided:** Extend to 4 turns/conversation; add `--trials N` (average across runs, report min–max spread); separate measurement (token counts) from pricing so one run is costed under both **sticker** and **Sonnet intro** rates; add a routing-insight summary (turns that ran cheaper than Opus-only would have).
- **Rejected:** Hardcoding a single price regime; single-trial-only (noisy on response-length variance).
- **Why / status:** More stable, more honest numbers. Script updated but **not re-run** — the 29.7% finding above is from the original 3-turn run; a fresh 4-turn/`--trials` run is available on request (spend scales with turns x trials x conditions, ~60 calls/trial).

---

## Files

### main.py — tutor persona & call shape
- **Decided:** Hardcode a tutor system prompt (guide methodology, withhold the answer unless asked) and call the model with `max_tokens=2048`, no extended thinking.
- **Rejected:** Adaptive thinking / larger token budget for V1.
- **Why:** Matches CLAUDE.md's product vision; keeps V1 readable and under the non-streaming timeout. Thinking can be added later.

### main.py — load .env via python-dotenv
- **Decided:** Install `python-dotenv` (already in CLAUDE.md's Section 1 list) and `load_dotenv()` at startup so the existing `.env` key is picked up.
- **Rejected:** Hand-parsing `.env`; requiring the key be exported manually.
- **Why:** It's in the approved plan and is the standard way to load the key the Anthropic client reads.

### main.py — built on Opus (plan deviation)
- **Decided:** Build `main.py` while on the Opus build-model.
- **Rejected:** Switching to Sonnet first (CLAUDE.md assigns Section 1 scaffolding to Sonnet).
- **Why:** User chose to proceed on Opus rather than switch mid-flow; logged as a conscious deviation.

---

## Integration

### Router sees the summary buffer
- **Decided:** Pass the current summary buffer into the router's classifier.
- **Rejected:** Classify from the raw incoming message text only.
- **Why:** Lets the router compare the new message against the buffer's `topic` to tell a short follow-up (Haiku) from a new topic (Sonnet).

### conversation_id: server-generated if omitted
- **Decided:** `/chat` accepts an optional `conversation_id`; if missing, the server mints a UUID and echoes it back.
- **Rejected:** Requiring the client to always supply it.
- **Why:** Lets a frontend start a thread without pre-generating an id, while still supporting continuation.

### Buffer injected into the tutor call (not just the router)
- **Decided:** When a buffer exists, inject the blob into the tutor's system prompt as context.
- **Rejected:** Using the buffer for routing only in V1.
- **Why:** Delivers the summary buffer's actual purpose — compressed context in place of full history.

### Image base64-encoded into the message
- **Decided:** Read the multipart `UploadFile` bytes and base64-encode them into an image content block on the model call.
- **Rejected:** Saving the upload to disk and referencing it.
- **Why:** Standard Anthropic vision approach; no file management needed for V1.

### Summary on every turn *(revised — was: only after Opus/Sonnet)*
- **Decided:** Call `update_buffer` on every turn, Haiku follow-ups included.
- **Rejected (original decision):** Updating only after Opus/Sonnet turns, per CLAUDE.md's literal wording.
- **Why revised:** The buffer is injected into every tutor call but was only refreshed after substantive turns, so a chain of Haiku follow-ups left `last_2_messages` / `student_struggle_points` stale — losing info (e.g. a misconception surfaced in a clarification). Compression already runs on cheap Haiku regardless; the only real cost is ~1s added latency per follow-up, which is acceptable. Deviates from CLAUDE.md's wording (doc updated to match).

---

## Future Features

Ideas captured but **not yet built or scheduled**. Each is a candidate, not a
commitment — no design is locked until it moves into a decision section above.

### Onboarding interview → persistent student-reasoning profile
- **Idea:** The first time a student starts a new class/project, the tutor runs a one-time "how do you think through problems" session using problems the student *already* knows, to learn their reasoning style.
- **Goal:** Explain new material in a way that matches how the student already thinks, combined with the tutor's full knowledge base.
- **Requires:** A persistent **student-reasoning-profile**, stored separately from the per-conversation summary buffer (the profile is long-lived and cross-conversation; the buffer is per-thread and ephemeral).
- **Open questions (for when we design it):** Where the profile lives (survives restarts, unlike the in-memory buffer); how it's injected into the tutor system prompt alongside the buffer; how/whether it's updated after the initial interview.
