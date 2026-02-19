"""Scene extractor — split chapters into scenes for screenplay mode.

Uses a multi-signal scoring system to detect scene boundaries in Chinese
narrative text. No LLM needed — purely rule-based splitting.

Boundary signals and weights:
  - Narrator transition (却说/话说/且说/再说/单说)     5
  - Scene closure (不在话下/按下不表/暂且不提)          4
  - Time jump (次日/三日后/是夜/翌晨/过了数日)         4
  - Scene opening (但见/只见/来到/行至/进入)            3
  - Blank-line gap (2+ consecutive blank lines)       3
  - Dialogue cluster boundary (≥3 lines mode switch)  2
  - Location change (from ChapterFact events)         2
  - Participant change (from ChapterFact events)      1
"""

from __future__ import annotations

import json
import logging
import re

from src.db import chapter_fact_store, chapter_store

logger = logging.getLogger(__name__)

# ── Cache ─────────────────────────────────────────

_scene_cache: dict[str, dict[int, list[dict]]] = {}  # novel_id -> {chapter_num -> scenes}


def invalidate_scene_cache(novel_id: str) -> None:
    """Clear scene cache for a novel."""
    _scene_cache.pop(novel_id, None)


# ── Boundary signal patterns ─────────────────────

# Weight 5 — narrator transition phrases (classic Chinese storytelling)
_NARRATOR_TRANSITION = re.compile(
    r"^(?:却说|话说|且说|再说|单说|单表|再表|且表|却表|话表|"
    r"且不说|再不说|暂不说|只说|先说|后说|"
    r"却道是|正是|有诗为证|有词为证|"
    r"欲知后事|未知|毕竟)"
)

# Weight 4 — scene closure phrases
_SCENE_CLOSURE = re.compile(
    r"(?:不在话下|按下不表|暂且不提|此处不提|不必细说|不必多言|"
    r"言归正传|闲话少叙|闲言少叙|此是后话|容后再叙|"
    r"后事如何|且听下回|这且不说|不[在题])[。，。,]?"
)

# Chapter ending pattern — should be merged into previous scene, not standalone
_CHAPTER_ENDING = re.compile(
    r"^(?:毕竟|欲知后事|且听下回|正是|有诗为证|未知)"
)

# Weight 4 — time jump expressions
_TIME_JUMP = re.compile(
    r"^(?:次日|翌日|明日|隔日|过了[一二三四五六七八九十百千数几]?[日天年月]|"
    r"[一二三四五六七八九十]日后|数日后|[一二三四五六七八九十百千]年后|"
    r"是[日夜晚]|当[夜晚日]|那[日夜晚天]|到了[第那]|"
    r"翌[日晨]|清[晨早]|黄昏|傍晚|入夜|深夜|半夜|三更|"
    r"天[明亮]|天色[微渐]|日[出落]|月[上升]|"
    r"过了[一数几]会[儿]?|过了半[天晌日]|"
    r"又过了|时光荏苒|光阴似箭|"
    r"不[多觉][久时]|转眼[间之]?|不一[会时日]|须臾|片刻之?后|少顷|"
    r"一日|这一日|忽一日|有一日|"
    r"却早过了|不觉[过已]了|不觉倏)"
)

# Weight 2 — weaker time signals at paragraph start
_TIME_WEAK = re.compile(
    r"^(?:当[时下]|此时|这时|彼时|那时|"
    r"少[时顷]间|一[时连]间)"
)

# Weight 3 — internal time jump (within first 50 chars, not necessarily at start)
# Catches cases like "美猴王享乐天真，何期有三五百载。一日，..."
_INTERNAL_TIME_JUMP = re.compile(
    r"(?:。|，|；)(?:一日[，,。]|这一日|忽一日|有一日|"
    r"却早过了|不觉[已过]了[一二三四五六七八九十百千数几]+[年月日]|"
    r"何期有[一二三四五六七八九十百千数几]+[年月日载百])"
)

# Weight 3 — scene opening phrases
_SCENE_OPENING = re.compile(
    r"^(?:但见|只见|来到|行至|进入|走进|来至|赶到|回到|去到|"
    r"走到|飞到|奔到|到了|到得|径[直奔往]|一路|"
    r"忽[然见听闻]|猛然|突然|蓦然|陡然|倏然|"
    r"原来|不想|不料|谁[知想料]|哪[知想料]|"
    r"好[猴行大圣]|这[猴行大圣]|那[猴行大圣]|"
    r"这一去|也是他|正[是行走说])"
)

