"""韵文门控 + 散文锚点实验（西游 M2 210 条）。

攻击目标：双模型共模盲区——韵文/对仗铺陈中罗列的景物名被误判为真地点
（v4 基线：一致率 84.3%，FP=31，判真精确率 68.4%，判真双模型一致仅 ~70%）。

三种策略对比：
  H  纯启发式：名称的全部出现证据都落在韵文段 → 自动判假，否则维持 v4 判定
  A  散文锚点 LLM：prompt 要求引用散文叙事句才能判真（多证据片段，标注文体）
  HA 组合：LLM 判真但全部证据为韵文 → 降级为假；其余取 LLM 判定

文体启发式（对古典小说韵语/骈文铺陈的两个稳定信号）：
  - 顿号枚举密度：±150 字窗口内「、」≥3
  - 对仗句对：按 ，。；！？\n 切段后，相邻等长（±1字）且 ≥8 字的段对 ≥2
依据：玉芝山窗口（韵文）vs 五庄观山门窗口（散文）人工校验。
"""
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import serve  # noqa: E402

NOVEL_ID = '3b2ef56c-1a55-466a-a7d1-34272446a198'
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_LLM = os.path.join(HERE, 'prefill-xiyouji-anchor.json')
V4 = os.path.join(HERE, 'prefill-xiyouji-evidence-v4.json')
QWEN = os.path.join(HERE, 'prefill-xiyouji-qwen.json')
MAX_EVID = 3
WINDOW = 150

_PUNCT_SPLIT = re.compile(r'[，。；！？\n：]')


def load_chapters() -> dict:
    tmp = os.path.join(tempfile.gettempdir(), 'qa-cross-data.db')
    if not os.path.exists(tmp):
        shutil.copyfile(os.path.expanduser('~/.ai-reader-v2/data.db'), tmp)
    conn = sqlite3.connect(tmp)
    return dict(conn.execute(
        "SELECT chapter_num, content FROM chapters WHERE novel_id=?",
        (NOVEL_ID,)).fetchall())


def find_evidences(name: str, chap_nums: list, chapters: dict) -> list[dict]:
    """扫最多 SCAN_LIMIT 处出现，散文证据优先返回 MAX_EVID 条。

    散文优先：名字常在韵文首现、散文再现（如五行山），只看前 N 处韵文
    会误杀真实地点；all_verse 仅在扫描范围内全无散文证据时成立。
    """
    SCAN_LIMIT = 8
    found = []
    order = list(dict.fromkeys(list(chap_nums) + sorted(chapters)))
    for cn in order:
        content = chapters.get(cn) or ''
        start = 0
        while len(found) < SCAN_LIMIT:
            i = content.find(name, start)
            if i < 0:
                break
            seg = content[max(0, i - WINDOW): i + len(name) + WINDOW]
            seg = re.sub(r'\s+', ' ', seg).strip()
            found.append({'chapter': cn, 'text': seg,
                          'verse': is_verse_window(seg)})
            start = i + len(name)
        if len(found) >= SCAN_LIMIT:
            break
    # 散文优先排序（稳定，保留章节顺序内的相对序）
    found.sort(key=lambda e: e['verse'])
    return found[:MAX_EVID]


def is_verse_window(text: str) -> bool:
    """韵文/骈文铺陈判定：顿号枚举密度 + 相邻对仗句对。"""
    enum = text.count('、')
    segs = [s.strip() for s in _PUNCT_SPLIT.split(text) if s.strip()]
    pairs = 0
    for a, b in zip(segs, segs[1:]):
        if abs(len(a) - len(b)) <= 1 and min(len(a), len(b)) >= 8:
            pairs += 1
    return enum >= 3 or pairs >= 2


