"""QA 实验：带原文证据 + 严格标准重跑西游记 M2 预填，与旧预填、人工判定对比。

只读冻结 DB 副本 /tmp/qa-exp-data.db；结果写 /tmp/prefill-xiyouji-evidence.json，
不触碰正式 .prefill-xiyouji.json。
"""
import json
import os
import re
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, os.path.expanduser(
    '~/Baiduyun/AISoul/AI-Reader-V2/scripts/qa-review'))
import serve  # noqa: E402

NOVEL_ID = '3b2ef56c-1a55-466a-a7d1-34272446a198'
OUT = '/tmp/prefill-xiyouji-evidence-v4.json'
WINDOW = 120

# ── 1. 取原文证据片段 ─────────────────────────────────────────
conn = sqlite3.connect('/tmp/qa-exp-data.db')
chap_rows = conn.execute(
    "SELECT chapter_num, content FROM chapters WHERE novel_id=?",
    (NOVEL_ID,)).fetchall()
chapters = {n: c for n, c in chap_rows}


def evidence_for(name: str, chap_nums: list[int]) -> str:
    for cn in chap_nums:
        content = chapters.get(cn) or ''
        i = content.find(name)
        if i >= 0:
            seg = content[max(0, i - WINDOW): i + len(name) + WINDOW]
            seg = re.sub(r'\s+', ' ', seg).strip()
            return f'第{cn}回: …{seg}…'
    # fallback: 全书找
    for cn, content in sorted(chapters.items()):
        i = content.find(name)
        if i >= 0:
            seg = content[max(0, i - WINDOW): i + len(name) + WINDOW]
            seg = re.sub(r'\s+', ' ', seg).strip()
            return f'第{cn}回: …{seg}…'
    return '(原文未找到)'


# ── 2. 新 prompt：证据 + 严格标准 ─────────────────────────────
PROMPT_M2_EVID = (
    "你是中国古典小说地点识别专家。根据给出的原文片段，判断该名称在小说中"
    "是不是「故事世界里真实存在的专有地点」（任意粒度，从大陆到亭台均可）。\n"
    "判 false 的情形：\n"
    "1. 泛称/通名（如「洞」「山」「人家」、无专名化的「京城」「禅堂」）；\n"
    "2. 韵语、诗词、对仗铺陈中罗列的景物名或前朝典故名（如「大明宫」「华清宫」"
    "出现在写景韵文中，叙事中并无此地），判 false；\n"
    "   但名称虽出现在韵文中、却指故事世界真实地点的（如「广寒宫」即月宫），仍判 true。\n"
    "3. 通名/泛称一律 false：名称本身是无专名修饰的通用词（如「花园」「山顶」"
    "「东廊」「后宫」「京城」「禅堂」「牌楼」「山门」），即使在叙事中真实出现、"
    "人物确实身处其中，也判 false——这类名称应挂到所属专名父级下，不独立成节点；\n"
    "4. 人名、神佛名、器物名被误识别为地点，或原文中并不存在（幻觉），判 false。\n"
    "判 true 的情形：名称为专有名称，且在叙事中作为故事世界真实地点出现"
    "（如「五庄观山门」「广寒宫」）。\n"
    "置信度: 证据明确=high, 一般把握=medium, 边界=low。\n"
    '严格只输出 JSON: {"verdicts":[{"id":0,"value":true|false,'
    '"confidence":"high|medium|low"}]}，id 必须保持输入顺序从 0 递增、不得遗漏、'
    '不得多出、不要解释。'
)