# ── Time-of-day detection ─────────────────────────

_TIME_MORNING = re.compile(
    r"清[晨早]|早[上晨间]|天[明亮]|晨[光曦]|旭日|日出|卯时|辰时|拂晓|黎明|破晓"
)
_TIME_NOON = re.compile(
    r"午[时后间]|正午|中午|日[正中]|巳时|午时|晌午|日头正"
)
_TIME_EVENING = re.compile(
    r"黄昏|傍晚|日落|日[暮薄]|夕[阳照]|申时|酉时|薄暮|暮色"
)
_TIME_NIGHT = re.compile(
    r"[深半]夜|入夜|夜[间里晚色深幕]|月[色光上]|星[光辰]|三更|"
    r"戌时|亥时|子时|丑时|寅时|漆黑|灯火"
)

# ── Emotional tone keywords ──────────────────────

_TONE_BATTLE = re.compile(
    r"杀[了来去死过将]|打[了来去将杀斗]|[大激鏖]战|恶斗|"
    r"一[刀剑枪棒拳掌]|交[手战锋]|厮[杀打]|"
    r"攻[击打]|抵[挡御]|格[挡斗]|流血|负伤|怒[吼喝骂]|"
    r"砍[了去来]|刺[了去来]|挡[了住开]"
)
_TONE_SAD = re.compile(
    r"[哭泣悲伤]|落[泪下]泪|流泪|痛[哭苦]|悲[痛伤戚]|哀[伤痛号]|"
    r"凄[惨凉]|惨|伤心|难过|感[伤怀]"
)
_TONE_HAPPY = re.compile(
    r"[笑喜乐]|欢[喜乐笑]|高兴|快[乐活]|大喜|开心|"
    r"庆[祝贺]|贺|喜悦|欣喜|兴奋"
)
_TONE_TENSE = re.compile(
    r"紧张|危[急险]|急[忙切]|惊[恐慌险惧吓]|恐[惧怖]|"
    r"[逃跑躲闪藏]|追[赶杀来]|险[些要]|命悬|千钧一发|"
    r"心[惊慌跳]|冷汗|倒吸|不[妙好敢]"
)

# ── Dialogue detection ────────────────────────────

_DIALOGUE_STARTERS = ("\u201c", "\u300c", "\"", "\u2018", "\u300e")


def _is_dialogue(para: str) -> bool:
    """Check if a paragraph starts as dialogue."""
    return para.startswith(_DIALOGUE_STARTERS)


def _count_dialogue(paragraphs: list[str]) -> int:
    """Count paragraphs that look like dialogue."""
    return sum(1 for p in paragraphs if _is_dialogue(p))


# ── Main entry points ────────────────────────────

async def extract_scenes(
    novel_id: str,
    chapter_num: int,
) -> list[dict]:
    """Extract scenes from a single chapter. Returns cached result if available."""
    if novel_id in _scene_cache and chapter_num in _scene_cache[novel_id]:
        return _scene_cache[novel_id][chapter_num]

    # Get chapter content
    chapter = await chapter_store.get_chapter_content(novel_id, chapter_num)
    if not chapter or not chapter.get("content"):
        return []

    content = chapter["content"]
    title = chapter.get("title", f"第{chapter_num}章")

    # Get chapter fact
    all_facts = await chapter_fact_store.get_all_chapter_facts(novel_id)
    fact_data = None
    for row in all_facts:
        if row.get("chapter_id") == chapter_num:
            try:
                fact_data = json.loads(row["fact_json"]) if isinstance(row["fact_json"], str) else row["fact_json"]
            except (json.JSONDecodeError, KeyError):
                pass
            break

    scenes = _split_into_scenes(content, title, chapter_num, fact_data)

    # Cache
    if novel_id not in _scene_cache:
        _scene_cache[novel_id] = {}
    _scene_cache[novel_id][chapter_num] = scenes

    return scenes


async def get_chapter_scenes(
    novel_id: str,
    chapter_start: int,
    chapter_end: int,
) -> dict:
    """Get scenes for a range of chapters."""
    result: dict[int, list[dict]] = {}
    for ch_num in range(chapter_start, chapter_end + 1):
        scenes = await extract_scenes(novel_id, ch_num)
        if scenes:
            result[ch_num] = scenes
    return result


# ── Core splitting algorithm ─────────────────────