ANCHOR_PROMPT = (
    "你是中国古典小说地点识别专家。判断给定名称是不是「故事世界里真实存在的专有地点」。\n"
    "每个名称给出若干处原文片段，并标注了文体（散文/韵文罗列）。\n"
    "判 true 的唯一标准：你能从**散文**片段中引用一句叙事原文，表明该地是人物实际"
    "活动、居住、前往或提及要去的地点。引用句放入 anchor 字段。\n"
    "以下情形判 false（anchor 置空）：\n"
    "1. 证据仅见于韵文罗列/诗词/对仗铺陈的景物枚举（即使名字是专名形态）；\n"
    "   例外：名称在韵文中出现、但指故事世界公认的真实地点（如「广寒宫」即月宫），"
    "且你确知其在小说叙事中作为真实地点存在，可判 true，anchor 注明依据。\n"
    "2. 通名/泛称（如「花园」「山顶」「东廊」「京城」「禅堂」）——即使叙事中真实出现，"
    "也不独立成地点节点；\n"
    "3. 人名/神佛名/器物名误识别，或原文不存在（幻觉）。\n"
    "置信度: 证据明确=high, 一般=medium, 边界=low。\n"
    '严格只输出 JSON: {"verdicts":[{"id":0,"value":true|false,'
    '"confidence":"high|medium|low","anchor":"引用句或空"}]}，'
    'id 从 0 递增、不得遗漏、不得多出。'
)


def _env() -> dict:
    env_path = os.path.join(HERE, '..', '..', '..', 'backend', '.env')
    cfg = {}
    for line in open(os.path.normpath(env_path)):
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    cfg.setdefault('base_url', 'https://api.deepseek.com/v1')
    cfg.setdefault('model', 'deepseek-chat')
    return cfg


def _deepseek(cfg, system, user):
    return serve._deepseek_chat(serve._backend_env(), system, user)


def evaluate(rows, key, label):
    valid = [r for r in rows if r[key] is not None]
    agr = sum(1 for r in valid if r[key] == r['human'])
    tp = sum(1 for r in valid if r[key] and r['human'])
    fp = sum(1 for r in valid if r[key] and not r['human'])
    fn = sum(1 for r in valid if not r[key] and r['human'])
    tn = sum(1 for r in valid if not r[key] and not r['human'])
    prec = tp / (tp + fp) if tp + fp else 0
    rec = tp / (tp + fn) if tp + fn else 0
    print(f'{label}: 一致率 {agr}/{len(valid)}={agr / len(valid):.1%} | '
          f'FP={fp} FN={fn} | 判真精确率 {prec:.1%} 召回 {rec:.1%}')
    return {'agree': agr / len(valid), 'fp': fp, 'fn': fn,
            'prec': prec, 'rec': rec}


