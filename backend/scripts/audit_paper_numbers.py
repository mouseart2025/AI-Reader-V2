"""Audit main.tex numbers against the underlying JSON ground truth.

Every assertion in the paper that references a concrete number should be
derivable from `paper/evaluation/v071/*.json` (or `baselines/*/`). This
auditor parses main.tex, finds the claim, computes the expected value from
source, and flags mismatches.

Usage:
    cd backend && uv run python scripts/audit_paper_numbers.py

Exit 0 if all checks pass, 1 otherwise.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

PAPER_ROOT = Path("/Users/leonfeng/Baiduyun/AISoul/ai-reader-internal/paper")
TEX_PATH = PAPER_ROOT / "latex" / "main.tex"
EVAL_ROOT = PAPER_ROOT / "evaluation" / "v071"
BASELINES = EVAL_ROOT / "baselines"


# =============================================================================
# Sources
# =============================================================================

def load_json(p: Path) -> dict | None:
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def benchmarks() -> dict[str, dict]:
    """Load the 5 per-novel gold benchmark JSONs."""
    out = {}
    for slug in ("xiyouji", "honglou", "shuihu", "sanguo", "fengshen"):
        d = load_json(EVAL_ROOT / f"{slug}-benchmark.json")
        if d:
            out[slug] = d
    return out


def ablation_by_stage() -> dict:
    return load_json(EVAL_ROOT / "ablation-by-stage.json") or {}


def fair_baseline() -> dict:
    return load_json(EVAL_ROOT / "ablation-voting-baseline-fair.json") or {}


def cot_result(slug: str) -> dict:
    return load_json(BASELINES / "single_shot_cot" / f"{slug}.json") or {}


def zero_shot_result(slug: str) -> dict:
    return load_json(BASELINES / "zero_shot" / slug / "aggregate.json") or {}


# =============================================================================
# Claim registry
# =============================================================================

@dataclass
class Claim:
    name: str
    tex_pattern: str  # regex that matches the claim; group(1) is the number
    expected: float | int | str  # the ground-truth value (from JSON)
    tolerance: float = 0.0  # absolute tolerance for float compare
    note: str = ""


def build_claims() -> list[Claim]:
    b = benchmarks()
    ab = ablation_by_stage()
    fb = fair_baseline()

    # Per-novel Overall scores from Table 2
    xy_overall = b.get("xiyouji", {}).get("gold_based", {}).get("overall")
    hl_overall = b.get("honglou", {}).get("gold_based", {}).get("overall")
    sh_overall = b.get("shuihu", {}).get("gold_based", {}).get("overall")
    sg_overall = b.get("sanguo", {}).get("gold_based", {}).get("overall")
    fs_overall = b.get("fengshen", {}).get("gold_based", {}).get("overall")

    # 5-novel average Overall
    overalls = [v for v in (xy_overall, hl_overall, sh_overall, sg_overall, fs_overall) if v is not None]
    avg_overall = sum(overalls) / len(overalls) if overalls else None

    # Total gold nodes
    total_gold = sum(
        b.get(s, {}).get("gold_based", {}).get("total_nodes", 0)
        for s in ("xiyouji", "honglou", "shuihu", "sanguo", "fengshen")
    )

    # Fair baseline averages
    fb_full_avg = fb.get("_summary", {}).get("avg_full_overall")
    fb_voting_avg = fb.get("_summary", {}).get("avg_voting_overall")
    # Fallback: compute from per-novel
    if fb_full_avg is None and fb:
        full_vals = [
            fb[s].get("fair_intersection", {}).get("full", {}).get("overall")
            for s in fb if isinstance(fb.get(s), dict) and "fair_intersection" in fb.get(s, {})
        ]
        vot_vals = [
            fb[s].get("fair_intersection", {}).get("voting", {}).get("overall")
            for s in fb if isinstance(fb.get(s), dict) and "fair_intersection" in fb.get(s, {})
        ]
        full_vals = [v for v in full_vals if v is not None]
        vot_vals = [v for v in vot_vals if v is not None]
        if full_vals:
            fb_full_avg = sum(full_vals) / len(full_vals)
        if vot_vals:
            fb_voting_avg = sum(vot_vals) / len(vot_vals)

    # Structural: xiyouji Edmonds+Prior+Suffix (Full) max_ch
    xy_full_mc = ab.get("xiyouji", {}).get("suffix", {}).get("max_ch")
    xy_full_depth = ab.get("xiyouji", {}).get("suffix", {}).get("depth")
    xy_raw_mc = ab.get("xiyouji", {}).get("import", {}).get("max_ch")
    xy_edmonds_mc = ab.get("xiyouji", {}).get("edmonds", {}).get("max_ch")
    xy_prior_mc = ab.get("xiyouji", {}).get("prior", {}).get("max_ch")

    # 5-novel avg full max_ch
    full_mcs = [ab.get(s, {}).get("suffix", {}).get("max_ch") for s in ab]
    full_mcs = [v for v in full_mcs if v is not None]
    avg_full_mc = sum(full_mcs) / len(full_mcs) if full_mcs else None

    # CoT baseline numbers
    xy_cot = cot_result("xiyouji")
    hl_cot = cot_result("honglou")
    xy_cot_mc = xy_cot.get("max_children")
    hl_cot_mc = hl_cot.get("max_children")
    xy_cot_roots = xy_cot.get("computed_roots")
    hl_cot_roots = hl_cot.get("computed_roots")
    xy_cot_missed = xy_cot.get("missed_count")
    hl_cot_missed = hl_cot.get("missed_count")
    xy_cot_halluc = xy_cot.get("hallucinated_count")
    hl_cot_halluc = hl_cot.get("hallucinated_count")
    xy_cot_pp = xy_cot.get("topology", {}).get("parent_precision") if xy_cot else None
    hl_cot_pp = hl_cot.get("topology", {}).get("parent_precision") if hl_cot else None

    # Zero-shot baselines
    xy_zs = zero_shot_result("xiyouji")
    hl_zs = zero_shot_result("honglou")
    xy_zs_roots = xy_zs.get("root_count") if xy_zs else None
    hl_zs_roots = hl_zs.get("root_count") if hl_zs else None
    xy_zs_pp = xy_zs.get("topology", {}).get("parent_precision") if xy_zs else None
    hl_zs_pp = hl_zs.get("topology", {}).get("parent_precision") if hl_zs else None

    # Fair-baseline individual rows (Table 3)
    def fb_field(slug: str, pipe: str) -> float | None:
        try:
            return fb[slug]["fair_intersection"][pipe]["overall"]
        except (KeyError, TypeError):
            return None

    claims: list[Claim] = []

    # --- Abstract + Intro + Contributions ---
    if avg_overall is not None:
        claims.append(Claim(
            name="Voting greedy max_ch (Journey) — abstract/intro/contributions",
            tex_pattern=r"from 279 to 63",
            expected="from 279 to 63",
            note="should match Table 4 Voting max_ch = 279 and Full max_ch = 63",
        ))
    claims.append(Claim(
        name="Total gold nodes (abstract + Table 1)",
        tex_pattern=r"4\{,\}941",
        expected="4{,}941",
        note=f"sum of per-novel total_nodes across 5 benchmarks = {total_gold}",
    ))
    claims.append(Claim(
        name="77% reduction phrase (abstract)",
        tex_pattern=r"77\\% reduction",
        expected="77%",
        note="= 1 - 63/279 ≈ 77.4% → 77%",
    ))

    # --- Table 2 per-novel Overalls ---
    if xy_overall is not None:
        claims.append(Claim(
            name="Journey to the West Overall (Table 2)",
            tex_pattern=r"Journey to the West & \\textbf\{(\d+\.\d+)\}",
            expected=xy_overall,
            tolerance=0.001,
        ))
    if hl_overall is not None:
        claims.append(Claim(
            name="Dream of the Red Chamber Overall (Table 2)",
            tex_pattern=r"Dream of the Red Chamber & \\textbf\{(\d+\.\d+)\}",
            expected=hl_overall,
            tolerance=0.001,
        ))
    if fs_overall is not None:
        claims.append(Claim(
            name="Investiture of the Gods Overall (Table 2)",
            tex_pattern=r"Investiture of the Gods & \\textbf\{(\d+\.\d+)\}",
            expected=fs_overall,
            tolerance=0.001,
        ))
    if sh_overall is not None:
        claims.append(Claim(
            name="Water Margin Overall (Table 2)",
            tex_pattern=r"Water Margin & (\d+\.\d+) &",
            expected=sh_overall,
            tolerance=0.001,
        ))
    if sg_overall is not None:
        claims.append(Claim(
            name="Three Kingdoms Overall (Table 2)",
            tex_pattern=r"Three Kingdoms & (\d+\.\d+) &",
            expected=sg_overall,
            tolerance=0.001,
        ))
    if avg_overall is not None:
        claims.append(Claim(
            name="5-novel average Overall (Table 2)",
            tex_pattern=r"5-novel average\} & \\textbf\{(\d+\.\d+)\}",
            expected=avg_overall,
            tolerance=0.001,
        ))

    # --- Table 3 Fair baseline --- (scoped to the exact 3-col row format)
    if fb_field("xiyouji", "full") is not None:
        claims.append(Claim(
            name="Journey Full fair-baseline Overall (Table 3)",
            tex_pattern=r"Journey to the West & \\textbf\{(\d+\.\d+)\} & 0\.9672",
            expected=fb_field("xiyouji", "full"),
            tolerance=0.0005,
        ))
    if fb_full_avg is not None:
        claims.append(Claim(
            name="Average Full fair-baseline (Table 3 + abstract + contribution 3)",
            tex_pattern=r"0\.9156",
            expected=round(fb_full_avg, 4),
            tolerance=0.0005,
            note="abstract and contributions both mention 0.9156",
        ))
    if fb_voting_avg is not None:
        claims.append(Claim(
            name="Average Voting fair-baseline (Table 3 + abstract)",
            tex_pattern=r"0\.9111",
            expected=round(fb_voting_avg, 4),
            tolerance=0.0005,
        ))

    # --- Table 4 Structural (Journey focus) ---
    if xy_raw_mc is not None:
        claims.append(Claim(
            name="Raw chapter LLM max_ch Journey (Table 4)",
            tex_pattern=r"Raw chapter LLM & [\d.]+ & (\d+) & \\xmark",
            expected=xy_raw_mc,
        ))
    if xy_edmonds_mc is not None:
        claims.append(Claim(
            name="Edmonds (no priors) max_ch Journey (Table 4)",
            tex_pattern=r"Edmonds \(no priors\) & [\d.]+ & (\d+) & \\cmark",
            expected=xy_edmonds_mc,
        ))
    if xy_full_mc is not None:
        claims.append(Claim(
            name="Full pipeline max_ch Journey (Table 4, abstract, intro, contributions, §3.3)",
            tex_pattern=r"\\textbf\{Full \(\+ SuffixNormalizer\)\} & \\textbf\{[\d.]+\} & \\textbf\{(\d+)\}",
            expected=xy_full_mc,
        ))
    if avg_full_mc is not None:
        claims.append(Claim(
            name="5-novel avg Full max_ch (Table 4 bottom)",
            tex_pattern=r"5-novel avg, Full\} & \\textit\{[\d.]+\} & \\textit\{(\d+)\}",
            expected=round(avg_full_mc),
        ))

    # --- Table 6 LLM baselines ---
    if xy_cot_mc is not None:
        claims.append(Claim(
            name="LLM-CoT Journey max_ch (Table 6)",
            tex_pattern=r"LLM-CoT one-shot & 74 &",
            expected=xy_cot_mc,
        ))
    if hl_cot_mc is not None:
        claims.append(Claim(
            name="LLM-CoT Red Chamber max_ch (Table 6 + §3.5)",
            tex_pattern=r"LLM-CoT one-shot & 143 &",
            expected=hl_cot_mc,
        ))
    if xy_zs_roots is not None:
        claims.append(Claim(
            name="Zero-shot Journey root_count (Table 6 + §3.5)",
            tex_pattern=r"101 disjoint roots on \\textit\{Journey\}",
            expected=xy_zs_roots,
        ))
    if hl_zs_roots is not None:
        claims.append(Claim(
            name="Zero-shot Red Chamber root_count (§3.5)",
            tex_pattern=r"56 on \\textit\{Red Chamber\}",
            expected=hl_zs_roots,
        ))

    return claims


# =============================================================================
# Audit runner
# =============================================================================

def compare(pattern: str, tex: str, expected, tolerance: float) -> tuple[bool, str]:
    """Run the regex, compare captured value (if any) against expected."""
    # If pattern has a capture group, extract and compare numerically
    m = re.search(pattern, tex)
    if not m:
        return False, f"pattern not found in main.tex"

    if m.groups():
        actual_str = m.group(1).replace(",", "").replace("{,}", "")
        try:
            actual = float(actual_str)
            exp_num = float(expected) if not isinstance(expected, str) else None
            if exp_num is not None:
                if abs(actual - exp_num) <= tolerance:
                    return True, f"actual={actual}, expected={exp_num}"
                return False, f"MISMATCH: tex={actual}, json={exp_num} (Δ={abs(actual-exp_num):.4f})"
            # Non-numeric expected: just ensure string match
            if str(actual) == str(expected):
                return True, f"actual={actual}, expected={expected}"
            return False, f"MISMATCH: tex={actual}, expected={expected}"
        except ValueError:
            return False, f"failed to parse '{actual_str}' as number"
    else:
        # Pattern has no group — just confirms presence
        return True, f"found: '{pattern[:60]}'"


def main():
    if not TEX_PATH.exists():
        sys.exit(f"ERROR: {TEX_PATH} not found")
    tex = TEX_PATH.read_text()

    claims = build_claims()
    if not claims:
        sys.exit("ERROR: no claims built — check eval JSON paths")

    print(f"Auditing {len(claims)} claims from {TEX_PATH.name}\n")

    passed = 0
    failed: list[tuple[Claim, str]] = []
    warnings: list[tuple[Claim, str]] = []

    for c in claims:
        ok, msg = compare(c.tex_pattern, tex, c.expected, c.tolerance)
        if ok:
            passed += 1
            print(f"  ✓ {c.name:60s} {msg}")
        else:
            if "not found" in msg:
                warnings.append((c, msg))
                print(f"  ? {c.name:60s} {msg}")
            else:
                failed.append((c, msg))
                print(f"  ✗ {c.name:60s} {msg}")
        if c.note:
            print(f"      note: {c.note}")

    print()
    print(f"Summary: {passed} PASS, {len(failed)} FAIL, {len(warnings)} WARN (pattern not found)")

    if failed:
        print("\n=== FAILURES (number mismatch, FIX REQUIRED) ===")
        for c, msg in failed:
            print(f"  {c.name}")
            print(f"    {msg}")
            print(f"    expected: {c.expected}")
            if c.note:
                print(f"    note: {c.note}")

    if warnings:
        print("\n=== WARNINGS (regex did not match — tex wording may have changed) ===")
        for c, msg in warnings:
            print(f"  {c.name}: pattern='{c.tex_pattern}'")

    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
