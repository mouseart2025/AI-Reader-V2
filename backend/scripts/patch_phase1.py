"""One-off patch: have DeepSeek fill specific gaps in phase1_bible.json.

Takes an existing phase1_bible.json that failed validation and applies
targeted additions (new locations, new relationships) without regenerating
or modifying unrelated content.

Usage:
    uv run python scripts/patch_phase1.py

The patch is hard-coded for the current gaps:
- 5 missing locations referenced by Ch22-26
- Insufficient family relationships (1 → 3+)
- Insufficient intimate relationships (1 → 2+)
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

# Reuse the DeepSeek client + pricing from the main script
from scripts.synthesize_novel import (  # noqa: E402
    DeepSeekClient,
    PRICE_INPUT,
    PRICE_OUTPUT,
    parse_json_lenient,
    append_metadata,
)

BIBLE_PATH = Path(
    "/Users/leonfeng/Baiduyun/AISoul/ai-reader-internal/paper/evaluation/contamination-free/novel/phase1_bible.json"
)

PATCH_PROMPT = """你正在为仙侠小说《星尘劫》补全世界设定。以下是现有的完整 JSON,请**仅新增** 下面指定的条目,**不要修改或删除** 任何现有内容。

# 现有设定(只读)

```json
{EXISTING_BIBLE}
```

# 必须新增的条目

## A. 5 个缺失地点(章节大纲 Ch22-26 已引用但未入列)

根据现有世界观,为以下 5 个地点合理分配 tier 和 parent(parent 必须是现有 locations 里已存在的名字):
- "星尘之源"
- "秘境内部"
- "核心祭坛"
- "光暗交界"
- "暗影区域"

建议逻辑:这 5 个地点是小说高潮部分的秘境,应归属到现有某个 kingdom/site 之下(自己选择合适的父级)。建议后缀 rank 层级合理,例如:
- "秘境内部" → site,parent = 某 kingdom 或已有大 site
- "星尘之源" / "光暗交界" / "暗影区域" → site,parent = "秘境内部"
- "核心祭坛" → building,parent = "星尘之源"

## B. 2 个新增 family 关系

现有 family 只有 1 对(云逸尘/云岚王 父子)。新增 2 对,使用**现有人物**或其近亲,可选:
- 云逸尘的母亲(可新增一名配角 "xx 母"),关系为 "母子"
- 云岚王与某位大臣的兄弟关系
- 云逸尘与现有某配角的堂兄妹关系

类型字段建议(注意 category 必须是 "family"):母子 / 兄妹 / 夫妻 / 叔侄 / 堂兄妹

## C. 1 个新增 intimate 关系

现有 intimate 只有 1 对。新增 1 对,使用现有人物,可选:
- 云逸尘与柳若烟的"恋人"关系
- 云逸尘与林清影的"结拜兄妹"关系(若未被 family 占用)
- 或其他合理组合

# 输出格式

**严格返回单个 JSON 对象**,只包含要新增的内容:

{{
  "add_locations": [
    {{"name": "...", "tier": "...", "parent": "...", "note": "..."}}
  ],
  "add_characters": [
    {{"canonical_name": "...", "aliases": [...], "role": "...", "description": "..."}}
  ],
  "add_relationships": [
    {{"person_a": "...", "person_b": "...", "type": "...", "category": "..."}}
  ]
}}

如果不需要新增人物(即使用现有人物即可),则 `add_characters` 为 `[]`。
输出时不加 markdown 代码块,不加解释文字。
"""


def main():
    if not BIBLE_PATH.exists():
        sys.exit(f"ERROR: {BIBLE_PATH} not found; run --phase 1 first")
    bible = json.loads(BIBLE_PATH.read_text())

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        sys.exit("ERROR: DEEPSEEK_API_KEY missing in backend/.env")

    client = DeepSeekClient(api_key)

    print("[patch] calling DeepSeek with surgical patch request...")
    t0 = time.time()
    prompt = PATCH_PROMPT.format(EXISTING_BIBLE=json.dumps(bible, ensure_ascii=False, indent=2))
    resp = client.chat(
        messages=[
            {"role": "system", "content": "你是仙侠小说世界设定的补全助手,严格按用户要求只新增指定内容。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.5,  # lower temp for more faithful patching
        max_tokens=4096,
        response_format={"type": "json_object"},
    )
    elapsed = time.time() - t0

    content = resp["content"]
    in_tok = resp["prompt_tokens"]
    out_tok = resp["completion_tokens"]
    cost = in_tok * PRICE_INPUT + out_tok * PRICE_OUTPUT
    print(f"[patch] done in {elapsed:.1f}s | input={in_tok} output={out_tok} ≈${cost:.4f}")

    patch = parse_json_lenient(content)
    if patch is None:
        raw_path = BIBLE_PATH.parent / "patch_raw.txt"
        raw_path.write_text(content)
        sys.exit(f"[patch] parse error; raw saved to {raw_path}")

    # Backup original
    backup = BIBLE_PATH.with_suffix(".json.bak")
    backup.write_text(BIBLE_PATH.read_text())
    print(f"[patch] backed up original to {backup}")

    # Merge
    bible["locations"].extend(patch.get("add_locations") or [])
    if patch.get("add_characters"):
        bible["characters"].extend(patch["add_characters"])
    bible["relationships"].extend(patch.get("add_relationships") or [])

    print()
    print("[patch] new locations added:")
    for L in patch.get("add_locations") or []:
        print(f"  + {L.get('name')} (tier={L.get('tier')}, parent={L.get('parent')})")
    print("[patch] new characters added:")
    for c in patch.get("add_characters") or []:
        print(f"  + {c.get('canonical_name')} ({c.get('role', '?')})")
    print("[patch] new relationships added:")
    for r in patch.get("add_relationships") or []:
        print(f"  + [{r.get('category')}] {r.get('person_a')} ← {r.get('type')} → {r.get('person_b')}")

    BIBLE_PATH.write_text(json.dumps(bible, ensure_ascii=False, indent=2))
    print(f"\n[patch] saved merged bible to {BIBLE_PATH}")

    append_metadata(
        BIBLE_PATH.parent,
        {
            "event": "phase1_patch",
            "elapsed_s": round(elapsed, 1),
            "tokens": {"in": in_tok, "out": out_tok},
            "cost_usd": round(cost, 4),
            "patch_summary": {
                "locations_added": len(patch.get("add_locations") or []),
                "characters_added": len(patch.get("add_characters") or []),
                "relationships_added": len(patch.get("add_relationships") or []),
            },
        },
    )
    print("\n[patch] next: uv run python scripts/synthesize_novel.py --validate")


if __name__ == "__main__":
    main()
