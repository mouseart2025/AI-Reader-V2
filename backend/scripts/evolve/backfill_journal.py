"""GeoEvolve 阶段 4 —— journal 历史代回填（幂等）。

为历史记录补齐元层字段（§4.4）：
  - policy_version：按 stage 映射（eval_policy 版本演化史 0→1→2→3 与阶段对应；
    版本演化未逐 commit 记录，按代所在阶段归属，口径见 README）
  - state_sha256：历史代的基因组状态指纹。**重建口径**（非当时文件的逐字节哈希）：
    vocab = vocab_delta.json 中 generation ≤ g 的条目名集合（条目自带 generation）；
    weights = journal 中 ≤ g 的 archived 代 weights.set 累积 − state_correction 回退；
    prompt = prompt_state.json history 中 ≤ g 的 sha256 末值。
    指纹 = 三者 canonical JSON 的 sha256。推不出的字段保持 null。
  - context_hash / judge verdicts_path：历史代的提议器输入与 judge 明细未落盘，
    不可重建 → 保持 null（新代由主路径自动带齐）。

幂等：已有非 null 字段的记录跳过；重复运行输出不变。

Usage:
    cd backend && .venv/bin/python scripts/evolve/backfill_journal.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_EVOLVE_DIR = Path(__file__).resolve().parent
JOURNAL_PATH = _EVOLVE_DIR / "evolution_journal.jsonl"

# eval_policy 版本与阶段对应（版本演化未逐 commit 落盘,按阶段归属）
STAGE_POLICY_VERSION = {0: 0, 1: 1, 2: 2, 3: 3}


def _sha(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def _reconstruct_state_fingerprints(records: list[dict]) -> dict[int, str]:
    """按代重建基因组状态指纹（见模块 docstring 的重建口径）。"""
    vocab_entries: dict = {}
    vd = _EVOLVE_DIR / "vocab_delta.json"
    if vd.exists():
        vocab_entries = json.loads(vd.read_text(encoding="utf-8")).get("entries", {})
    ps = _EVOLVE_DIR / "prompt_state.json"
    prompt_history = (json.loads(ps.read_text(encoding="utf-8")).get("history", [])
                      if ps.exists() else [])

    out: dict[int, str] = {}
    weights: dict = {}
    for r in records:
        gen = r.get("generation")
        # 状态变迁按 journal 顺序累积(仅 archived 代的 set / correction 的 unset)
        if r.get("stage") == 2 and isinstance(gen, int):
            if str(r.get("decision", "")).startswith("archived"):
                weights.update(r.get("genome_diff", {}).get("weights.set") or {})
            elif r.get("decision") == "state_correction":
                for k in (r.get("genome_diff", {}).get("weights.unset") or {}):
                    weights.pop(k, None)
        if not isinstance(gen, int):
            continue
        state = {
            "vocab": sorted(n for n, e in vocab_entries.items()
                            if isinstance(e, dict) and e.get("generation", 10**9) <= gen),
            "weights": dict(weights) if r.get("stage", 0) >= 2 else {},
            "prompt": next((h["sha256"] for h in reversed(prompt_history)
                            if h.get("generation", 10**9) <= gen), None),
        }
        out[gen] = _sha(state)
    return out


def backfill(records: list[dict], fingerprints: dict[int, str]) -> tuple[list[dict], dict]:
    """纯函数:回填记录,返回 (新记录列表, 覆盖统计)。"""
    stats = {"total": 0, "policy_version_filled": 0, "state_sha256_filled": 0,
             "context_hash_null": 0, "verdicts_path_null": 0, "already_complete": 0}
    for r in records:
        stats["total"] += 1
        if r.get("policy_version") is not None and r.get("state_sha256"):
            stats["already_complete"] += 1
            continue
        if r.get("policy_version") is None:
            r["policy_version"] = STAGE_POLICY_VERSION.get(r.get("stage", 0))
            stats["policy_version_filled"] += 1
        gen = r.get("generation")
        if not r.get("state_sha256") and isinstance(gen, int) and gen in fingerprints:
            r["state_sha256"] = fingerprints[gen]
            r["state_fingerprint_source"] = "reconstructed"
            stats["state_sha256_filled"] += 1
        r.setdefault("context_hash", None)
        if r.get("context_hash") is None:
            stats["context_hash_null"] += 1
        if r.get("judge") and not r["judge"].get("verdicts_path"):
            r["judge"]["verdicts_path"] = None
            stats["verdicts_path_null"] += 1
    return records, stats


def main() -> int:
    records = [json.loads(line) for line in
               JOURNAL_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    fingerprints = _reconstruct_state_fingerprints(records)
    new_records, stats = backfill(records, fingerprints)
    JOURNAL_PATH.write_text(
        "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n"
                for r in new_records),
        encoding="utf-8")
    print(f"[backfill] {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
