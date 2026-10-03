"""路由冒烟测试:chat / conflicts / encyclopedia / factions / timeline /
usage / world_structure / scenes / prescan。

目标是「路由接线正确、序列化不炸」:每个路由打 1-2 个核心端点的
happy path + 一个 404/校验错误路径,不做深业务断言(业务层已有测试)。

真实 app + httpx ASGITransport;DB 走 conftest 的 api_client fixture
(memory_db),无真实 LLM / 文件系统访问。
"""

import json

import pytest

NOVEL = "novel-smoke"


async def _seed_novel(db, with_chapter=False):
    await db.execute(
        "INSERT INTO novels (id, title, total_chapters) VALUES (?, '冒烟小说', 1)",
        (NOVEL,),
    )
    if with_chapter:
        await db.execute(
            "INSERT INTO chapters (id, novel_id, chapter_num, title, content,"
            " analysis_status) VALUES (1, ?, 1, '第1章', ?, 'completed')",
            (NOVEL, "却说那宋江与武松在柴进庄上吃酒。\n\n次日二人辞别柴进,各自上路。"),
        )
    await db.commit()


# ── chat ──


@pytest.mark.asyncio
async def test_chat_conversations_crud(api_client):
    client, db = api_client
    await _seed_novel(db)

    resp = await client.get(f"/api/novels/{NOVEL}/conversations")
    assert resp.status_code == 200
    assert resp.json() == {"conversations": []}

    resp = await client.post(
        f"/api/novels/{NOVEL}/conversations", json={"title": "测试对话"},
    )
    assert resp.status_code == 200
    conv = resp.json()
    assert conv["title"] == "测试对话"

    resp = await client.get(f"/api/conversations/{conv['id']}/messages")
    assert resp.status_code == 200
    assert resp.json()["messages"] == []

    resp = await client.delete(f"/api/conversations/{conv['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_chat_404s(api_client):
    client, _db = api_client
    resp = await client.get("/api/novels/no-such-novel/conversations")
    assert resp.status_code == 404
    resp = await client.delete("/api/conversations/no-such-conv")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "对话不存在"


# ── conflicts ──


@pytest.mark.asyncio
async def test_conflicts_happy_empty(api_client):
    client, db = api_client
    await _seed_novel(db)

    resp = await client.get(f"/api/novels/{NOVEL}/conflicts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["conflicts"] == []
    assert set(body["severity_counts"]) == {"严重", "一般", "提示"}


@pytest.mark.asyncio
async def test_conflicts_novel_not_found(api_client):
    client, _db = api_client
    resp = await client.get("/api/novels/no-such-novel/conflicts")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "小说不存在"


# ── encyclopedia ──


@pytest.mark.asyncio
async def test_encyclopedia_stats_and_entries(api_client):
    client, db = api_client
    await _seed_novel(db)

    resp = await client.get(f"/api/novels/{NOVEL}/encyclopedia")
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)

    resp = await client.get(f"/api/novels/{NOVEL}/encyclopedia/entries")
    assert resp.status_code == 200
    assert resp.json() == {"entries": []}


@pytest.mark.asyncio
async def test_encyclopedia_404s(api_client):
    client, db = api_client
    await _seed_novel(db)

    resp = await client.get("/api/novels/no-such-novel/encyclopedia")
    assert resp.status_code == 404

    resp = await client.get(f"/api/novels/{NOVEL}/encyclopedia/不存在的概念")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "概念不存在"


# ── factions ──


@pytest.mark.asyncio
async def test_factions_happy_no_facts(api_client):
    client, db = api_client
    await _seed_novel(db)

    resp = await client.get(f"/api/novels/{NOVEL}/factions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["orgs"] == []
    assert body["relations"] == []
    assert body["analyzed_range"] == [0, 0]


@pytest.mark.asyncio
async def test_factions_novel_not_found(api_client):
    client, _db = api_client
    resp = await client.get("/api/novels/no-such-novel/factions")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "小说不存在"


# ── timeline ──


@pytest.mark.asyncio
async def test_timeline_happy_no_facts(api_client):
    client, db = api_client
    await _seed_novel(db)

    resp = await client.get(f"/api/novels/{NOVEL}/timeline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["events"] == []
    assert body["analyzed_range"] == [0, 0]
    assert body["total_swimlanes"] == 0


@pytest.mark.asyncio
async def test_timeline_novel_not_found(api_client):
    client, _db = api_client
    resp = await client.get("/api/novels/no-such-novel/timeline")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "小说不存在"


# ── usage ──


@pytest.mark.asyncio
async def test_usage_track_and_stats(api_client):
    client, _db = api_client

    resp = await client.post(
        "/api/usage/track",
        json={"event_type": "smoke_event", "metadata": {"k": "v"}},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    resp = await client.get("/api/usage/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_events"] == 1
    assert body["days"] == 30
    assert isinstance(body["daily_trend"], list)

    resp = await client.delete("/api/usage/clear")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["deleted"] == 1


@pytest.mark.asyncio
async def test_usage_stats_days_validation(api_client):
    client, _db = api_client
    resp = await client.get("/api/usage/stats?days=0")
    assert resp.status_code == 422
    resp = await client.get("/api/usage/stats?days=999")
    assert resp.status_code == 422


# ── world_structure ──


@pytest.mark.asyncio
async def test_world_structure_get_default(api_client):
    """无 world_structures 行 → 返回默认结构(model_dump 不炸)。"""
    client, db = api_client
    await _seed_novel(db)

    resp = await client.get(f"/api/novels/{NOVEL}/world-structure")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, dict)
    assert body["novel_id"] == NOVEL

    resp = await client.get(f"/api/novels/{NOVEL}/world-structure/overrides")
    assert resp.status_code == 200
    assert resp.json() == {"overrides": []}


@pytest.mark.asyncio
async def test_world_structure_errors(api_client):
    client, db = api_client
    await _seed_novel(db)

    resp = await client.get("/api/novels/no-such-novel/world-structure")
    assert resp.status_code == 404

    resp = await client.put(
        f"/api/novels/{NOVEL}/world-structure/overrides",
        json={"overrides": [
            {"override_type": "bogus_type", "override_key": "x",
             "override_json": {}},
        ]},
    )
    assert resp.status_code == 400
    assert "override_type" in resp.json()["detail"]


# ── scenes ──


@pytest.mark.asyncio
async def test_scenes_single_chapter_rule_fallback(api_client):
    """无 LLM scenes_json → 走 rule-based 兜底,响应结构完整。"""
    client, db = api_client
    await _seed_novel(db, with_chapter=True)

    resp = await client.get(f"/api/novels/{NOVEL}/scenes/1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["chapter"] == 1
    assert body["source"] == "rule"
    assert body["scene_count"] == len(body["scenes"])


@pytest.mark.asyncio
async def test_scenes_llm_source(api_client):
    """chapter_facts.scenes_json 存在 → 优先返回 LLM scenes。"""
    client, db = api_client
    await _seed_novel(db, with_chapter=True)
    scenes = [{"scene_id": 1, "summary": "吃酒", "location": "柴进庄",
               "participants": ["宋江", "武松"]}]
    await db.execute(
        "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json,"
        " scenes_json) VALUES (?, 1, '{}', ?)",
        (NOVEL, json.dumps(scenes, ensure_ascii=False)),
    )
    await db.commit()

    resp = await client.get(f"/api/novels/{NOVEL}/scenes/1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "llm"
    assert body["scene_count"] == 1
    assert body["scenes"][0]["location"] == "柴进庄"


@pytest.mark.asyncio
async def test_scenes_errors(api_client):
    client, db = api_client
    await _seed_novel(db)

    resp = await client.get("/api/novels/no-such-novel/scenes/1")
    assert resp.status_code == 404

    # 范围端点缺必填 query 参数 → 422
    resp = await client.get(f"/api/novels/{NOVEL}/scenes")
    assert resp.status_code == 422


# ── prescan ──


@pytest.mark.asyncio
async def test_prescan_status_and_dictionary(api_client):
    client, db = api_client
    await _seed_novel(db)

    resp = await client.get(f"/api/novels/{NOVEL}/prescan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["entity_count"] == 0

    resp = await client.get(f"/api/novels/{NOVEL}/entity-dictionary")
    assert resp.status_code == 200
    assert resp.json() == {"data": [], "total": 0}


@pytest.mark.asyncio
async def test_prescan_dictionary_with_entry(api_client):
    """词典有序条目 → 响应模型序列化(EntityDictItem)不炸。"""
    client, db = api_client
    await _seed_novel(db)
    await db.execute(
        "INSERT INTO entity_dictionary (novel_id, name, entity_type,"
        " frequency, confidence, aliases, source, sample_context)"
        " VALUES (?, '宋江', 'person', 10, 'high', '[\"及时雨\"]', 'prescan',"
        " '宋江道……')",
        (NOVEL,),
    )
    await db.commit()

    resp = await client.get(f"/api/novels/{NOVEL}/entity-dictionary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["data"][0]
    assert item["name"] == "宋江"
    assert item["aliases"] == ["及时雨"]
    assert item["entity_type"] == "person"


@pytest.mark.asyncio
async def test_prescan_novel_not_found(api_client):
    client, _db = api_client
    resp = await client.get("/api/novels/no-such-novel/prescan")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "小说不存在"
    resp = await client.get("/api/novels/no-such-novel/entity-dictionary")
    assert resp.status_code == 404
