"""地点层级质量benchmark - Phase 0 CLI.

用法:
    # 跑西游记gold-based指标 (对比当前WS vs errata标注)
    uv run python -m backend.scripts.benchmark_hierarchy --novel=xiyouji

    # 跑任意小说的规则自动校验 (无gold, 仅规则引擎)
    uv run python -m backend.scripts.benchmark_hierarchy --novel-id=<uuid> --rules-only

    # 输出JSON到文件
    uv run python -m backend.scripts.benchmark_hierarchy --novel=xiyouji --out=report.json

    # 保存历史记录到 backend/data/hierarchy_validation/history/
    uv run python -m backend.scripts.benchmark_hierarchy --novel=xiyouji --save-history

Exit codes:
    0: benchmark 成功
    1: 未找到gold或novel
    2: 指标回归 (overall下降 >0.01 vs last history) — CI可用
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# Path setup: script directly runnable without package install
ROOT = Path(__file__).resolve().parents[2]  # project root
sys.path.insert(0, str(ROOT / "backend"))

from src.services.hierarchy_validator import (  # noqa: E402
    KnowledgeBase,
    RuleValidator,
    TextVerifier,
    compute_metrics_from_gold,
    compute_metrics_from_verdicts,
    load_gold,
)

NOVEL_ID_MAP = {
    "xiyouji": "3b2ef56c-1a55-466a-a7d1-34272446a198",
    "honglou": "c384901a-8b71-437a-af35-b5ec1c56c696",
    "shuihu": "4ac43c73-f67b-427c-8d6d-e766a1423977",
    "sanguo": "b1287ef6-c215-4bd2-842c-cb04aec5eb70",
    "fengshen": "53013970-effd-4f50-aef7-728ca13de69a",
}


def load_snapshot_from_db(
    novel_id: str, db_path: Path
) -> tuple[dict[str, str], dict[str, str], Counter, str, str]:
    """从SQLite加载当前world_structure + 章节提及次数.

    Returns:
        (location_parents, location_tiers, mentions, title, genre)
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT title FROM novels WHERE id=?", (novel_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Novel {novel_id} not found in {db_path}")
    title = row["title"]

    cur.execute(
        "SELECT structure_json FROM world_structures WHERE novel_id=?", (novel_id,)
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"No world_structure for novel {novel_id}")
    ws = json.loads(row["structure_json"])
    location_parents = ws.get("location_parents", {})
    location_tiers = ws.get("location_tiers", {})
    genre = ws.get("novel_genre_hint", "")

    # chapter facts for mention counts
    cur.execute(
        "SELECT fact_json FROM chapter_facts WHERE novel_id=?", (novel_id,)
    )
    mentions: Counter = Counter()
    for r in cur.fetchall():
        fj = json.loads(r["fact_json"])
        for loc in fj.get("locations") or []:
            if loc.get("name"):
                mentions[loc["name"]] += 1

    conn.close()
    return location_parents, location_tiers, mentions, title, genre


def default_db_path() -> Path:
    home = Path.home()
    return home / ".ai-reader-v2" / "data.db"


