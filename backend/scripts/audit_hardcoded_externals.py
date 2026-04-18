"""Scan backend + frontend for hardcoded external-world assumptions.

Catches the class of bug that surfaced 2026-04-18:
- LLM model names go stale every 3-6 months (e.g. Claude 4-5 → 4-7)
- Real place names smuggled into prompt examples or rule tables
- API endpoints written as literals instead of config
- Hardcoded dates / year literals

This scanner does NOT auto-fix; it categorizes each hit by severity:

  HIGH   — likely to break when external world changes
           (e.g. model names in CLOUD_PROVIDERS, current-year literals)
  MED    — prompt/test content referencing real world (may be intentional)
  LOW    — comments / docstrings / migrations (informational)

Usage:
    cd backend && uv run python scripts/audit_hardcoded_externals.py
    cd backend && uv run python scripts/audit_hardcoded_externals.py --severity HIGH  # filter
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path("/Users/leonfeng/Baiduyun/AISoul/AI-Reader-V2")
BACKEND_SRC = REPO_ROOT / "backend" / "src"
BACKEND_SCRIPTS = REPO_ROOT / "backend" / "scripts"
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"

SKIP_DIRS = {".venv", "node_modules", "__pycache__", "dist", "build", ".git"}
SKIP_FILES = {"package-lock.json", "uv.lock"}


# =============================================================================
# Patterns (regex, label, severity)
# =============================================================================

PATTERNS: list[tuple[str, str, str]] = [
    # --- LLM model names ---
    (r"\bclaude-(?:opus|sonnet|haiku|3)-[\w.-]+", "claude-model-name", "HIGH"),
    (r"\bgpt-[45](?:\.\d+)?(?:-[\w-]+)?\b", "gpt-model-name", "HIGH"),
    (r"\bdeepseek-(?:chat|reasoner|coder|v\d+)[\w.-]*\b", "deepseek-model-name", "HIGH"),
    (r"\bgemini-[12]\.\d+[\w-]*\b", "gemini-model-name", "HIGH"),
    # qwen cloud models use dashes (qwen-max, qwen3-235b-a22b);
    # exclude Ollama-style qwen3:8b (local, user-configurable, not stale-prone)
    (r"\bqwen\d*-[\w-]+(?![:\w])", "qwen-model-name", "HIGH"),
    (r"\bMiniMax-[\w.-]+\b", "minimax-model-name", "HIGH"),
    (r"\bglm-\d+(?:\.\d+)?(?:-[\w-]+)?\b", "glm-model-name", "HIGH"),
    (r"\bkimi-(?:latest|k\d|[\w-]+)\b", "kimi-model-name", "HIGH"),
    # --- API URLs ---
    (r"https?://api\.(?:anthropic|openai|deepseek|minimax|moonshot)\.(?:com|cn)[\w/.-]*", "llm-api-url", "HIGH"),
    (r"https?://(?:dashscope\.aliyuncs|generativelanguage\.googleapis|api\.siliconflow|open\.bigmodel|api\.lingyiwanwu)\.[\w/.-]+", "llm-api-url", "HIGH"),
    # --- Real Chinese place names (famous, in LLM training) ---
    (r"(?<![\u4e00-\u9fff])(?:长安|洛阳|南京|北京|上海|苏州|杭州|广州|西安|泰山|峨眉|终南山|五台山|九华山|华山|武当山|普陀山|崆峒山|少林寺|武当|青城)(?![\u4e00-\u9fff])", "real-place-name", "MED"),
    # --- Real dynasty names (in rules/prompts) ---
    (r"(?<![\u4e00-\u9fff])(?:唐朝|宋朝|明朝|清朝|秦朝|汉朝|周朝|商朝|元朝|大唐|大宋|大明|大清)(?![\u4e00-\u9fff])", "real-dynasty", "MED"),
    # --- Famous fictional character/place from known novels ---
    (r"(?<![\u4e00-\u9fff])(?:孙悟空|猪八戒|沙和尚|唐三藏|贾宝玉|林黛玉|王熙凤|诸葛亮|曹操|令狐冲|郭靖|黄蓉|张三丰)(?![\u4e00-\u9fff])", "famous-novel-entity", "MED"),
    # --- Hardcoded current-year literals ---
    (r"\b(?:2023|2024|2025|2026)-\d{2}-\d{2}\b", "hardcoded-date", "LOW"),
    (r'["\'](?:20[23][3-6])["\']', "hardcoded-year", "LOW"),
    # --- Version strings ---
    (r"\bv0\.\d+\.\d+\b", "version-string", "LOW"),
    # --- Chapter count / word count assumptions ---
    (r"(?<!\d)100\s*回", "chapter-count-literal", "LOW"),
    (r"(?<!\d)120\s*回", "chapter-count-literal", "LOW"),
]

# Files/dirs where the patterns are expected (don't flag as HIGH)
EXPECTED_IN = {
    "prompts": [
        "extraction_system.txt",
        "extraction_examples.json",
        "contamination",
        "knowledge_prior",
        "person_knowledge_prior",
        "blocklist",
        "alias_resolver",  # intentional blocklist strings
        "name_authority",
    ],
    "fixtures": ["fixtures/", "golden_standard_"],
    "audits": ["audit_", "_baseline.py", "synthesize_novel", "patch_phase1"],
    "tests": ["/tests/"],
    "migrations": ["migration", "/db/"],
}


def classify(file_path: Path, label: str, line_text: str = "") -> str:
    """Downgrade severity based on file context."""
    path_str = str(file_path)

    # settings.py CLOUD_PROVIDERS is the centralized model registry —
    # hits there are intentional and not "hardcoded drift" concerns.
    # Only model-name + url labels get this exemption.
    if path_str.endswith("settings.py") and label.endswith(("-model-name", "-api-url")):
        return "LOW"  # still track (so we can audit the registry itself) but not HIGH

    # cost_service.py pricing dict — model names appear as dict keys to look up
    # prices. These ARE intentional, but prices drift, so track as MED not HIGH.
    if path_str.endswith("cost_service.py") and label.endswith("-model-name"):
        return "MED"

    # Comments / docstrings — model name is illustrative, not runtime behavior.
    stripped = line_text.lstrip()
    if stripped.startswith(("#", "//", "/*", "*", '"""', "'''")):
        return "LOW"
    # JSDoc / multiline doc — heuristic: "* " prefix or inside triple-quoted
    if "/**" in line_text or stripped.startswith("* "):
        return "LOW"

    # Prompt files and fixtures — real names ARE expected (blocklists, examples)
    if any(marker in path_str for marker in EXPECTED_IN["prompts"]):
        if label in ("real-place-name", "real-dynasty", "famous-novel-entity"):
            return "INFO"  # totally expected
    if any(marker in path_str for marker in EXPECTED_IN["fixtures"]):
        return "INFO"
    if any(marker in path_str for marker in EXPECTED_IN["audits"]):
        return "INFO"  # this script itself is full of model names
    if any(marker in path_str for marker in EXPECTED_IN["tests"]):
        if label in ("real-place-name", "real-dynasty", "famous-novel-entity", "hardcoded-date", "hardcoded-year", "chapter-count-literal"):
            return "INFO"
    if any(marker in path_str for marker in EXPECTED_IN["migrations"]):
        if label in ("hardcoded-date", "hardcoded-year", "version-string"):
            return "INFO"

    # Everything else: use default severity
    for pat, lbl, sev in PATTERNS:
        if lbl == label:
            return sev
    return "UNKNOWN"


