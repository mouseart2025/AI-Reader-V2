"""Generate pre-analyzed sample novel data packages for first-launch experience.

Usage:
    uv run python scripts/generate_sample_data.py \
        --input sample-novels/xiyouji-25.txt \
        --title "西游记（前25回）" \
        --author "吴承恩" \
        --output ../frontend/public/sample-data/xiyouji.json \
        --skip-content \
        [--model qwen3:14b]

The script performs: TXT → split → DB insert → prescan → analysis → export v2 JSON.
Requires Ollama running with the specified model (default: qwen3:8b).
"""

import argparse
import asyncio
import hashlib
import json
import sys
import time
import uuid

# Add backend src to path
sys.path.insert(0, ".")

from src.db.sqlite_db import init_db, get_connection
from src.db import novel_store, analysis_task_store
from src.extraction.entity_pre_scanner import EntityPreScanner
from src.services.analysis_service import get_analysis_service
from src.services.export_service import export_novel
from src.utils.chapter_splitter import split_chapters_ex
from src.utils.text_processor import decode_text


async def wait_for_analysis(task_id: str, novel_id: str, total: int) -> None:
    """Poll analysis task status until completed or failed."""
    while True:
        task = await analysis_task_store.get_task(task_id)
        if not task:
            raise RuntimeError(f"Task {task_id} disappeared")

        status = task["status"]
        current = task.get("current_chapter") or 0

        if status == "completed":
            print(f"\r  分析完成: {total}/{total} 章节", flush=True)
            return
        elif status in ("failed", "cancelled"):
            raise RuntimeError(f"分析失败: status={status}")
        else:
            print(f"\r  分析中: {current}/{total} 章节 (status={status})", end="", flush=True)
            await asyncio.sleep(2)


async def main(args: argparse.Namespace) -> None:
    # Initialize database
    await init_db()
    print(f"数据库初始化完成")

    # Read and decode TXT file
    with open(args.input, "rb") as f:
        raw = f.read()
    text = decode_text(raw)
    file_hash = hashlib.sha256(raw).hexdigest()
    print(f"读取文件: {args.input} ({len(text)} 字)")

    # Split chapters
    result = split_chapters_ex(text)
    chapters = result.chapters
    if args.chapters:
        chapters = chapters[: args.chapters]
    total_words = sum(ch.word_count for ch in chapters)
    print(f"切分完成: {len(chapters)} 章节, {total_words} 字 (模式: {result.matched_mode})")

    # Insert novel into DB
    novel_id = str(uuid.uuid4())
    await novel_store.insert_novel(
        novel_id=novel_id,
        title=args.title,
        author=args.author,
        file_hash=file_hash,
        total_chapters=len(chapters),
        total_words=total_words,
    )
    await novel_store.insert_chapters(novel_id, chapters)
    print(f"写入数据库: novel_id={novel_id}")

    # Entity pre-scan
    print("开始实体预扫描...")
    scanner = EntityPreScanner()
    entities = await scanner.scan(novel_id)
    print(f"预扫描完成: {len(entities)} 个实体")

    # Update prescan_status
    conn = await get_connection()
    try:
        await conn.execute(
            "UPDATE novels SET prescan_status = 'completed' WHERE id = ?", (novel_id,)
        )
        await conn.commit()
    finally:
        await conn.close()

    # Run full analysis
    print("开始章节分析...")
    service = get_analysis_service()
    task_id = await service.start(novel_id, 1, len(chapters))
    await wait_for_analysis(task_id, novel_id, len(chapters))

    # Export as v2 JSON
    print("导出数据包...")
    data = await export_novel(novel_id, skip_content=args.skip_content)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    file_size = len(open(args.output, "rb").read())
    print(f"\n{'='*50}")
    print(f"导出完成: {args.output}")
    print(f"  格式版本: {data['format_version']}")
    print(f"  小说: {data['novel']['title']}")
    print(f"  章节数: {len(data['chapters'])}")
    print(f"  ChapterFact: {len(data['chapter_facts'])}")
    print(f"  实体词典: {len(data['entity_dictionary'])} 条")
    print(f"  世界结构: {'有' if data['world_structures'] else '无'}")
    print(f"  文件大小: {file_size / 1024:.1f} KB")
    print(f"{'='*50}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成样本小说预分析数据包")
    parser.add_argument("--input", required=True, help="TXT 小说文件路径")
    parser.add_argument("--title", required=True, help="小说标题")
    parser.add_argument("--author", default=None, help="作者名")
    parser.add_argument("--output", required=True, help="输出 JSON 文件路径")
    parser.add_argument("--chapters", type=int, default=None, help="仅处理前 N 章")
    parser.add_argument("--model", default=None, help="Ollama 模型名（覆盖环境变量）")
    parser.add_argument(
        "--skip-content",
        action="store_true",
        help="省略章节原文以减小体积",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.model:
        import os
        os.environ["OLLAMA_MODEL"] = args.model
    asyncio.run(main(args))