def cmd_benchmark(args):
    db = Path(args.db) if args.db else default_db_path()
    if not db.exists():
        print(f"[error] DB not found: {db}", file=sys.stderr)
        return 1

    # Resolve novel_id
    if args.novel_id:
        novel_id = args.novel_id
        novel_key = args.novel or args.novel_id[:8]
    elif args.novel:
        novel_id = NOVEL_ID_MAP.get(args.novel)
        if not novel_id:
            print(f"[error] Unknown novel key: {args.novel}", file=sys.stderr)
            return 1
        novel_key = args.novel
    else:
        print("[error] Must provide --novel or --novel-id", file=sys.stderr)
        return 1

    print(f"[info] Loading snapshot: novel={novel_key}, id={novel_id[:8]}...")
    try:
        lp, lt, mentions, title, genre = load_snapshot_from_db(novel_id, db)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1
    # current_nodes = nodes actually in world_structure (authoritative state)
    # Ignore stale chapter_facts mentions of deleted/renamed names
    current_nodes = (set(lp.keys()) | set(lp.values()) | set(lt.keys())) - {"", None}
    print(f"[info] Loaded: {len(lp)} parent edges, {len(current_nodes)} nodes, {sum(mentions.values())} mentions")

    # Primary path: gold-based metrics
    gold_based_metrics = None
    resolved_count = 0
    if not args.rules_only:
        try:
            gold, gold_raw = load_gold(novel_key)
            print(f"[info] Gold loaded: {len(gold)} annotated nodes")
            gold_based_metrics, resolved_count = compute_metrics_from_gold(
                novel_key, current_nodes, gold,
                current_tiers=lt, current_parents=lp, gold_raw=gold_raw,
            )
            print("\n" + "=" * 60)
            print("GOLD-BASED METRICS (self-verifying vs errata)")
            print("=" * 60)
            print(gold_based_metrics.format_report())
            print(f"\n[resolved] {resolved_count} gold errors have been addressed")
        except FileNotFoundError:
            print(f"[warn] No gold annotation for {novel_key}, falling back to rules-only", file=sys.stderr)
            args.rules_only = True

    # Secondary path: rule-based (with optional Layer 3 text verification)
    from pathlib import Path as P
    kb = KnowledgeBase.load()
    text_verifier = None
    try:
        text_verifier = TextVerifier(novel_id, db)
        print(f"[info] Text verifier loaded: {text_verifier.text_length:,} chars")
    except Exception:
        pass
    rv = RuleValidator(kb, text_verifier=text_verifier, novel_genre_hint=genre)
    rule_verdicts = rv.validate_snapshot(lp, lt, dict(mentions))
    rule_metrics = compute_metrics_from_verdicts(novel_key, rule_verdicts)

    print("\n" + "=" * 60)
    print("RULE-BASED AUTO-VALIDATION (generic rules, no gold)")
    print("=" * 60)
    print(rule_metrics.format_report())

    # Gold vs Rules: coverage分析 (规则能捕捉多少gold标注的错误?)
    if gold_based_metrics is not None:
        gold, _ = load_gold(novel_key)
        gold_errors = {n for n, v in gold.items() if v.verdict == "error" and n in current_nodes}
        rule_errors = {n for n, v in rule_verdicts.items() if v.verdict == "error"}
        tp = gold_errors & rule_errors
        fn = gold_errors - rule_errors  # gold says error but rules miss
        fp = rule_errors - gold_errors  # rules flag but gold says correct/suspect
        print("\n" + "=" * 60)
        print("RULE ENGINE EVALUATION (vs gold)")
        print("=" * 60)
        if gold_errors:
            recall = len(tp) / len(gold_errors)
        else:
            recall = 0.0
        precision_r = len(tp) / len(rule_errors) if rule_errors else 0.0
        print(f"Gold errors in current snapshot: {len(gold_errors)}")
        print(f"Rule-flagged errors:             {len(rule_errors)}")
        print(f"True positives (caught):         {len(tp)}  → recall={recall:.2%}")
        print(f"False negatives (missed):        {len(fn)}")
        print(f"False positives (over-flagged):  {len(fp)}  → precision={precision_r:.2%}")
        if args.verbose and fn:
            print("\n[FN: errors missed by rule engine, sample 10]")
            for name in list(fn)[:10]:
                print(f"  - {name}: {gold[name].error_types}")
        if args.verbose and fp:
            print("\n[FP: over-flagged by rules, sample 10]")
            for name in list(fp)[:10]:
                print(f"  - {name}: {rule_verdicts[name].error_types}")

    # Output
    primary = gold_based_metrics or rule_metrics
    if args.out:
        out_path = Path(args.out)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "novel": novel_key,
                    "novel_id": novel_id,
                    "title": title,
                    "timestamp": datetime.now().isoformat(),
                    "gold_based": gold_based_metrics.to_dict() if gold_based_metrics else None,
                    "rule_based": rule_metrics.to_dict(),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"\n[info] Report saved: {out_path}")

    if args.save_history:
        hist_dir = ROOT / "backend" / "data" / "hierarchy_validation" / "history"
        hist_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        hist_path = hist_dir / f"{novel_key}-{ts}.json"
        with hist_path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "novel": novel_key,
                    "timestamp": datetime.now().isoformat(),
                    "gold_based": gold_based_metrics.to_dict() if gold_based_metrics else None,
                    "rule_based": rule_metrics.to_dict(),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"[info] History saved: {hist_path}")

        # Regression check
        all_hist = sorted(hist_dir.glob(f"{novel_key}-*.json"))
        if len(all_hist) >= 2:
            with all_hist[-2].open(encoding="utf-8") as f:
                prev = json.load(f)
            prev_overall = (prev.get("gold_based") or prev.get("rule_based"))["overall"]
            curr_overall = primary.overall
            delta = curr_overall - prev_overall
            print(f"\n[regression check] prev={prev_overall:.4f} curr={curr_overall:.4f} Δ={delta:+.4f}")
            if delta < -0.01:
                print(f"[WARN] regression detected: overall dropped {abs(delta):.4f}", file=sys.stderr)
                return 2

    return 0


def main():
    ap = argparse.ArgumentParser(description="地点层级质量benchmark")
    ap.add_argument("--novel", help="novel key (e.g. xiyouji)")
    ap.add_argument("--novel-id", help="novel uuid (overrides --novel)")
    ap.add_argument("--db", help="SQLite DB path (default: ~/.ai-reader-v2/data.db)")
    ap.add_argument("--rules-only", action="store_true", help="skip gold, rules only")
    ap.add_argument("--out", help="save JSON report to file")
    ap.add_argument("--save-history", action="store_true", help="save to history/ + regression check")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    sys.exit(cmd_benchmark(args))


if __name__ == "__main__":
    main()
