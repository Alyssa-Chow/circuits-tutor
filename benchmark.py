"""Cost/quality benchmark: routing + buffer vs. single-model defaults.

Compares four ways to run the same multi-turn RC-circuits conversations:
    1. Opus for every turn      (naive "just use the best model" default)
    2. Sonnet for every turn
    3. Haiku for every turn
    4. Our actual system: router.classify per turn + summary-buffer context

Single-model conditions pass full conversation history each turn (what you get
by just chatting in a Claude project). The routed condition uses the real
system, and its Haiku summary-buffer calls are counted against it so the cost
comparison is fair.

Design notes:
    - Measurement (token counts) is separated from pricing, so a single run is
      costed under BOTH the sticker rate and Sonnet's current intro rate.
    - --trials averages cost across repeated runs to smooth out the natural
      variance in response length.

Usage:
    venv/bin/python benchmark.py --plan            # offline routing plan, no API calls
    venv/bin/python benchmark.py                   # 1 trial, real API calls
    venv/bin/python benchmark.py --trials 3        # 3 trials, averaged (3x the spend)
"""

import argparse
import statistics

from dotenv import load_dotenv
from anthropic import Anthropic

from router import MODEL_IDS, classify
from summary_buffer import get_buffer, update_buffer
import summary_buffer as sb
from main import _TUTOR_SYSTEM, _build_system_prompt

load_dotenv()

# USD per 1M tokens (input, output). Two regimes so one run can be costed both
# ways: STICKER is the list price; INTRO reflects Sonnet's promotional $2/$10
# through 2026-08-31 (Opus/Haiku unchanged).
PRICING_STICKER = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}
PRICING_INTRO = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),  # intro pricing (through 2026-08-31)
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}

MAX_TOKENS = 2048  # matches main.py's tutor reply budget

# Three RC-circuits conversations (4 turns each). Each opens with a new-topic
# question and adds follow-ups; at least one follow-up needs Opus (a
# derivation/proof) so routing has to mix cheap and capable models within one
# conversation.
CONVERSATIONS = {
    "easy": [
        "What is the time constant of an RC circuit, and what does it physically represent?",
        "why is it called 'constant' if the capacitor voltage keeps changing?",
        "so after one time constant, what fraction has it charged to?",
        "and roughly how many time constants until it's basically fully charged?",
    ],
    "medium": [
        "In an RC charging circuit with a 5V source, R=10k ohm and C=100uF, how do I find the capacitor voltage at t = 2 seconds?",
        "wait, why does it approach 5V instead of overshooting past it?",
        "can you walk me through the step-by-step derivation of the charging equation?",
        "what would change if I doubled the resistance?",
    ],
    "hard": [
        "Derive the differential equation for a series RC circuit and solve for the transient voltage response.",
        "why does the natural response decay exponentially rather than linearly?",
        "prove that the time constant tau = RC has the correct units of seconds.",
        "how would the solution differ for an RL circuit instead?",
    ],
}


def _text(response) -> str:
    return next((b.text for b in response.content if b.type == "text"), "")


def _cost(token_agg, pricing) -> float:
    """Cost of a {model_id: (input_tokens, output_tokens)} aggregate under a rate table."""
    total = 0.0
    for model_id, (tin, tout) in token_agg.items():
        p_in, p_out = pricing[model_id]
        total += tin / 1e6 * p_in + tout / 1e6 * p_out
    return total


# --- Usage-tracking client wrapper -----------------------------------------
class _TrackingMessages:
    def __init__(self, parent):
        self._parent = parent

    def create(self, **kwargs):
        resp = self._parent.real.messages.create(**kwargs)
        self._parent.calls.append(
            {
                "model": kwargs["model"],
                "input": resp.usage.input_tokens,
                "output": resp.usage.output_tokens,
            }
        )
        return resp