def main():
    data = serve.merge_samples('xiyouji')
    m2_items = [it for it in data['items'] if it['type'] in ('m2_qa', 'm2_cal')]
    print(f'M2 项: {len(m2_items)}')

    # 取证据
    for it in m2_items:
        it['evidence_text'] = evidence_for(it['name'], it.get('chapters') or [])
    noev = [it['name'] for it in m2_items if it['evidence_text'] == '(原文未找到)']
    if noev:
        print('未找到原文的:', noev)

    cfg = serve._backend_env()
    BATCH = 25
    result = {}
    for i in range(0, len(m2_items), BATCH):
        chunk = m2_items[i:i + BATCH]
        lines = []
        for k, it in enumerate(chunk):
            lines.append(f'{k}. 名称={it["name"]} 原文: {it["evidence_text"]}')
        user = (f'逐条核对以下 {len(chunk)} 条（id 从 0 到 {len(chunk)-1}）：\n'
                + '\n'.join(lines))
        verdicts = []
        for attempt in range(2):
            try:
                raw = serve._deepseek_chat(cfg, PROMPT_M2_EVID, user)
                verdicts = serve._parse_llm_json(raw).get('verdicts') or []
                break
            except Exception as e:  # noqa: BLE001
                print(f'批 {i // BATCH + 1} 第{attempt + 1}次失败: {e}')
        byid = {}
        for v in verdicts:
            try:
                byid[int(v.get('id'))] = v
            except (TypeError, ValueError):
                continue
        for k, it in enumerate(chunk):
            v = byid.get(k)
            if v is None:
                print(f'WARN 缺 id={k} name={it["name"]}')
                continue
            result[it['uid']] = {'value': v.get('value'),
                                  'confidence': v.get('confidence', 'medium')}
        print(f'批 {i // BATCH + 1}/{(len(m2_items) + BATCH - 1) // BATCH} 完成')

    json.dump(result, open(OUT, 'w'), ensure_ascii=False, indent=2)
    print(f'写出 {OUT}: {len(result)} 条')

    # ── 3. 三方对比 ──────────────────────────────────────────
    old = serve.load_prefill('xiyouji')
    items = {it['uid']: it for it in data['items']}
    rows = []
    for uid, it in items.items():
        if it['type'] not in ('m2_qa', 'm2_cal') or it['value'] is None:
            continue
        human = bool(it['value'])
        old_ai = str(old.get(uid, {}).get('value')).lower() == 'true'
        new_v = result.get(uid)
        new_ai = str(new_v.get('value')).lower() == 'true' if new_v else None
        rows.append({'uid': uid, 'name': it['name'], 'human': human,
                     'old': old_ai, 'new': new_ai,
                     'new_conf': new_v.get('confidence') if new_v else None})

    def stats(key):
        valid = [r for r in rows if r[key] is not None]
        agr = sum(1 for r in valid if r[key] == r['human'])
        tp = sum(1 for r in valid if r[key] and r['human'])
        fp = sum(1 for r in valid if r[key] and not r['human'])
        fn = sum(1 for r in valid if not r[key] and r['human'])
        tn = sum(1 for r in valid if not r[key] and not r['human'])
        return (f'一致率 {agr}/{len(valid)} = {agr / len(valid):.1%} | '
                f'TP={tp} FP={fp} FN={fn} TN={tn} | '
                f'精确率 {tp / (tp + fp):.1%} 召回 {tp / (tp + fn):.1%}')

    print('\n=== 旧预填（无证据） ===')
    print(stats('old'))
    print('=== 新预填（带证据+严格标准） ===')
    print(stats('new'))

    print('\n=== 新预填按置信度一致率 ===')
    for c in ['high', 'medium', 'low']:
        sub = [r for r in rows if r['new_conf'] == c]
        if sub:
            a = sum(1 for r in sub if r['new'] == r['human'])
            print(f'{c}: {len(sub)} 条, 一致 {a} ({a / len(sub):.1%})')

    print('\n=== 新预填仍不一致项 ===')
    for r in rows:
        if r['new'] is not None and r['new'] != r['human']:
            print(f"  {r['name']}  人工={r['human']} 新AI={r['new']} "
                  f"conf={r['new_conf']} (旧AI={r['old']})")

    fixed = [r for r in rows
             if r['old'] != r['human'] and r['new'] == r['human']]
    broken = [r for r in rows
              if r['old'] == r['human'] and r['new'] is not None
              and r['new'] != r['human']]
    print(f'\n修复: {len(fixed)} 条 | 新错: {len(broken)} 条')
    print('新错明细:', [(r['name'], r['human'], r['old'], r['new']) for r in broken])


if __name__ == '__main__':
    main()
