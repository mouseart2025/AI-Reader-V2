"""zh_wiki_geo.sqlite 点查询层测试。

wiki 索引从「TSV 全量载入内存 dict（~400MB RSS）」改为 SQLite 点查询：
  - 仅在 world 数据集解析时按地名单条查询，阅读/中文小说路径零开销
  - 打包后文件在 Tauri resource 目录（AI_READER_GEO_DIR），不在 onefile 内
  - 查询优先级不变：wiki（编辑者策展）> GeoNames 众包别名
"""

import sqlite3

import pytest

from src.services import geo_resolver as gr


def _pack(lat: float, lng: float) -> int:
    return (int((lat + 90.0) * 1_000_000) << 32) | int((lng + 180.0) * 1_000_000)


def _make_wiki_db(path, rows: dict[str, tuple[float, float]]) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE geo (name TEXT PRIMARY KEY, coord INTEGER NOT NULL) WITHOUT ROWID"
    )
    con.executemany(
        "INSERT INTO geo VALUES (?,?)", [(n, _pack(*c)) for n, c in rows.items()]
    )
    con.commit()
    con.close()


@pytest.fixture()
def wiki_db(tmp_path, monkeypatch):
    """临时 geo 数据目录（AI_READER_GEO_DIR），重置 wiki 连接缓存。"""
    monkeypatch.setenv("AI_READER_GEO_DIR", str(tmp_path))
    monkeypatch.setattr(gr, "_wiki_geo_conn", None)
    monkeypatch.setattr(gr, "_wiki_geo_unavailable", False)
    yield tmp_path
    if gr._wiki_geo_conn is not None:
        gr._wiki_geo_conn.close()
        gr._wiki_geo_conn = None
    gr._wiki_geo_unavailable = False


def test_wiki_lookup_hit(wiki_db):
    _make_wiki_db(wiki_db / "zh_wiki_geo.sqlite", {"涅瓦河": (59.95667, 31.04556)})
    lat, lng = gr._wiki_geo_lookup("涅瓦河")
    assert lat == pytest.approx(59.95667, abs=1e-5)
    assert lng == pytest.approx(31.04556, abs=1e-5)


def test_wiki_lookup_miss_returns_none(wiki_db):
    _make_wiki_db(wiki_db / "zh_wiki_geo.sqlite", {"涅瓦河": (59.95667, 31.04556)})
    assert gr._wiki_geo_lookup("不存在的地名") is None


def test_wiki_db_missing_disables_layer(wiki_db):
    assert gr._wiki_geo_lookup("涅瓦河") is None
    assert gr._wiki_geo_unavailable is True
    # 标记后不再重复探测文件系统
    _make_wiki_db(wiki_db / "zh_wiki_geo.sqlite", {"涅瓦河": (59.95667, 31.04556)})
    assert gr._wiki_geo_lookup("涅瓦河") is None


def test_wiki_wins_over_geonames_alias(wiki_db, monkeypatch):
    """GeoNames 把 彼得堡 错挂到加拿大 Peterborough；wiki/策展层必须优先。"""
    _make_wiki_db(wiki_db / "zh_wiki_geo.sqlite", {"圣彼得堡": (59.95, 30.32)})
    monkeypatch.setattr(
        gr,
        "_zh_alias_index",
        {"圣彼得堡": [(44.30, -78.32, 1000, "PPL", "CA", 1)]},
    )
    lat, lng = gr._resolve_from_zh_alias("圣彼得堡")
    assert (lat, lng) == pytest.approx((59.95, 30.32), abs=1e-5)


def test_geonames_fallback_when_wiki_misses(wiki_db, monkeypatch):
    _make_wiki_db(wiki_db / "zh_wiki_geo.sqlite", {})
    monkeypatch.setattr(
        gr,
        "_zh_alias_index",
        {"孟菲斯": [(35.14953, -90.04898, 646889, "PPL", "US", 4641239)]},
    )
    lat, lng = gr._resolve_from_zh_alias("孟菲斯")
    assert (lat, lng) == pytest.approx((35.14953, -90.04898), abs=1e-5)
