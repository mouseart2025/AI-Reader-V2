"""Rebuttal baseline arm — single-shot LLM-CoT hierarchy construction on the
synthetic contamination-free novel 《星尘劫》 (Star-Dust Calamity).

Second baseline row for Table A.1 (alongside stardust_voting_arm.py): give
DeepSeek V3 the SAME evidence the Edmonds pipeline sees (location set +
per-location parent candidates with vote counts + per-chapter location
summaries) and ask it to reason once and emit the full containment tree as
JSON. Evidence gathering and prompts are reused verbatim from
single_shot_cot_baseline.py; only the model client changes (DeepSeek
deepseek-chat instead of Claude) and the gold is the contamination-free
gold_standard.json.

Read-only against the real DB (sqlite SELECTs only, no src imports, no
writes — the frozen-DB rule concerns writes).

Usage:
    cd backend && uv run python scripts/stardust_cot_arm.py

Output:
    ../../ai-reader-internal/paper/evaluation/contamination-free/novel/cot-baseline.json
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

NOVEL_TITLE = "星尘劫"
NOVEL_ID = "336fa0fa-cc30-43c0-8843-2d28a82e755a"

OUT_DIR = Path(
    "/Users/leonfeng/Baiduyun/AISoul/ai-reader-internal/paper/evaluation/contamination-free/novel"
)
OUT_PATH = OUT_DIR / "cot-baseline.json"

MODEL = "deepseek-chat"  # DeepSeek V3
BASE_URL = "https://api.deepseek.com/v1"
MAX_OUTPUT_TOKENS = 16384
# DeepSeek V3 list prices (USD / 1M tokens), cache-miss input
PRICE_IN = 0.27
PRICE_OUT = 1.10


def _load_cot_module():
    """Import single_shot_cot_baseline.py to reuse evidence loading + prompts."""
    spec = importlib.util.spec_from_file_location(
        "single_shot_cot_baseline",
        Path(__file__).parent / "single_shot_cot_baseline.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def deepseek_chat(system: str, user: str) -> dict:
    """Minimal async DeepSeek client via httpx (pattern from synthesize_novel.py)."""
    import httpx

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        sys.exit("DEEPSEEK_API_KEY not set (check backend/.env).")

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.0,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=300.0,
    ) as client:
        resp = await client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
    return {
        "content": data["choices"][0]["message"]["content"],
        "prompt_tokens": data["usage"]["prompt_tokens"],
        "completion_tokens": data["usage"]["completion_tokens"],
    }


def count_cycles(parents: dict[str, str]) -> int:
    """Count distinct cycles (same口径 as stardust_voting_arm / m5 ablation)."""
    cycles = 0
    done: set[str] = set()
    for start in parents:
        if start in done:
            continue
        path: dict[str, int] = {}
        node = start
        while node in parents and node not in done and node not in path:
            path[node] = len(path)
            node = parents[node]
        if node in path and node not in done:
            cycles += 1
        done.update(path)
    return cycles


async def main() -> None:
    cot = _load_cot_module()

    print(f"=== 《{NOVEL_TITLE}》 — single-shot CoT, model={MODEL} ===")
    print("  Gathering evidence (read-only from real DB)...")
    evidence = cot.load_evidence(NOVEL_ID)
    print(f"  Locations: {len(evidence['loc_mentions'])}")
    print(f"  Chapters with facts: {len(evidence['chapter_summaries'])}")

    prompt = cot.build_prompt(evidence, NOVEL_TITLE)
    print(f"  Prompt length: {len(prompt):,} chars (~{len(prompt) // 2:,} tokens)")

    print("  Calling DeepSeek...")
    resp = await deepseek_chat(cot.SYSTEM_PROMPT, prompt)
    content = resp["content"]
    in_tok = resp["prompt_tokens"]
    out_tok = resp["completion_tokens"]
    cost = in_tok / 1_000_000 * PRICE_IN + out_tok / 1_000_000 * PRICE_OUT
    print(f"  Tokens: input={in_tok:,} output={out_tok:,} Cost≈${cost:.4f}")

    # Parse JSON (LLM may still wrap in markdown despite the prompt)
    text = content.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raw_file = OUT_DIR / "cot-baseline-raw.txt"
        raw_file.write_text(content)
        sys.exit(f"JSON parse error: {e}; raw saved: {raw_file}")

    predicted_parents: dict[str, str] = parsed.get("父子关系") or parsed.get("parents") or {}
    stated_roots: list[str] = parsed.get("顶级地点") or parsed.get("roots") or []

    # ── Structural metrics ──
    ch_count = Counter(predicted_parents.values())
    top = ch_count.most_common(1)
    all_nodes = set(predicted_parents.keys()) | set(predicted_parents.values()) | set(stated_roots)
    roots = all_nodes - set(predicted_parents.keys())
    cycles = count_cycles(predicted_parents)

    # ── Coverage vs input evidence ──
    input_locs = set(evidence["loc_mentions"].keys())
    output_locs = set(predicted_parents.keys()) | set(stated_roots)
    missed = input_locs - output_locs
    hallucinated = output_locs - input_locs

    # ── Accuracy vs gold (same parent-precision口径 as eval_contamination_free) ──
    gold_locs = json.loads((OUT_DIR / "gold_standard.json").read_text()).get("locations") or []
    gold_names = {L["name"] for L in gold_locs}
    gold_parents = {L["name"]: L.get("correct_parent") for L in gold_locs}
    parent_total = 0
    parent_correct = 0
    parent_mismatches: list[list[str]] = []
    for name, gold_p in gold_parents.items():
        if gold_p is None:
            continue
        if name in predicted_parents:
            parent_total += 1
            if predicted_parents[name] == gold_p:
                parent_correct += 1
            else:
                parent_mismatches.append([name, gold_p, predicted_parents[name]])
    parent_precision = parent_correct / parent_total if parent_total else 0.0
    extracted_gold = gold_names & output_locs
    entity_recall = len(extracted_gold) / len(gold_names) if gold_names else 0.0

    result = {
        "_description": (
            "Rebuttal baseline arm for Table A.1: single-shot LLM-CoT hierarchy "
            "construction on the synthetic contamination-free novel 《星尘劫》. "
            "DeepSeek V3 receives the same evidence as the Edmonds pipeline "
            "(locations + parent-candidate votes + chapter location summaries, "
            "prompts reused verbatim from single_shot_cot_baseline.py) and emits "
            "the full containment tree in one call. No iterative voting, no "
            "Edmonds, no post-processing."
        ),
        "_script": "backend/scripts/stardust_cot_arm.py",
        "novel": NOVEL_TITLE,
        "novel_id": NOVEL_ID,
        "contamination_status": "synthetic (DeepSeek V3 generated, not in LLM pretraining)",
        "arm": "single-shot LLM-CoT (deepseek-chat / DeepSeek V3)",
        "model": MODEL,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd": round(cost, 4),
        "input_locations": len(input_locs),
        "output_parent_assignments": len(predicted_parents),
        "stated_roots": len(stated_roots),
        "structural": {
            "max_children": top[0][1] if top else 0,
            "max_children_node": top[0][0] if top else "",
            "root_count": len(roots),
            "roots": sorted(roots),
            "cycles": cycles,
            "has_cycle": cycles > 0,
        },
        "coverage": {
            "missed_count": len(missed),
            "missed_locations": sorted(missed),
            "hallucinated_count": len(hallucinated),
            "hallucinated_locations": sorted(hallucinated),
        },
        "accuracy_vs_gold": {
            "entity_recall": round(entity_recall, 4),
            "parent_precision": round(parent_precision, 4),
            "parent_total": parent_total,
            "parent_correct": parent_correct,
            "parent_mismatches": parent_mismatches,
        },
        "parents": predicted_parents,
        "stated_roots_list": stated_roots,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"  Saved: {OUT_PATH}")
    print(
        f"  Summary: locs={len(input_locs)} → assignments={len(predicted_parents)} "
        f"max_ch={result['structural']['max_children']} roots={len(roots)} "
        f"cycles={cycles} missed={len(missed)} halluc={len(hallucinated)} "
        f"parent_P={parent_precision:.3f}"
    )


if __name__ == "__main__":
    asyncio.run(main())
