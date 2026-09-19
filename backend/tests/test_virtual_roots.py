"""虚拟根标记(语义根/工程根分离)回归测试。

背景(2026-09-19,王贺 10.2):「天下」(华夏政治-文化秩序)与「主世界」
(overworld 译名)不是同一概念。系统的 uber_root 与图层根(主世界/天界)
是工程脚手架,却泄漏进层级树与标注导出。修复:WorldStructure 新增
virtual_locations;图层新建根一律虚拟;uber_root 仅在水浒/三国/封神
(文本确有「天下」概念)为真实节点,其余小说虚拟化。
"""
from src.models.world_structure import LayerType, MapLayer, WorldStructure
from src.services.geo_skills.orchestrator import (
    GeoOrchestrator,
    is_real_tianxia_novel,
)


class TestRealTianxiaPolicy:
    def test_real_tianxia_novels(self):
        for t in ("水浒传", "三国演义", "封神演义"):
            assert is_real_tianxia_novel(t)

    def test_virtual_root_novels(self):
        for t in ("西游记", "红楼梦", "诡秘之主", ""):
            assert not is_real_tianxia_novel(t)


def _ws() -> WorldStructure:
    ws = WorldStructure(
        novel_id="test",
        layers=[
            MapLayer(layer_id="overworld", name="主世界",
                     layer_type=LayerType.overworld),
            MapLayer(layer_id="celestial", name="天界",
                     layer_type=LayerType.sky),
        ],
        location_tiers={
            "天下": "world", "东胜神洲": "continent", "西牛贺洲": "continent",
            "天庭": "kingdom", "离恨天": "region", "花果山": "region",
        },
        location_parents={
            "东胜神洲": "天下", "西牛贺洲": "天下",
            "天庭": "天下", "离恨天": "天下", "花果山": "东胜神洲",
        },
        location_layer_map={
            "天下": "overworld", "东胜神洲": "overworld", "西牛贺洲": "overworld",
            "花果山": "overworld", "天庭": "celestial", "离恨天": "celestial",
        },
    )
    return ws


class TestInjectLayerRootsVirtualMarking:
    def test_xiyouji_uber_root_is_virtual(self):
        """西游:天下非文本概念 → uber_root 虚拟;新建图层根虚拟。"""
        ws = _ws()
        GeoOrchestrator._inject_layer_roots(ws, "西游记")
        assert "天下" in ws.virtual_locations
        # overworld 有东胜神洲/西牛贺洲两个直系 → 新建「主世界」虚拟根
        assert "主世界" in ws.virtual_locations
        # 天庭是真实地点,即便服务 celestial 分组也不得标记为虚拟
        assert "天庭" not in ws.virtual_locations

    def test_shuihu_uber_root_is_real(self):
        """水浒:「天下」是文本真实概念(大宋天下) → uber_root 不虚拟。"""
        ws = _ws()
        GeoOrchestrator._inject_layer_roots(ws, "水浒传")
        assert "天下" not in ws.virtual_locations
        # 图层新建根仍然是脚手架
        assert "主世界" in ws.virtual_locations

    def test_world_structure_virtual_locations_json_roundtrip(self):
        """virtual_locations 随 WorldStructure 序列化往返(pydantic)。"""
        ws = _ws()
        ws.virtual_locations = {"天下", "主世界"}
        ws2 = WorldStructure.model_validate_json(ws.model_dump_json())
        assert ws2.virtual_locations == {"天下", "主世界"}


class TestSemanticParent:
    def _ws(self) -> WorldStructure:
        return WorldStructure(
            novel_id="test",
            location_parents={
                "大唐国": "主世界", "主世界": "天下",
                "山东": "天下", "郓城县": "济州", "济州": "山东",
            },
            virtual_locations={"主世界", "天下"},
        )

    def test_skips_virtual_ancestors(self):
        """大唐国→主世界(虚拟)→天下(虚拟) → None(顶层浮动)。"""
        ws = self._ws()
        assert ws.semantic_parent("大唐国") is None
        assert ws.semantic_parent("主世界") is None

    def test_real_parent_passthrough(self):
        """链条中无虚拟节点时原样返回。"""
        ws = self._ws()
        assert ws.semantic_parent("郓城县") == "济州"
        assert ws.semantic_parent("济州") == "山东"

    def test_partial_virtual_chain(self):
        """山东→天下(虚拟) → None;若天下真实则返回天下。"""
        ws = self._ws()
        assert ws.semantic_parent("山东") is None
        ws.virtual_locations = {"主世界"}  # 天下为真实节点(水浒场景)
        assert ws.semantic_parent("山东") == "天下"

    def test_is_virtual(self):
        ws = self._ws()
        assert ws.is_virtual("主世界")
        assert not ws.is_virtual("山东")
