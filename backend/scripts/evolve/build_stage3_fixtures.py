"""GeoEvolve 阶段 3 —— 一次性冻结基准构建（T 集 / E0 双跑 / 噪声底 / judge 基线）。

产出三个冻结 fixture（随后加入 frozen_manifest.json，评估口径外置）：
  stage3_chapters.json     快速层章节子集（dashboard seed=42 抽样章的
                           确定性子集：索引 [0,2,4,6,8]，每本 5 章）
  stage3_t_set.json        T 集：10 个抽样章 × 3 本的 DeepSeek 扫描地名
                           （复用 quality_dashboard 的 M2 prompt，temp=0）
  stage3_e0_baseline.json  E0：原 prompt 在快速层子集上双跑（A/B）的抽取结果、
                           recall、逐指标噪声底、judge 抽检基线

Usage:
    cd backend && .venv/bin/python scripts/evolve/build_stage3_fixtures.py
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
import quality_dashboard as qd  # noqa: E402


async def build_t_set(conn, ids: dict[str, str], cost_acc: dict) -> dict:
    """T 集：10 个抽样章 × 3 内层本的 M2 扫描（一次性冻结）。"""
    out = {}
    for slug in pe.INNER:
        # 从 dashboard 冻结产物取抽样章清单
        dash_path = Path(pe.__file__).parent / "out" / "dashboard" / f"{slug}.json"
        dash = json.loads(dash_path.read_text(encoding="utf-8"))
        sampled = dash["m2"]["sampled_chapters"]
        chapters: dict[int, list[str]] = {}
        title = pe.qd_titles()[slug]
        for ch in sampled:
            row = conn.execute(
                "SELECT title, content FROM chapters WHERE novel_id=? AND chapter_num=?",
                (ids[slug], ch)).fetchone()
            if not row:
                continue
            user = qd.M2_SCAN_USER_TEMPLATE.format(
                title=title, chapter_num=ch, chapter_title=row[0],
                content=row[1][: qd.MAX_CONTENT_CHARS])
            raw = await qd.deepseek_chat(qd.M2_SCAN_SYSTEM, user,
                                         f"s3-T {slug} ch{ch}", cost_acc)
            try:
                data = qd.parse_llm_json(raw)
                names = sorted({n.strip() for n in data.get("locations", [])
                                if isinstance(n, str) and n.strip()})
            except Exception:
                names = []
            chapters[str(ch)] = names
            print(f"[s3-fixture] T {slug} ch{ch}: {len(names)} 名")
        out[slug] = {"sampled_chapters": sampled, "chapters": chapters}
    return out


async def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(_BACKEND_DIR / ".env", override=True)

    cost_acc = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}

    # 1. 章节子集（R2 起扩到全 10 个抽样章——噪声底压缩,eval_policy v5 口径）
    import sqlite3

    conn = sqlite3.connect(
        f"file:{Path.home() / '.ai-reader-v2' / 'data.db'}?mode=ro", uri=True)
    ids = {slug: nid for slug, _t, nid in qd.resolve_novel_ids(conn)}
    chapters_fixture = {}
    for slug in pe.INNER:
        dash_path = Path(pe.__file__).parent / "out" / "dashboard" / f"{slug}.json"
        sampled = json.loads(dash_path.read_text(encoding="utf-8"))["m2"]["sampled_chapters"]
        chapters_fixture[slug] = list(sampled)  # 全 10 章(v1 曾为索引 [0,2,4,6,8] 的 5 章)
    pe.CHAPTERS_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    pe.CHAPTERS_FIXTURE.write_text(
        json.dumps(chapters_fixture, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(f"[s3-fixture] 章节子集: {chapters_fixture}")

    # 2. T 集
    t_path = pe.T_SET_FIXTURE
    if t_path.exists():
        print("[s3-fixture] T 集已存在，跳过（删除后重跑可重建）")
        t_set = json.loads(t_path.read_text(encoding="utf-8"))
    else:
        t_set = await build_t_set(conn, ids, cost_acc)
        t_path.write_text(json.dumps(t_set, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")

    # 3. E0 双跑（原 prompt）
    genres = pe.load_genre_hints()
    state = pe.load_state()
    assert state["override_section"] is None, "E0 基准必须用原 prompt 构建"
    original = state["original_section"]
    system_by_slug = {slug: pe.build_system_prompt(original, genres[slug])
                      for slug in pe.INNER}
    runs = {}
    for run_tag in ("A", "B"):
        runs[run_tag] = await pe.extract_subset(system_by_slug, chapters_fixture,
                                                cost_acc, None)
    e0 = {}
    for slug in pe.INNER:
        names_a = sorted({n for ns in runs["A"][slug].values() for n in ns})
        names_b = sorted({n for ns in runs["B"][slug].values() for n in ns})
        # recall 分母只用快速层子集章节的 T(其余章节的名字不可达)
        t_names = {n for ch in chapters_fixture[slug]
                   for n in t_set[slug]["chapters"].get(str(ch), [])}

        def recall(names, t_names=t_names):
            return len(t_names & set(names)) / len(t_names) if t_names else None

        from src.extraction.fact_validator import _is_generic_location

        def generic_rate(names):
            return (sum(1 for n in names if _is_generic_location(n) is not None)
                    / len(names)) if names else None

        ra, rb = recall(names_a), recall(names_b)
        ga, gb = generic_rate(names_a), generic_rate(names_b)
        e0[slug] = {
            "names": names_a,   # E0 基线取 A 跑
            "names_b": names_b,
            "t_size": len(t_names),
            "recall_a": ra, "recall_b": rb,
            "recall_noise": abs((ra or 0) - (rb or 0)),
            "generic_rate_a": ga, "generic_rate_b": gb,
            "generic_noise": abs((ga or 0) - (gb or 0)),
            "count_inflation_noise": abs(len(names_a) - len(names_b))
                                       / max(len(names_a), 1),
        }
        print(f"[s3-fixture] E0 {slug}: T={len(t_names)} "
              f"recall A/B={ra:.4f}/{rb:.4f} 噪声={e0[slug]['recall_noise']:.4f} "
              f"generic={ga:.4f}/{gb:.4f}")

    # 4. judge 基线（E0 地名抽样裁定，种子 42）
    judge_base = await pe.judge_spotcheck(
        # 用"E0 自身"当新增名测基线 supported 率：judge_spotcheck 取 E'∖E0,
        # 这里传 extracted=runs B、e0=run A,即测 B 相对 A 的新增名质量
        runs["B"], {slug: {"names": e0[slug]["names"]} for slug in pe.INNER},
        cost_acc, None)
    print(f"[s3-fixture] judge 基线 supported_rate={judge_base['supported_rate']} "
          f"(n={judge_base['n']})")

    e0_doc = {
        "version": 1,
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
        "note": "E0=原 prompt 快速层双跑基线;names 取 A 跑;judge_baseline 为 B∖A 抽检",
        "novels": e0,
        "judge_baseline": judge_base,
        "cost": cost_acc,
    }
    pe.E0_FIXTURE.write_text(json.dumps(e0_doc, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    conn.close()
    print(f"[s3-fixture] done, 成本 ≈ ${cost_acc['cost_usd']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