def _split_into_scenes(
    content: str,
    chapter_title: str,
    chapter_num: int,
    fact_data: dict | None,
) -> list[dict]:
    """Split chapter text into scenes using multi-signal boundary scoring."""
    # Split content preserving blank lines for gap detection
    raw_lines = content.split("\n")

    # Build paragraph list with original line indices (for blank-line gap detection)
    paragraphs: list[str] = []
    para_line_indices: list[int] = []  # maps para index -> original line index
    for i, line in enumerate(raw_lines):
        stripped = line.strip()
        if stripped:
            paragraphs.append(stripped)
            para_line_indices.append(i)

    if not paragraphs:
        return []

    # Collect fact data
    events = fact_data.get("events", []) if fact_data else []
    characters = fact_data.get("characters", []) if fact_data else []
    locations = fact_data.get("locations", []) if fact_data else []

    char_names = set()
    for ch in characters:
        char_names.add(ch.get("name", ""))
        char_names.update(ch.get("new_aliases", []))
    char_names.discard("")

    loc_names = [loc.get("name", "") for loc in locations if loc.get("name")]

    # Build event-to-paragraph mapping for location/participant signals
    event_locations = _map_events_to_paragraphs(events, paragraphs)

    # Compute boundary scores for each paragraph
    boundary_scores = _compute_boundary_scores(
        paragraphs, para_line_indices, raw_lines, event_locations
    )

    # Determine threshold — adaptive based on chapter length
    # Longer chapters need lower thresholds to get enough scene breaks
    if len(paragraphs) > 50:
        base_threshold = 3
    elif len(paragraphs) > 20:
        base_threshold = 4
    elif len(paragraphs) > 12:
        base_threshold = 5
    else:
        base_threshold = 6

    # Find scene break points
    break_points = _find_break_points(boundary_scores, base_threshold, min_scene_paras=3)

    # Build scenes from break points
    scenes = _build_scenes_from_breaks(
        paragraphs, break_points, chapter_num, char_names, loc_names, events
    )

    # Fallback: if still only 1 scene, try progressively lower thresholds
    if len(scenes) <= 1 and len(paragraphs) > 8:
        for drop in (1, 2):
            lower_threshold = max(base_threshold - drop, 3)
            if lower_threshold >= base_threshold:
                continue
            lower_breaks = _find_break_points(boundary_scores, lower_threshold, min_scene_paras=3)
            if len(lower_breaks) > len(break_points):
                scenes = _build_scenes_from_breaks(
                    paragraphs, lower_breaks, chapter_num, char_names, loc_names, events
                )
                break

    # Merge trivial trailing scenes (chapter ending phrases like "且听下回分解")
    if len(scenes) > 1:
        last = scenes[-1]
        pr = last.get("paragraph_range", [0, 0])
        last_para_count = pr[1] - pr[0] + 1
        if last_para_count <= 2:
            # Check if last scene is just closing text
            last_text = "\n".join(paragraphs[pr[0]:pr[1] + 1])
            if _CHAPTER_ENDING.search(last_text) or last_para_count == 1:
                # Merge into previous scene
                prev = scenes[-2]
                prev_pr = prev.get("paragraph_range", [0, 0])
                prev["paragraph_range"] = [prev_pr[0], pr[1]]
                # Update dialogue count
                extra_paras = paragraphs[prev_pr[1] + 1:pr[1] + 1]
                prev["dialogue_count"] = prev.get("dialogue_count", 0) + _count_dialogue(extra_paras)
                scenes.pop()

    return scenes


