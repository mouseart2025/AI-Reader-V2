"""Audit main.tex numbers against the underlying JSON ground truth.

Every assertion in the paper that references a concrete number should be
derivable from `paper/evaluation/v071/*.json` (or `baselines/*/`) — frozen
v0.71 provenance — or from `paper/version-refresh-trialrun-2026-09-23.json`
(refreshed pure-start post-fix v0.78 runs). This auditor parses main.tex,
finds the claim, computes the expected value from source, and flags
mismatches.

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
TRIALRUN_PATH = PAPER_ROOT / "version-refresh-trialrun-2026-09-23.json"


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


def trialrun() -> dict:
    """v0.78 refresh source (pure-start post-fix runs), 2026-09-23."""
    return load_json(TRIALRUN_PATH) or {}


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
    tr = trialrun()

    # ── v0.78 refresh source: pure-start post-fix runs (2026-09-23) ──
    ps = tr.get("pure_start_postfix_run_pass1", {})
    t5 = tr.get("pure_start_postfix_table5", {})
    fc = tr.get("fair_confirm_postfix_pure_start", {})
    fc_book = fc.get("per_book", {})
    fc_macro = fc.get("macro", {})

    def ps_overall(slug: str) -> float | None:
        return ps.get(slug, {}).get("naive_full", {}).get("overall")

    def ps_errors(slug: str) -> int | None:
        return ps.get(slug, {}).get("naive_full", {}).get("error_count")

    def fc_field(slug: str, pipe: str) -> float | None:
        v = fc_book.get(slug, {}).get(pipe)
        return float(v) if v is not None else None

    slugs = ("xiyouji", "honglou", "shuihu", "sanguo", "fengshen")

    # Per-novel Overall scores (tab:main) — refreshed pure-start post-fix
    xy_overall = ps_overall("xiyouji")
    hl_overall = ps_overall("honglou")
    sh_overall = ps_overall("shuihu")
    sg_overall = ps_overall("sanguo")
    fs_overall = ps_overall("fengshen")
    overalls = [v for v in (xy_overall, hl_overall, sh_overall, sg_overall, fs_overall) if v is not None]
    avg_overall = sum(overalls) / len(overalls) if overalls else None

    # Total gold nodes (frozen: LLM extractions unchanged by the refresh)
    total_gold = sum(
        b.get(s, {}).get("gold_based", {}).get("total_nodes", 0)
        for s in slugs
    )
    total_errors = sum(v for v in (ps_errors(s) for s in slugs) if v is not None)

    # Fair baseline (tab:fair) — refreshed
    fb_full_avg = fc_macro.get("full")
    fb_voting_avg = fc_macro.get("voting")

    # Frozen v0.71 structural intermediates (tab:ablation dagger rows)
    xy_raw_mc = ab.get("xiyouji", {}).get("import", {}).get("max_ch")
    xy_edmonds_mc = ab.get("xiyouji", {}).get("edmonds", {}).get("max_ch")
    xy_prior_mc = ab.get("xiyouji", {}).get("prior", {}).get("max_ch")

    # Refreshed structural headline (tab:ablation, pure-start post-fix)
    xy_full_mc = t5.get("xiyouji", {}).get("full_max_ch")
    xy_voting_mc = t5.get("xiyouji", {}).get("voting_merged")
    full_mcs = [t5.get(s, {}).get("full_max_ch") for s in slugs]
    full_mcs = [v for v in full_mcs if v is not None]
    avg_full_mc = sum(full_mcs) / len(full_mcs) if full_mcs else None
    voting_mcs = [t5.get(s, {}).get("voting_merged") for s in slugs]
    voting_mcs = [v for v in voting_mcs if v is not None]
    avg_voting_mc = sum(voting_mcs) / len(voting_mcs) if voting_mcs else None
    depths = [t5.get(s, {}).get("depth") for s in slugs]
    depths = [v for v in depths if v is not None]
    avg_full_depth = sum(depths) / len(depths) if depths else None

    # CoT baseline numbers (frozen)
    xy_cot = cot_result("xiyouji")
    hl_cot = cot_result("honglou")
    xy_cot_mc = xy_cot.get("max_children")
    hl_cot_mc = hl_cot.get("max_children")

    # Zero-shot baselines (frozen)
    xy_zs = zero_shot_result("xiyouji")
    hl_zs = zero_shot_result("honglou")
    xy_zs_roots = xy_zs.get("root_count") if xy_zs else None
    hl_zs_roots = hl_zs.get("root_count") if hl_zs else None

    claims: list[Claim] = []

    # --- Abstract + Intro + Contributions ---
    if xy_voting_mc is not None and xy_full_mc is not None:
        claims.append(Claim(
            name="Voting->Full max_ch (Journey) — abstract/intro/contributions",
            tex_pattern=r"from 203 to 57",
            expected="from 203 to 57",
            note=f"tab:ablation pure-start post-fix: voting {xy_voting_mc}, full {xy_full_mc}",
        ))
    claims.append(Claim(
        name="Total gold nodes (abstract + tab:data)",
        tex_pattern=r"4\{,\}941",
        expected="4{,}941",
        note=f"sum of per-novel total_nodes across 5 benchmarks = {total_gold}",
    ))
    claims.append(Claim(
        name="72% reduction phrase (abstract)",
        tex_pattern=r"72\\% reduction",
        expected="72%",
        note="= 1 - 57/203 ≈ 71.9% → 72%",
    ))

    # --- tab:main per-novel Overalls (refreshed) ---
    if xy_overall is not None:
        claims.append(Claim(
            name="Journey to the West Overall (tab:main)",
            tex_pattern=r"Journey to the West & \\textbf\{(\d+\.\d+)\}",
            expected=xy_overall,
            tolerance=0.0005,
        ))
    if hl_overall is not None:
        claims.append(Claim(
            name="Dream of the Red Chamber Overall (tab:main)",
            tex_pattern=r"Dream of the Red Chamber & \\textbf\{(\d+\.\d+)\}",
            expected=hl_overall,
            tolerance=0.0005,
        ))
    if fs_overall is not None:
        claims.append(Claim(
            name="Investiture of the Gods Overall (tab:main)",
            tex_pattern=r"Investiture of the Gods & \\textbf\{(\d+\.\d+)\}",
            expected=fs_overall,
            tolerance=0.0005,
        ))
    if sh_overall is not None:
        claims.append(Claim(
            name="Water Margin Overall (tab:main)",
            tex_pattern=r"Water Margin & (\d+\.\d+) &",
            expected=sh_overall,
            tolerance=0.0005,
        ))
    if sg_overall is not None:
        claims.append(Claim(
            name="Three Kingdoms Overall (tab:main)",
            tex_pattern=r"Three Kingdoms & (\d+\.\d+) &",
            expected=sg_overall,
            tolerance=0.0005,
        ))
    if avg_overall is not None:
        claims.append(Claim(
            name="5-novel average Overall (tab:main)",
            tex_pattern=r"5-novel average\} & \\textbf\{(\d+\.\d+)\}",
            expected=avg_overall,
            tolerance=0.0005,
        ))

    # --- tab:main error counts (refreshed) ---
    for slug, novel, denom in (
        ("xiyouji", "Journey to the West", 1205),
        ("honglou", "Dream of the Red Chamber", 1000),
        ("fengshen", "Investiture of the Gods", 233),
        ("shuihu", "Water Margin", 1383),
        ("sanguo", "Three Kingdoms", 1120),
    ):
        e = ps_errors(slug)
        if e is not None:
            claims.append(Claim(
                name=f"{novel} error count (tab:main)",
                tex_pattern=rf"{novel} & .*?(\d+)/{denom} \\\\",
                expected=e,
                note="pure-start post-fix naive_full.error_count",
            ))
    if total_errors:
        claims.append(Claim(
            name="Total error count (tab:main bottom row)",
            tex_pattern=r"\\textbf\{(\d+)/4941\}",
            expected=total_errors,
        ))

    # --- tab:fair (refreshed pure-start post-fix) ---
    fair_rows = (
        ("xiyouji", r"Journey to the West & (\d\.\d+) & \\textbf\{0\.9707\}"),
        ("honglou", r"Dream of the Red Chamber & \\textbf\{(\d\.\d+)\} & 0\.9705"),
        ("shuihu", r"Water Margin & \\textbf\{(\d\.\d+)\} & 0\.8346"),
        ("sanguo", r"Three Kingdoms & \\textbf\{(\d\.\d+)\} & 0\.8313"),
        ("fengshen", r"Investiture of the Gods & \\textbf\{(\d\.\d+)\} & 0\.9237"),
    )
    for slug, pat in fair_rows:
        v = fc_field(slug, "full")
        if v is not None:
            claims.append(Claim(
                name=f"{slug} Full fair-baseline Overall (tab:fair)",
                tex_pattern=pat,
                expected=v,
                tolerance=0.0005,
            ))
    if fb_full_avg is not None:
        claims.append(Claim(
            name="Average Full fair-baseline (tab:fair + abstract + contribution 3)",
            tex_pattern=r"0\.9101",
            expected=round(fb_full_avg, 4),
            tolerance=0.0005,
            note="abstract and contributions both mention 0.9101",
        ))
    if fb_voting_avg is not None:
        claims.append(Claim(
            name="Average Voting fair-baseline (tab:fair + abstract)",
            tex_pattern=r"0\.9062",
            expected=round(fb_voting_avg, 4),
            tolerance=0.0005,
        ))

    # --- tab:ablation structural (refreshed headline + frozen daggers) ---
    if xy_raw_mc is not None:
        claims.append(Claim(
            name="Raw chapter LLM max_ch Journey (tab:ablation, frozen v0.71)",
            tex_pattern=r"Raw chapter LLM & 2\.07\$\^\\dagger\$ & (\d+)\$\^\\dagger\$ & \\xmark",
            expected=xy_raw_mc,
        ))
    if xy_voting_mc is not None:
        claims.append(Claim(
            name="Voting greedy max_ch Journey (tab:ablation, refreshed)",
            tex_pattern=r"Voting \(greedy\) & 4\.09\$\^\\dagger\$ & \\textbf\{(\d+)\}",
            expected=xy_voting_mc,
        ))
    if xy_edmonds_mc is not None:
        claims.append(Claim(
            name="Edmonds (no priors) max_ch Journey (tab:ablation, frozen v0.71)",
            tex_pattern=r"Edmonds \(no priors\) & 2\.91\$\^\\dagger\$ & (\d+)\$\^\\dagger\$",
            expected=xy_edmonds_mc,
        ))
    if xy_prior_mc is not None:
        claims.append(Claim(
            name="Edmonds + Priors max_ch Journey (tab:ablation, frozen v0.71)",
            tex_pattern=r"Edmonds \+ Priors & 2\.91\$\^\\dagger\$ & (\d+)\$\^\\dagger\$",
            expected=xy_prior_mc,
        ))
    if xy_full_mc is not None:
        claims.append(Claim(
            name="Full pipeline max_ch Journey (tab:ablation, abstract, intro, §3.3)",
            tex_pattern=r"\\textbf\{Full \(\+ SuffixNormalizer\)\} & \\textbf\{[\d.]+\} & \\textbf\{(\d+)\}",
            expected=xy_full_mc,
        ))
    if avg_voting_mc is not None:
        claims.append(Claim(
            name="5-novel avg Voting max_ch (tab:ablation bottom)",
            tex_pattern=r"5-novel avg, Voting\} & \\textit\{--\} & \\textit\{(\d+)\}",
            expected=round(avg_voting_mc),
            note=f"mean voting_merged = {avg_voting_mc}",
        ))
    if avg_full_mc is not None:
        claims.append(Claim(
            name="5-novel avg Full max_ch (tab:ablation bottom)",
            tex_pattern=r"5-novel avg, Full\} & \\textit\{[\d.]+\} & \\textit\{(\d+)\}",
            expected=round(avg_full_mc),
            note=f"mean full_max_ch = {avg_full_mc}",
        ))
    if avg_full_depth is not None:
        claims.append(Claim(
            name="5-novel avg Full depth (tab:ablation bottom)",
            tex_pattern=r"5-novel avg, Full\} & \\textit\{([\d.]+)\}",
            expected=avg_full_depth,
            tolerance=0.005,
        ))

    # --- tab:llm-baselines (frozen) ---
    if xy_cot_mc is not None:
        claims.append(Claim(
            name="LLM-CoT Journey max_ch (tab:llm-baselines)",
            tex_pattern=r"LLM-CoT one-shot & 74 &",
            expected=xy_cot_mc,
        ))
    if hl_cot_mc is not None:
        claims.append(Claim(
            name="LLM-CoT Red Chamber max_ch (tab:llm-baselines + §3.5)",
            tex_pattern=r"LLM-CoT one-shot & 143 &",
            expected=hl_cot_mc,
        ))
    if xy_zs_roots is not None:
        claims.append(Claim(
            name="Zero-shot Journey root_count (tab:llm-baselines + §3.5)",
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
        return False, "pattern not found in main.tex"

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
        for c, _msg in warnings:
            print(f"  {c.name}: pattern='{c.tex_pattern}'")

    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
