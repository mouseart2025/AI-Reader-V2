"""Per-chapter cost/duration report (v0.85).

`chapter_facts.chapter_id` is the DB row id, not the chapter number. The cost
detail used to display it as "第 N 章"; the report now joins `chapters` and
returns chapter_num/title plus total/avg duration.
"""

import json

import pytest


@pytest.mark.asyncio
async def test_cost_detail_uses_chapter_num_and_durations(api_client):
    client, db = api_client
    await db.execute("INSERT INTO novels (id, title) VALUES ('n-cost', '测试书')")
    for num, title, ms, cost in [
        (1, "第一章", 1200, 0.002),
        (2, "第二章", 800, 0.001),
        (3, "第三章", 0, 0.0),
    ]:
        cur = await db.execute(
            "INSERT INTO chapters (novel_id, chapter_num, title, content) "
            "VALUES ('n-cost', ?, ?, '正文')",
            (num, title),
        )
        chapter_id = cur.lastrowid
        fact = {"chapter_id": num, "novel_id": "n-cost", "locations": [], "events": []}
        await db.execute(
            "INSERT INTO chapter_facts "
            "(novel_id, chapter_id, fact_json, input_tokens, output_tokens, "
            " cost_usd, cost_cny, extraction_ms, llm_model) "
            "VALUES ('n-cost', ?, ?, 1000, 200, ?, ?, ?, 'test-model')",
            (
                chapter_id, json.dumps(fact), cost, cost * 7.2, ms,
            ),
        )
    await db.execute(
        "INSERT INTO analysis_tasks (id, novel_id, status, chapter_start, chapter_end) "
        "VALUES ('task-cost', 'n-cost', 'completed', 1, 2)"
    )
    await db.commit()

    resp = await client.get("/api/novels/n-cost/analysis/cost-detail")
    assert resp.status_code == 200
    data = resp.json()

    assert [c["chapter_num"] for c in data["chapters"]] == [1, 2, 3]
    assert data["chapters"][0]["chapter_title"] == "第一章"
    assert data["chapters"][0]["extraction_ms"] == 1200
    assert data["summary"]["total_ms"] == 2000
    assert data["summary"]["avg_chapter_ms"] == 1000
    assert data["summary"]["max_chapter_ms"] == 1200

    csv_resp = await client.get("/api/novels/n-cost/analysis/cost-detail/csv")
    assert csv_resp.status_code == 200
    text = csv_resp.text
    assert "标题" in text
    assert "第一章" in text
    assert "1200" in text

    records_resp = await client.get("/api/settings/analysis-records")
    assert records_resp.status_code == 200
    rec = next(
        r for r in records_resp.json()["records"] if r["task_id"] == "task-cost"
    )
    # Range filter must use chapter_num, not the DB row id.
    assert rec["chapter_count"] == 2
    assert rec["total_ms"] == 2000
    assert rec["avg_chapter_ms"] == 1000
