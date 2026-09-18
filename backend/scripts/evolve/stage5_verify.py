"""GeoEvolve 阶段 5 —— 正式证据链：当前最优 genome 复测（§7.3/§7.4）。

对当前工作区状态（阶段 1 词表 142 条 delta + 阶段 2 默认权重 + 阶段 3
gen64 prompt 变异）复测关键指标：
  1. topo 换序复测（阶段 2 机制：--permute-chapters 7/23，纯规则）——
     与 weights_state.baseline_metrics 对照，抖动 vs 基线噪声底 0.0209+0.01
  2. prompt recall 复测（阶段 3 快速层重跑，~16 次 LLM 调用 ≈$0.15）——
     与 journal gen64 记录对照，抖动 vs 噪声底 macro 0.03 / 单本 0.09
  3. geo.unresolved_rate 确定性复算（规则,无噪声,应逐值一致）

输出：out/stage5_verification.json + stdout 摘要。

Usage:
    cd backend && .venv/bin/python scripts/evolve/stage5_verify.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_EVOLVE_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _EVOLVE_DIR.parent.parent
for _p in (str(_BACKEND_DIR), str(_BACKEND_DIR / "scripts"), str(_EVOLVE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import prompt_evolve as pe  # noqa: E402
import run_loop as rl  # noqa: E402
import weight_jitter as wj  # noqa: E402

OUT_PATH = _EVOLVE_DIR / "out" / "stage5_verification.json"


def verify_topo(weights_state: dict) -> dict:
    """topo 换序复测：默认参数(当前 overrides 为空) vs baseline_metrics。"""
    baseline = weights_state["baseline_metrics"]
    results = {}
    for seed in (7, 23):
        raw = rl.compute_weight_metrics_subprocess(permute_seed=seed)
        vec = rl.stage2_metric_vector(raw)
        jitters = {k: round(abs(vec[k] - baseline[k]), 4)
                   for k in baseline if ".topo." in k and k in vec}
        results[f"seed{seed}"] = {"max_jitter": max(jitters.values()),
                                  "jitters": jitters}
    limit = 0.0209 + 0.01  # 基线噪声底 + 阈值(v2.2 口径)
    ok = all(r["max_jitter"] <= limit for r in results.values())
    return {"limit": limit, "pass": ok, "seeds": results,
            "note": "当前权重=默认(overrides 为空),复测的是默认 genome 的换序稳定性"}


async def _prompt_fast_layer(state: dict, fixtures: dict, cost_acc: dict) -> dict:
    return await rl._eval_stage3_candidate(state, fixtures, cost_acc,
                                           rl.LlmBudget(100))


def verify_prompt_recall() -> dict:
    """prompt recall 复测：当前 committed prompt(gen64 变异)重跑快速层。"""
    from dotenv import load_dotenv

    load_dotenv(_BACKEND_DIR / ".env", override=True)
    state = pe.load_state()
    assert state["override_section"] is not None, "无已接受 prompt 变异"
    fixtures = {
        "chapters": json.loads(pe.CHAPTERS_FIXTURE.read_text(encoding="utf-8")),
        "t_set": json.loads(pe.T_SET_FIXTURE.read_text(encoding="utf-8")),
        "e0": json.loads(pe.E0_FIXTURE.read_text(encoding="utf-8")),
        "genres": pe.load_genre_hints(),
    }
    # journal gen64 的原始测量
    journal = rl._load_jsonl_tail(rl.JOURNAL_PATH, n=10_000)
    gen64 = next(r for r in journal if r.get("generation") == 64)
    ref = gen64["metrics"]

    cost_acc = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
    res = asyncio.run(_prompt_fast_layer(state, fixtures, cost_acc))
    vec = rl._stage3_vec_from_metrics(res["metrics"])
    keys = [k for k in vec if ".prompt.recall" in k]
    jitters = {k: round(abs(vec[k] - ref[k]), 4) for k in keys}
    limits = {"macro.prompt.recall": 0.03}  # 单本 0.09
    ok = all(j <= (0.09 if k != "macro.prompt.recall" else 0.03)
             for k, j in jitters.items())
    return {"pass": ok, "jitters": jitters,
            "ref_macro": ref["macro.prompt.recall"],
            "remeasure_macro": vec["macro.prompt.recall"],
            "cost_usd": round(cost_acc["cost_usd"], 4),
            "limits": {**limits, "per_novel": 0.09}}


def verify_geo() -> dict:
    """geo.unresolved_rate 确定性复算(规则,应与 vocab_delta 终态一致)。"""
    geo = rl.compute_geo_metrics_subprocess()
    return {"rates": {s: round(m["unresolved_rate"], 6) for s, m in geo.items()},
            "note": "规则评估无噪声;终态水浒应为 0.687036(journal gen21)"}


def main() -> int:
    ws = wj.load_weights_state()
    out = {
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
        "genome_state": {
            "vocab_delta_entries": len(json.loads(
                (_EVOLVE_DIR / "vocab_delta.json").read_text())["entries"]),
            "weights_overrides": ws["overrides"],
            "prompt_history": json.loads(
                (_EVOLVE_DIR / "prompt_state.json").read_text())["history"],
        },
    }
    print("[s5] 1/3 topo 换序复测...")
    out["topo_permute"] = verify_topo(ws)
    print(f"[s5] topo: pass={out['topo_permute']['pass']} "
          f"max_jitter={[v['max_jitter'] for v in out['topo_permute']['seeds'].values()]}")
    print("[s5] 2/3 prompt recall 复测(快速层重跑,~$0.15)...")
    out["prompt_recall_remeasure"] = verify_prompt_recall()
    print(f"[s5] prompt: pass={out['prompt_recall_remeasure']['pass']} "
          f"macro {out['prompt_recall_remeasure']['ref_macro']:.4f} → "
          f"{out['prompt_recall_remeasure']['remeasure_macro']:.4f} "
          f"(成本 ${out['prompt_recall_remeasure']['cost_usd']})")
    print("[s5] 3/3 geo 确定性复算...")
    out["geo_rerun"] = verify_geo()
    print(f"[s5] geo: {out['geo_rerun']['rates']}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(f"[s5] 已写 {OUT_PATH}")
    return 0 if (out["topo_permute"]["pass"]
                 and out["prompt_recall_remeasure"]["pass"]) else 1


if __name__ == "__main__":
    sys.exit(main())