def main():
    data = serve.merge_samples('xiyouji')
    items = {it['uid']: it for it in data['items']}
    m2 = [it for it in data['items'] if it['type'] in ('m2_qa', 'm2_cal')]
    chapters = load_chapters()

    # ── 1. 多证据 + 文体标注 ─────────────────────────────────
    evid_map = {}
    verse_stat = Counter()
    for it in m2:
        evs = find_evidences(it['name'], it.get('chapters') or [], chapters)
        evid_map[it['uid']] = evs
        verse_stat['all_verse' if evs and all(e['verse'] for e in evs)
                   else 'has_prose'] += 1
    print('文体分布:', dict(verse_stat))

    # ── 2. 散文锚点 LLM 判定 ─────────────────────────────────
    cfg = _env()
    result = {}
    BATCH = 20
    for i in range(0, len(m2), BATCH):
        chunk = m2[i:i + BATCH]
        lines = []
        for k, it in enumerate(chunk):
            evs = evid_map[it['uid']]
            if evs:
                ev_txt = ' | '.join(
                    f"[{'韵文罗列' if e['verse'] else '散文'}] 第{e['chapter']}回: …{e['text']}…"
                    for e in evs)
            else:
                ev_txt = '(原文未找到)'
            lines.append(f'{k}. 名称={it["name"]} 证据: {ev_txt}')
        user = (f'逐条核对以下 {len(chunk)} 条（id 从 0 到 {len(chunk)-1}）：\n'
                + '\n'.join(lines))
        verdicts = []
        for attempt in range(2):
            try:
                raw = _deepseek(cfg, ANCHOR_PROMPT, user)
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
            if v is not None:
                result[it['uid']] = {
                    'value': v.get('value'),
                    'confidence': v.get('confidence', 'medium'),
                    'anchor': v.get('anchor') or '',
                }
        print(f'批 {i // BATCH + 1}/{(len(m2) + BATCH - 1) // BATCH} 完成')
    json.dump(result, open(OUT_LLM, 'w'), ensure_ascii=False, indent=2)
    print(f'写出 {OUT_LLM}: {len(result)} 条')

    # ── 3. 三方对比 ──────────────────────────────────────────
    v4 = json.load(open(V4))
    qwen = json.load(open(QWEN)) if os.path.exists(QWEN) else {}
    rows = []
    for uid, it in items.items():
        if it['type'] not in ('m2_qa', 'm2_cal') or it['value'] is None:
            continue
        evs = evid_map.get(uid, [])
        all_verse = bool(evs) and all(e['verse'] for e in evs)
        v4v = str(v4.get(uid, {}).get('value')).lower() == 'true' if uid in v4 else None
        av = result.get(uid)
        a = str(av['value']).lower() == 'true' if av else None
        row = {'uid': uid, 'name': it['name'], 'human': bool(it['value']),
               'all_verse': all_verse,
               'v4': v4v,
               'anchor': a,
               'anchor_conf': av.get('confidence') if av else None,
               'anchor_text': av.get('anchor') if av else ''}
        # H: 全韵文 → 假，否则维持 v4
        row['H'] = (False if all_verse else v4v) if v4v is not None else None
        # HA: LLM 判真但全韵文 → 假
        row['HA'] = (a and not all_verse) if a is not None else None
        rows.append(row)

    print()
    evaluate(rows, 'v4', 'v4 基线（无文体信息）  ')
    evaluate(rows, 'H', 'H  启发式门控(v4+全韵文→假)')
    evaluate(rows, 'anchor', 'A  散文锚点 LLM      ')
    evaluate(rows, 'HA', 'HA 组合(锚点+韵文降级) ')

    print('\n=== 共模错误修复情况（v4 错的 FP，各策略表现）===')
    fps = [r for r in rows if r['v4'] and not r['human']]
    for strat in ['H', 'anchor', 'HA']:
        fixed = sum(1 for r in fps if r[strat] is False)
        print(f'v4 FP 共 {len(fps)} 条, {strat} 修复 {fixed} 条')
    print('\n=== 全韵文项里人工判真的（启发式会误杀的风险项）===')
    for r in rows:
        if r['all_verse'] and r['human']:
            print(f"  {r['name']} 人工=True v4={r['v4']} 锚点={r['anchor']} "
                  f"anchor引文: {(r['anchor_text'] or '')[:60]}")
    print('\n=== A/HA 新引入的错误（v4 对、新策略错）===')
    for r in rows:
        for strat in ['anchor', 'HA']:
            if r['v4'] == r['human'] and r[strat] is not None and r[strat] != r['human']:
                print(f"  [{strat}] {r['name']} 人工={r['human']} v4={r['v4']} "
                      f"all_verse={r['all_verse']} anchor: {(r['anchor_text'] or '')[:60]}")
                break

    # 韵文降级组合 + 双模型交叉的最终形态预估
    if qwen:
        both_false = [r for r in rows
                      if r['HA'] is False
                      and str(qwen.get(r['uid'], {}).get('value')).lower() == 'false']
        bf_correct = sum(1 for r in both_false if not r['human'])
        print(f'\n=== 最终形态预估: HA判假 ∩ Qwen判假 ===')
        print(f'{len(both_false)} 条, 准确率 {bf_correct / len(both_false):.1%}'
              if both_false else '0 条')


if __name__ == '__main__':
    main()
