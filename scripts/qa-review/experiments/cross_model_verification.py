"""办法四验证实验：双模型交叉核对（DeepSeek × Claude）—— 西游记 M2 地点真伪。

方法：两套模型用同一 prompt + 同一原文证据独立判定 210 条 M2 项；
双模型一致 → 自动放行；分歧 → 进人工。以人工判定为基准评估：
  - 放行准确率（双模型一致时有多准）
  - 人工残余量（分歧项占比）
  - 共模错误（双模型一致但都错 —— 自动化的实际上限）

前置：冻结 DB（md5 须匹配 xiyouji.json 的 freeze.db_md5），脚本自动复制到临时文件。
产物：prefill-xiyouji-claude.json（Claude 判定），不触碰正式预填文件。
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
QWEN_MODEL = 'qwen3-235b-a22b-instruct-2507'
QWEN_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   'prefill-xiyouji-qwen.json')
DEEPSEEK_V4 = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'prefill-xiyouji-evidence-v4.json')
WINDOW = 120

# 与 v4 完全相同的 prompt（DeepSeek 侧结果即 v4 产物，保证对照干净）
PROMPT = (
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
    '"confidence":"high|medium|low"}]}，id 从 0 递增、不得遗漏、不得多出、不要解释。'
)


def _env() -> dict:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            '..', '..', '..', 'backend', '.env')
    cfg = {}
    for line in open(os.path.normpath(env_path)):
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def _claude_chat(cfg: dict, system: str, user: str) -> str:
    """第二模型独立判定：阿里云百炼 Qwen（OpenAI 兼容接口）。"""
    import httpx
    resp = httpx.post(
        f'{QWEN_BASE_URL}/chat/completions',
        headers={'Authorization': f"Bearer {cfg['DASHSCOPE_API_KEY']}",
                 'Content-Type': 'application/json'},
        json={'model': QWEN_MODEL,
              'messages': [{'role': 'system', 'content': system},
                           {'role': 'user', 'content': user}],
              'temperature': 0},
        timeout=180)
    resp.raise_for_status()
    return resp.json()['choices'][0]['message']['content']


def main():
    data = serve.merge_samples('xiyouji')
    m2_items = [it for it in data['items'] if it['type'] in ('m2_qa', 'm2_cal')]
    print(f'M2 项: {len(m2_items)}')

    # 证据提取（复制冻结 DB 到临时文件，只读）
    live_db = os.path.expanduser('~/.ai-reader-v2/data.db')
    tmp = os.path.join(tempfile.gettempdir(), 'qa-cross-data.db')
    if not os.path.exists(tmp):
        shutil.copyfile(live_db, tmp)
    conn = sqlite3.connect(tmp)
    chapters = dict(conn.execute(
        "SELECT chapter_num, content FROM chapters WHERE novel_id=?",
        (NOVEL_ID,)).fetchall())

    def evidence_for(name, chap_nums):
        for cn in list(chap_nums) + sorted(chapters):
            content = chapters.get(cn) or ''
            i = content.find(name)
            if i >= 0:
                seg = content[max(0, i - WINDOW): i + len(name) + WINDOW]
                return f'第{cn}回: …' + re.sub(r'\s+', ' ', seg).strip() + '…'
        return '(原文未找到)'

    for it in m2_items:
        it['evidence_text'] = evidence_for(it['name'], it.get('chapters') or [])

    # Claude 判定
    cfg = _env()
    BATCH = 25
    result = {}
    for i in range(0, len(m2_items), BATCH):
        chunk = m2_items[i:i + BATCH]
        lines = [f'{k}. 名称={it["name"]} 原文: {it["evidence_text"]}'
                 for k, it in enumerate(chunk)]
        user = (f'逐条核对以下 {len(chunk)} 条（id 从 0 到 {len(chunk)-1}）：\n'
                + '\n'.join(lines))
        verdicts = []
        for attempt in range(2):
            try:
                raw = _claude_chat(cfg, PROMPT, user)
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
                result[it['uid']] = {'value': v.get('value'),
                                      'confidence': v.get('confidence', 'medium')}
        print(f'批 {i // BATCH + 1}/{(len(m2_items) + BATCH - 1) // BATCH} 完成')

    json.dump(result, open(OUT, 'w'), ensure_ascii=False, indent=2)
    print(f'写出 {OUT}: {len(result)} 条')

    # ── 交叉统计 ──────────────────────────────────────────────
    ds = json.load(open(DEEPSEEK_V4))
    items = {it['uid']: it for it in data['items']}
    rows = []
    for uid, it in items.items():
        if it['type'] not in ('m2_qa', 'm2_cal') or it['value'] is None:
            continue
        if uid not in ds or uid not in result:
            continue
        rows.append({
            'uid': uid, 'name': it['name'], 'human': bool(it['value']),
            'ds': str(ds[uid]['value']).lower() == 'true',
            'cl': str(result[uid]['value']).lower() == 'true',
        })

    n = len(rows)
    agree = [r for r in rows if r['ds'] == r['cl']]
    disagree = [r for r in rows if r['ds'] != r['cl']]
    auto_correct = sum(1 for r in agree if r['ds'] == r['human'])
    both_wrong = [r for r in agree if r['ds'] != r['human']]

    def solo(key):
        c = sum(1 for r in rows if r[key] == r['human'])
        return f'{c}/{n} = {c / n:.1%}'

    print(f'\n=== 单模型一致率（对人工） ===')
    print(f'DeepSeek v4: {solo("ds")}   Qwen: {solo("cl")}')
    print(f'\n=== 双模型交叉（办法四） ===')
    print(f'总样本: {n}')
    print(f'双模型一致(自动放行): {len(agree)} ({len(agree) / n:.1%})，'
          f'其中正确 {auto_correct}，放行准确率 {auto_correct / len(agree):.1%}')
    print(f'双模型分歧(进人工): {len(disagree)} ({len(disagree) / n:.1%})')
    print(f'共模错误(一致但都错，自动化上限损失): {len(both_wrong)} '
          f'({len(both_wrong) / n:.1%})')
    print(f'端到端错误率(人工修正分歧后剩余): {len(both_wrong) / n:.1%}')
    print('\n=== 共模错误明细 ===')
    for r in both_wrong:
        print(f"  {r['name']}  人工={r['human']} 双模型={r['ds']}")
    print('\n=== 分歧项明细（进人工的部分） ===')
    for r in disagree:
        print(f"  {r['name']}  人工={r['human']} DS={r['ds']} CL={r['cl']}")
    print('\n=== 分歧项中谁对得多 ===')
    print('DS 对:', sum(1 for r in disagree if r['ds'] == r['human']),
          ' CL 对:', sum(1 for r in disagree if r['cl'] == r['human']))


if __name__ == '__main__':
    main()