def _compute_boundary_scores(
    paragraphs: list[str],
    para_line_indices: list[int],
    raw_lines: list[str],
    event_locations: list[tuple[str, set[str]]],
) -> list[float]:
    """Compute a boundary score for each paragraph. Higher = stronger break signal."""
    n = len(paragraphs)
    scores = [0.0] * n

    # Track dialogue mode for cluster boundary detection
    is_dialogue_list = [_is_dialogue(p) for p in paragraphs]

    for i in range(n):
        para = paragraphs[i]
        score = 0.0

        # Signal 1: Narrator transition (weight 5)
        if _NARRATOR_TRANSITION.search(para):
            score += 5

        # Signal 2: Previous paragraph has scene closure (weight 4)
        if i > 0 and _SCENE_CLOSURE.search(paragraphs[i - 1]):
            score += 4

        # Signal 3: Time jump (weight 4)
        if _TIME_JUMP.search(para):
            score += 4

        # Signal 4: Scene opening phrase (weight 3)
        if _SCENE_OPENING.search(para):
            score += 3

        # Signal 4b: Weak time signal at start (weight 2)
        if _TIME_WEAK.search(para):
            score += 2

        # Signal 4c: Internal time jump in first 50 chars (weight 3)
        # Only if the ^ time jump didn't already fire
        if not _TIME_JUMP.search(para) and _INTERNAL_TIME_JUMP.search(para[:60]):
            score += 3

        # Signal 5: Blank-line gap (weight 3) — 2+ blank lines before this paragraph
        if i > 0:
            current_line_idx = para_line_indices[i]
            prev_line_idx = para_line_indices[i - 1]
            blank_count = current_line_idx - prev_line_idx - 1
            if blank_count >= 2:
                score += 3

        # Signal 6: Dialogue cluster boundary (weight 2)
        # Transition from ≥3 consecutive dialogue to ≥3 consecutive narration (or vice versa)
        if i >= 3:
            # Check if previous 3 paras were all dialogue and current starts narration streak
            prev_dialogue = all(is_dialogue_list[i - j] for j in range(1, 4))
            if prev_dialogue and not is_dialogue_list[i]:
                # Check forward: at least 2 more narration lines
                fwd_narration = sum(
                    1 for j in range(i, min(i + 3, n)) if not is_dialogue_list[j]
                )
                if fwd_narration >= 2:
                    score += 2

            prev_narration = all(not is_dialogue_list[i - j] for j in range(1, 4))
            if prev_narration and is_dialogue_list[i]:
                fwd_dialogue = sum(
                    1 for j in range(i, min(i + 3, n)) if is_dialogue_list[j]
                )
                if fwd_dialogue >= 2:
                    score += 2

        # Signal 7 & 8: Location and participant changes from events (weight 2 + 1)
        if event_locations and i < len(event_locations):
            cur_loc, cur_parts = event_locations[i]
            if i > 0:
                prev_loc, prev_parts = event_locations[i - 1]
                if cur_loc and prev_loc and cur_loc != prev_loc:
                    score += 2  # Location change
                if cur_parts and prev_parts:
                    overlap = len(cur_parts & prev_parts)
                    total = len(cur_parts | prev_parts)
                    if total > 0 and overlap / total < 0.4:
                        score += 1  # Significant participant change

        scores[i] = score

    return scores


def _map_events_to_paragraphs(
    events: list[dict],
    paragraphs: list[str],
) -> list[tuple[str, set[str]]]:
    """Map each paragraph to the nearest event's location and participants.

    Returns a list parallel to paragraphs: (location, participant_set).
    """
    if not events:
        return []

    # For each event, find which paragraph it best matches (by keyword overlap)
    event_para_map: list[tuple[int, dict]] = []
    for evt in events:
        summary = evt.get("summary", "")
        participants = evt.get("participants", [])
        # Find best matching paragraph
        best_idx = 0
        best_score = 0
        keywords = set(summary) | set("".join(participants))
        for i, p in enumerate(paragraphs):
            overlap = len(keywords & set(p))
            if overlap > best_score:
                best_score = overlap
                best_idx = i
        event_para_map.append((best_idx, evt))

    # Sort by paragraph index
    event_para_map.sort(key=lambda x: x[0])

    # Build per-paragraph location/participant info (propagate from nearest event)
    result: list[tuple[str, set[str]]] = [("", set()) for _ in paragraphs]
    cur_loc = ""
    cur_parts: set[str] = set()

    evt_idx = 0
    for i in range(len(paragraphs)):
        while evt_idx < len(event_para_map) and event_para_map[evt_idx][0] <= i:
            evt = event_para_map[evt_idx][1]
            loc = evt.get("location") or ""
            if loc:
                cur_loc = loc
            parts = set(evt.get("participants", []))
            if parts:
                cur_parts = parts
            evt_idx += 1
        result[i] = (cur_loc, set(cur_parts))

    return result


def _find_break_points(
    scores: list[float],
    threshold: float,
    min_scene_paras: int = 3,
) -> list[int]:
    """Find paragraph indices where scene breaks should occur.

    A break at index i means a new scene starts at paragraph i.
    Always includes 0 (the first paragraph starts the first scene).

    For very strong boundaries (score >= 7, e.g. narrator transitions like 却说),
    the minimum scene distance is relaxed to 2 paragraphs, since a short scene
    between two strong transitions is a valid narrative unit.
    """
    breaks = [0]

    for i in range(1, len(scores)):
        if scores[i] >= threshold:
            # Strong boundaries allow shorter preceding scenes
            min_dist = 2 if scores[i] >= 7 else min_scene_paras
            if i - breaks[-1] >= min_dist:
                breaks.append(i)

    return breaks


