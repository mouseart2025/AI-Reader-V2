"""Test scene extraction on real 西游记 chapter text.

Run: cd backend && uv run python test_scene_split.py
"""
import sys
sys.path.insert(0, ".")

from src.services.scene_extractor import (
    _split_into_scenes,
    _compute_boundary_scores,
    _find_break_points,
    _is_dialogue,
)


def load_chapters(path: str) -> list[tuple[str, str]]:
    """Split novel file into chapters. Returns [(title, content), ...]"""
    import re
    with open(path, encoding="utf-8") as f:
        text = f.read()

    # Split by chapter headers (第X回)
    pattern = re.compile(r"(?:上卷|中卷|下卷)?\s*第([一二三四五六七八九十百零\d]+)回\s+(.+?)(?:\n|$)")
    chapters = []
    positions = []
    for m in pattern.finditer(text):
        positions.append((m.start(), m.group(0).strip()))

    for i, (pos, title) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        content = text[pos + len(title):end].strip()
        chapters.append((title, content))

    return chapters


def analyze_chapter(title: str, content: str, chapter_num: int):
    """Run scene extraction and print detailed analysis."""
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}")

    # Run extraction (no fact_data for pure text test)
    scenes = _split_into_scenes(content, title, chapter_num, fact_data=None)

    # Also compute raw boundary scores for debugging
    raw_lines = content.split("\n")
    paragraphs = []
    para_line_indices = []
    for i, line in enumerate(raw_lines):
        stripped = line.strip()
        if stripped:
            paragraphs.append(stripped)
            para_line_indices.append(i)

    scores = _compute_boundary_scores(paragraphs, para_line_indices, raw_lines, [])

    # Show high-scoring boundaries
    print(f"\n总段落数: {len(paragraphs)}")
    print(f"检测到场景数: {len(scenes)}")

    print(f"\n--- 高分边界信号 (score >= 3) ---")
    for i, s in enumerate(scores):
        if s >= 3:
            preview = paragraphs[i][:50].replace("\n", " ")
            print(f"  段落 {i:3d} | 得分 {s:.0f} | {preview}...")

    print(f"\n--- 场景切分结果 ---")
    for scene in scenes:
        pr = scene.get("paragraph_range", [0, 0])
        char_roles = scene.get("character_roles", [])
        leads = [cr["name"] for cr in char_roles if cr["role"] == "主"]
        supporting = [cr["name"] for cr in char_roles if cr["role"] == "配"]

        print(f"\n  场景 {scene['index']+1}: {scene['title']}")
        print(f"    段落范围: {pr[0]}-{pr[1]} ({pr[1]-pr[0]+1} 段)")
        if scene.get("location"):
            print(f"    地点: {scene['location']}")
        if scene.get("time_of_day"):
            print(f"    时间: {scene['time_of_day']}")
        if scene.get("emotional_tone"):
            print(f"    氛围: {scene['emotional_tone']}")
        if scene.get("event_type"):
            print(f"    类型: {scene['event_type']}")
        if leads:
            print(f"    主要人物: {', '.join(leads)}")
        if supporting:
            print(f"    配角: {', '.join(supporting)}")
        if scene.get("key_dialogue"):
            for d in scene["key_dialogue"]:
                print(f"    对话: {d[:50]}...")
        print(f"    对话段数: {scene['dialogue_count']}")

        # Show first 2 paragraphs of scene
        start_p = pr[0]
        end_p = min(pr[0] + 2, pr[1] + 1)
        for pi in range(start_p, end_p):
            if pi < len(paragraphs):
                print(f"    >>> {paragraphs[pi][:60]}...")

    return scenes


def main():
    path = "sample-novels/xiyouji.txt"
    chapters = load_chapters(path)

    if not chapters:
        print("未找到章节！")
        return

    print(f"找到 {len(chapters)} 章")

    # Aggregate stats across all chapters
    print("\n=== 全量统计 ===")
    all_scene_counts = []
    for idx in range(len(chapters)):
        title, content = chapters[idx]
        scenes = _split_into_scenes(content, title, idx + 1, fact_data=None)
        all_scene_counts.append(len(scenes))

    avg_scenes = sum(all_scene_counts) / len(all_scene_counts)
    print(f"  总章数: {len(chapters)}")
    print(f"  平均场景数/章: {avg_scenes:.1f}")
    print(f"  场景数范围: {min(all_scene_counts)}-{max(all_scene_counts)}")

    from collections import Counter
    dist = Counter(all_scene_counts)
    print(f"\n  场景数分布:")
    for k in sorted(dist):
        print(f"    {k} 场景: {'█' * dist[k]} ({dist[k]})")

    # Show detailed results for sample chapters
    print("\n=== 详细样例 ===")
    for idx in [0, 1, 2]:
        if idx < len(chapters):
            title, content = chapters[idx]
            analyze_chapter(title, content, idx + 1)

    print("\n" + "=" * 80)
    print("测试完成")


if __name__ == "__main__":
    main()