class TrackingClient:
    """Wraps a real Anthropic client and records raw usage for every call."""

    def __init__(self, real):
        self.real = real
        self.calls = []
        self.messages = _TrackingMessages(self)

    def token_agg(self):
        """Collapse calls into {model_id: (input_tokens, output_tokens)}."""
        agg = {}
        for c in self.calls:
            tin, tout = agg.get(c["model"], (0, 0))
            agg[c["model"]] = (tin + c["input"], tout + c["output"])
        return agg


# --- Condition runners -----------------------------------------------------
def run_single_model(client, model_id, turns, _cid):
    """One model for every turn, passing full history (the naive default)."""
    history = []
    results = []
    for turn in turns:
        history.append({"role": "user", "content": turn})
        resp = client.messages.create(
            model=model_id, max_tokens=MAX_TOKENS, system=_TUTOR_SYSTEM, messages=history
        )
        reply = _text(resp)
        history.append({"role": "assistant", "content": reply})
        results.append({"model_key": model_id, "reply": reply})
    return results


def run_routed(client, _model_id, turns, cid):
    """Our real system: classify per turn, inject buffer, update buffer each turn."""
    sb._BUFFERS.pop(cid, None)  # start clean
    results = []
    for turn in turns:
        buffer = get_buffer(cid)
        model_key = classify(turn, has_image=False, buffer=buffer)
        resp = client.messages.create(
            model=MODEL_IDS[model_key],
            max_tokens=MAX_TOKENS,
            system=_build_system_prompt(buffer),
            messages=[{"role": "user", "content": turn}],
        )
        reply = _text(resp)
        update_buffer(client, cid, turn, reply)  # real Haiku summary call (counted)
        results.append({"model_key": model_key, "reply": reply})
    return results


CONDITIONS = [
    ("Opus-only", MODEL_IDS["opus"], run_single_model),
    ("Sonnet-only", MODEL_IDS["sonnet"], run_single_model),
    ("Haiku-only", MODEL_IDS["haiku"], run_single_model),
    ("Routed+buffer", None, run_routed),
]


# --- Offline routing plan (no API calls) -----------------------------------
def show_plan():
    print("Routing plan (offline — classify() only, no API calls):\n")
    for level, turns in CONVERSATIONS.items():
        print(f"[{level.upper()}]")
        sb._BUFFERS.pop(f"plan-{level}", None)
        for i, turn in enumerate(turns, 1):
            buffer = get_buffer(f"plan-{level}")
            key = classify(turn, has_image=False, buffer=buffer)
            sb._BUFFERS[f"plan-{level}"] = {"topic": level}  # simulate buffer after turn 1
            print(f"  turn {i} -> {key:6} | {turn[:66]}")
        print()
    turns_total = sum(len(t) for t in CONVERSATIONS.values())
    print("Real-run call estimate (per trial):")
    print(f"  Opus/Sonnet/Haiku-only: {turns_total} calls each")
    print(f"  Routed: {turns_total} tutor + {turns_total} summary = {turns_total * 2} calls")
    print(f"  All four conditions: ~{turns_total * 5} calls/trial")