def _build_scenes_from_breaks(
    paragraphs: list[str],
    break_points: list[int],
    chapter_num: int,
    char_names: set[str],
    loc_names: list[str],
    events: list[dict],
) -> list[dict]:
    """Build scene dicts from break point indices."""
    scenes = []

    for idx, start in enumerate(break_points):
        end = break_points[idx + 1] if idx + 1 < len(break_points) else len(paragraphs)
        scene_paras = paragraphs[start:end]

        scene = _build_rich_scene(
            index=idx,
            chapter_num=chapter_num,
            paragraphs=scene_paras,
            paragraph_range=[start, end - 1],
            char_names=char_names,
            loc_names=loc_names,
            events=events,
            all_paragraphs=paragraphs,
        )
        scenes.append(scene)

    return scenes


# ── Rich scene metadata builders ─────────────────

def _build_rich_scene(
    index: int,
    chapter_num: int,
    paragraphs: list[str],
    paragraph_range: list[int],
    char_names: set[str],
    loc_names: list[str],
    events: list[dict],
    all_paragraphs: list[str],
) -> dict:
    """Build a scene dict with rich metadata."""
    text = "\n".join(paragraphs)

    # --- Characters present in this scene ---
    present_chars = [c for c in char_names if c in text]

    # --- Character roles (主/配/提及) ---
    character_roles = _classify_character_roles(paragraphs, present_chars)

    # --- Location ---
    scene_loc = ""
    for loc in loc_names:
        if loc in text:
            scene_loc = loc
            break

    # --- Heading (first non-dialogue sentence, truncated) ---
    heading = _extract_heading(paragraphs)

    # --- Title (from heading or fallback) ---
    title = heading if heading else f"场景 {index + 1}"

    # --- Time of day ---
    time_of_day = _detect_time_of_day(text)

    # --- Emotional tone ---
    emotional_tone = _detect_emotional_tone(text)

    # --- Key dialogue (1-2 most informative dialogue lines) ---
    key_dialogue = _extract_key_dialogue(paragraphs)

    # --- Event type ---
    event_type = _classify_event_type(paragraphs, events, paragraph_range, all_paragraphs)

    # --- Description (first paragraph, truncated) ---
    description = paragraphs[0][:100] if paragraphs else ""

    # --- Dialogue count ---
    dialogue_count = _count_dialogue(paragraphs)

    # --- Events in this scene range ---
    scene_events = _get_events_in_range(events, paragraph_range, all_paragraphs)

    return {
        "index": index,
        "chapter": chapter_num,
        "title": title,
        "location": scene_loc,
        "characters": [cr["name"] for cr in character_roles][:10],
        "description": description,
        "dialogue_count": dialogue_count,
        "paragraph_range": paragraph_range,
        "events": scene_events,
        # New rich metadata
        "heading": heading,
        "time_of_day": time_of_day,
        "emotional_tone": emotional_tone,
        "key_dialogue": key_dialogue,
        "character_roles": character_roles[:10],
        "event_type": event_type,
    }


def _extract_heading(paragraphs: list[str]) -> str:
    """Extract scene heading from first non-dialogue paragraph."""
    for p in paragraphs[:5]:
        if not _is_dialogue(p):
            # Take first sentence or first 20 chars
            # Split by Chinese punctuation
            for sep in ("。", "，", "；", "！", "？"):
                idx = p.find(sep)
                if 0 < idx <= 25:
                    return p[:idx]
            return p[:20]
    return ""


def _detect_time_of_day(text: str) -> str:
    """Detect time of day from text content."""
    # Check a limited prefix to avoid false matches deep in text
    check_text = text[:200]
    if _TIME_MORNING.search(check_text):
        return "早"
    if _TIME_NOON.search(check_text):
        return "午"
    if _TIME_EVENING.search(check_text):
        return "晚"
    if _TIME_NIGHT.search(check_text):
        return "夜"
    return ""