# =============================================================================
# Scanner
# =============================================================================

def walk_sources():
    roots = [BACKEND_SRC, BACKEND_SCRIPTS, FRONTEND_SRC]
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if fn in SKIP_FILES:
                    continue
                if fn.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".txt", ".md")):
                    yield Path(dirpath) / fn


def scan_file(path: Path) -> list[tuple[int, str, str, str, str]]:
    """Return list of (line_num, label, severity, matched, line_text)."""
    try:
        content = path.read_text()
    except Exception:
        return []
    hits: list[tuple[int, str, str, str, str]] = []
    for pat, label, _ in PATTERNS:
        for m in re.finditer(pat, content):
            line_start = content.rfind("\n", 0, m.start()) + 1
            line_end = content.find("\n", m.end())
            if line_end == -1:
                line_end = len(content)
            line_num = content[: m.start()].count("\n") + 1
            line_text = content[line_start:line_end].strip()
            severity = classify(path, label, line_text)
            hits.append((line_num, label, severity, m.group(0), line_text[:120]))
    return hits


# =============================================================================
# Main
# =============================================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--severity", choices=["HIGH", "MED", "LOW", "INFO", "ALL"], default="HIGH",
                    help="Show only this severity and above (default: HIGH)")
    ap.add_argument("--label", default=None, help="Filter to specific label (e.g. claude-model-name)")
    ap.add_argument("--count", action="store_true", help="Print counts by category instead of details")
    args = ap.parse_args()

    severity_order = ["HIGH", "MED", "LOW", "INFO"]
    sev_min = severity_order.index(args.severity) if args.severity != "ALL" else len(severity_order)

    all_hits: dict[str, list] = defaultdict(list)  # severity → list of (path, *hit)
    total_files = 0
    for path in walk_sources():
        total_files += 1
        for hit in scan_file(path):
            line_num, label, severity, matched, line_text = hit
            if args.label and label != args.label:
                continue
            if args.severity != "ALL":
                if severity_order.index(severity) > sev_min:
                    continue
            all_hits[severity].append((path, line_num, label, matched, line_text))

    if args.count:
        # By severity
        print(f"Scanned {total_files} source files")
        for sev in severity_order:
            print(f"  {sev:5s}: {len(all_hits.get(sev, []))}")
        # By label within HIGH+MED
        by_label: dict[str, int] = defaultdict(int)
        for sev in ("HIGH", "MED"):
            for path, _, label, *_ in all_hits.get(sev, []):
                by_label[label] += 1
        print("\nTop labels (HIGH+MED):")
        for label, cnt in sorted(by_label.items(), key=lambda kv: -kv[1])[:15]:
            print(f"  {label:30s} {cnt}")
        return

    # Detailed output: grouped by severity, then by file
    print(f"Scanned {total_files} source files\n")
    for sev in severity_order:
        hits = all_hits.get(sev, [])
        if not hits:
            continue
        print(f"═══ {sev} ({len(hits)}) ═══")
        # Group by file
        by_file: dict[Path, list] = defaultdict(list)
        for h in hits:
            by_file[h[0]].append(h[1:])
        for path in sorted(by_file.keys()):
            rel = path.relative_to(REPO_ROOT)
            print(f"\n  {rel}")
            for line_num, label, matched, line_text in by_file[path][:8]:
                print(f"    L{line_num:4d} [{label}] {matched}")
                if line_text and line_text != matched:
                    print(f"           {line_text[:100]}")
            if len(by_file[path]) > 8:
                print(f"    ... +{len(by_file[path]) - 8} more in this file")
        print()

    # Exit with signal
    sys.exit(1 if all_hits.get("HIGH") else 0)


if __name__ == "__main__":
    main()