# --- Real benchmark --------------------------------------------------------
def run_benchmark(trials):
    real = Anthropic()
    report = ["# Benchmark: routing + buffer vs. single-model defaults\n"]
    report.append(f"_Trials per condition: {trials}. Costs are means across trials._\n")

    # grids[regime][level][condition] = mean cost
    grids = {"sticker": {}, "intro": {}}
    routing_by_level = {}  # level -> per-turn model keys (from trial 1 of the routed run)

    for level, turns in CONVERSATIONS.items():
        report.append(f"\n## {level.capitalize()} conversation\n")
        for i, t in enumerate(turns, 1):
            report.append(f"{i}. {t}")
        report.append("")
        grids["sticker"].setdefault(level, {})
        grids["intro"].setdefault(level, {})

        for name, model_id, runner in CONDITIONS:
            sticker_costs, intro_costs = [], []
            rep_results = None  # representative responses (trial 1) for the report

            for t in range(trials):
                tracker = TrackingClient(real)
                cid = f"bench-{level}-{name}-t{t}"
                print(f"[{level}] {name} trial {t + 1}/{trials} ...", flush=True)
                results = runner(tracker, model_id, turns, cid)
                agg = tracker.token_agg()
                sticker_costs.append(_cost(agg, PRICING_STICKER))
                intro_costs.append(_cost(agg, PRICING_INTRO))
                if t == 0:
                    rep_results = results

            grids["sticker"][level][name] = statistics.mean(sticker_costs)
            grids["intro"][level][name] = statistics.mean(intro_costs)
            if name == "Routed+buffer":
                routing_by_level[level] = [r["model_key"] for r in rep_results]

            spread = ""
            if trials > 1:
                spread = f" (sticker range ${min(sticker_costs):.4f}–${max(sticker_costs):.4f})"
            report.append(f"### {name} — mean ${statistics.mean(sticker_costs):.4f} sticker / ${statistics.mean(intro_costs):.4f} intro{spread}")
            models_line = " -> ".join(
                (r["model_key"].split("-")[1] if r["model_key"].startswith("claude") else r["model_key"])
                for r in rep_results
            )
            report.append(f"_models per turn: {models_line}_\n")
            for i, r in enumerate(rep_results, 1):
                report.append(f"**Turn {i}** (`{r['model_key']}`):\n\n{r['reply']}\n")
            report.append("")

    _print_and_append_tables(grids, routing_by_level, trials, report)

    with open("benchmark_report.md", "w") as f:
        f.write("\n".join(report))
    print("\nFull responses + tables written to benchmark_report.md")


def _print_and_append_tables(grids, routing_by_level, trials, report):
    names = [n for n, _, _ in CONDITIONS]

    for regime, label in (("sticker", "STICKER"), ("intro", "INTRO (Sonnet $2/$10)")):
        grid = grids[regime]
        print("\n" + "=" * 64)
        print(f"COST COMPARISON — {label} (USD, mean of {trials} trial(s))")
        print("=" * 64)
        col = "{:<16}" + "{:>14}" * len(names)
        print(col.format("conversation", *names))
        report.append(f"\n## Cost comparison — {label} (USD)\n")
        report.append("| conversation | " + " | ".join(names) + " |")
        report.append("|" + "---|" * (len(names) + 1))

        totals = {n: 0.0 for n in names}
        for level in CONVERSATIONS:
            vals = [grid[level][n] for n in names]
            for n, v in zip(names, vals):
                totals[n] += v
            print(col.format(level, *[f"${v:.4f}" for v in vals]))
            report.append(f"| {level} | " + " | ".join(f"${v:.4f}" for v in vals) + " |")
        print(col.format("TOTAL", *[f"${totals[n]:.4f}" for n in names]))
        report.append("| **TOTAL** | " + " | ".join(f"**${totals[n]:.4f}**" for n in names) + " |")

        opus_t, routed_t = totals["Opus-only"], totals["Routed+buffer"]
        if opus_t:
            pct = (opus_t - routed_t) / opus_t * 100
            line = f"Routed+buffer ${routed_t:.4f} vs Opus-only ${opus_t:.4f} -> {pct:.1f}% cheaper."
            print(line)
            report.append(f"\n{line}")

    # Routing insight: how many turns Opus-only overspent on.
    print("\n" + "=" * 64)
    print("ROUTING INSIGHT")
    print("=" * 64)
    report.append("\n## Routing insight\n")
    for level, keys in routing_by_level.items():
        non_opus = sum(1 for k in keys if k != "opus")
        line = f"{level}: routed used {keys} — {non_opus}/{len(keys)} turns ran on a cheaper model than Opus-only would have."
        print(line)
        report.append(f"- {line}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", action="store_true", help="offline routing plan only, no API calls")
    parser.add_argument("--trials", type=int, default=1, help="repeat each condition N times and average (N x the spend)")
    args = parser.parse_args()

    if args.plan:
        show_plan()
    else:
        run_benchmark(args.trials)