def _detect_emotional_tone(text: str) -> str:
    """Detect dominant emotional tone from text content."""
    counts: dict[str, int] = {
        "战斗": len(_TONE_BATTLE.findall(text)),
        "悲伤": len(_TONE_SAD.findall(text)),
        "欢乐": len(_TONE_HAPPY.findall(text)),
        "紧张": len(_TONE_TENSE.findall(text)),
    }
    max_tone = max(counts, key=counts.get)  # type: ignore[arg-type]
    if counts[max_tone] >= 3:
        return max_tone
    return "平静"


def _extract_key_dialogue(paragraphs: list[str]) -> list[str]:
    """Extract 1-2 most informative dialogue lines from paragraphs."""
    dialogues: list[str] = []
    for p in paragraphs:
        if _is_dialogue(p) and len(p) >= 8:
            dialogues.append(p)

    if not dialogues:
        return []

    # Sort by length (longer = more informative), take top 2
    dialogues.sort(key=len, reverse=True)
    result = []
    for d in dialogues[:2]:
        # Truncate very long dialogue
        if len(d) > 60:
            d = d[:57] + "..."
        result.append(d)
    return result


def _classify_character_roles(
    paragraphs: list[str],
    present_chars: list[str],
) -> list[dict]:
    """Classify characters as 主 (lead), 配 (supporting), or 提及 (mentioned).

    - 主: appears in ≥3 paragraphs OR has dialogue
    - 配: appears in ≥2 paragraphs
    - 提及: appears only once
    """
    if not present_chars:
        return []

    text = "\n".join(paragraphs)
    dialogue_text = "\n".join(p for p in paragraphs if _is_dialogue(p))

    char_scores: list[tuple[str, int, str]] = []  # (name, frequency, role)
    for name in present_chars:
        freq = text.count(name)
        in_dialogue = name in dialogue_text
        para_count = sum(1 for p in paragraphs if name in p)

        if para_count >= 3 or in_dialogue:
            role = "主"
        elif para_count >= 2:
            role = "配"
        else:
            role = "提及"

        char_scores.append((name, freq, role))

    # Sort: 主 first, then 配, then 提及; within each group by frequency desc
    role_order = {"主": 0, "配": 1, "提及": 2}
    char_scores.sort(key=lambda x: (role_order[x[2]], -x[1]))

    return [{"name": name, "role": role} for name, _, role in char_scores]


def _classify_event_type(
    paragraphs: list[str],
    events: list[dict],
    paragraph_range: list[int],
    all_paragraphs: list[str],
) -> str:
    """Classify the scene type: 对话/战斗/旅行/描写/回忆."""
    text = "\n".join(paragraphs)
    dialogue_ratio = _count_dialogue(paragraphs) / max(len(paragraphs), 1)

    # Check for battle keywords (stricter regex requires compound words)
    battle_score = len(_TONE_BATTLE.findall(text))
    if battle_score >= 3:
        return "战斗"

    # Check for travel keywords
    travel_pattern = re.compile(
        r"[行走赶奔飞骑]了|一路|赶路|启程|上路|动身|出发|前[行往进]|奔[向往]"
    )
    travel_score = len(travel_pattern.findall(text))
    if travel_score >= 3:
        return "旅行"

    # Check for flashback/memory keywords (require ≥2 matches to avoid false positives)
    # Note: 当日 excluded — it means "on that day" (neutral temporal), not a flashback
    memory_pattern = re.compile(
        r"想[起当到]|回忆|当[年初]|从前|往[日事昔]|昔[日年]|记得|犹记"
    )
    memory_score = len(memory_pattern.findall(text))
    if memory_score >= 2 and dialogue_ratio < 0.3:
        return "回忆"

    # High dialogue ratio
    if dialogue_ratio >= 0.5:
        return "对话"

    # Default to description
    return "描写"


def _get_events_in_range(
    events: list[dict],
    paragraph_range: list[int],
    all_paragraphs: list[str],
) -> list[dict]:
    """Get events that likely belong to this scene's paragraph range."""
    if not events:
        return []

    # Simple heuristic: match events by finding their summary text in scene paragraphs
    scene_text = "\n".join(all_paragraphs[paragraph_range[0]:paragraph_range[1] + 1])
    result = []
    for evt in events:
        summary = evt.get("summary", "")
        participants = evt.get("participants", [])
        # Check if any participant or key summary words appear in scene text
        if any(p in scene_text for p in participants if p):
            result.append({"summary": summary, "type": evt.get("type", "")})
        elif summary and any(kw in scene_text for kw in summary[:10]):
            result.append({"summary": summary, "type": evt.get("type", "")})

    return result[:5]  # Limit to 5 events per scene
