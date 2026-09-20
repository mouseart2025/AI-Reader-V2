"""AuditorSkill — 层级入网门禁审计(P1-C,2026-09-20 起默认启用)。

在 Edmonds 裁决之后、SuffixNormalizer 之前,对"应用后"的 parents 图做
入网校验:

  1. check_spatial_constraints(CYCLE/TIER_INVERSION/SCALE_SKIP/NOISE_ROOT),
     虚拟根豁免取自 world_structures.virtual_locations(与
     scripts/evolve/compute_spatial_quality.py 同口径);SCALE_SKIP 走
     精修模式(parent 为 kingdom/region 级容器时豁免,只剔挂到
     world/continent 宏观根的真跨级);
  2. 同名异地规则:child 命中 is_homonym_prone 且票分布存在多个高票
     parent(top1/top2 < 2)时标记 HOMONYM_AMBIGUOUS;
  3. 违规边经 parent_overrides 剔除(child → None 脱钩);但
     (child, parent) ∈ snapshot.prior_edges 的边永不剔除(权威豁免)。

evolve_param("auditor.report_only", False) 为真时只记录不剔除:violations
写进 SkillResult.metadata["auditor"],供离线分析。默认 False = 剔除生效。
evolve_param("auditor.enabled", True) 可把整个 skill 移出链路。

确定性:所有遍历用 sorted() 或 (-weight, name) 双键排序。
"""

from __future__ import annotations

import json
import logging

from src.services.geo_skills.base import GeoSkill
from src.services.geo_skills.evolve_params import evolve_param
from src.services.geo_skills.snapshot import HierarchySnapshot, SkillResult
from src.utils.location_names import is_homonym_prone
from src.utils.spatial_quality import check_spatial_constraints

logger = logging.getLogger(__name__)


class AuditorSkill(GeoSkill):
    """Validate the post-Edmonds parents graph before it enters the network."""

    def __init__(self, novel_id: str = "",
                 virtual_roots: set[str] | None = None):
        self.novel_id = novel_id
        # 测试可直传;生产从 world_structures.virtual_locations 读。
        self._virtual_roots = virtual_roots

    @property
    def name(self) -> str:
        return "入网门禁审计"

    async def _load_virtual_roots(self) -> set[str]:
        if self._virtual_roots is not None:
            return set(self._virtual_roots)
        if not self.novel_id:
            return set()
        from src.db.sqlite_db import get_connection

        conn = await get_connection()
        try:
            cursor = await conn.execute(
                "SELECT structure_json FROM world_structures WHERE novel_id = ?",
                (self.novel_id,),
            )
            row = await cursor.fetchone()
        finally:
            await conn.close()
        if not row:
            return set()
        ws = json.loads(row["structure_json"])
        return set(ws.get("virtual_locations") or [])

    def _find_violations(self, snapshot: HierarchySnapshot,
                         virtual_roots: set[str]) -> list[dict]:
        parents = snapshot.location_parents
        # 精修 SCALE_SKIP(2026-09-20 实测):parent 为 kingdom/region 级合法
        # 宏观容器(山/县/府/州)时,site/building 直接挂载是常态,豁免;
        # 只有挂到 world/continent 级宏观根才报 SCALE_SKIP。
        violations = check_spatial_constraints(
            parents,
            location_tiers=snapshot.location_tiers,
            virtual_roots=virtual_roots,
            scale_skip_macro_exempt=True,
        )
        # 同名异地规则:易混名(皇宫/夹道/山洞…)在票分布上有多个高票
        # parent 时,其当前挂载不可靠。
        for child in sorted(snapshot.parent_votes):
            if not is_homonym_prone(child):
                continue
            counter = snapshot.parent_votes[child]
            if len(counter) < 2:
                continue
            ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
            (p1, w1), (p2, w2) = ranked[0], ranked[1]
            if w2 > 0 and w1 / w2 < 2:
                violations.append({
                    "code": "HOMONYM_AMBIGUOUS",
                    "severity": "warning",
                    "message": f"homonym-prone {child!r} has competing "
                               f"parents {p1!r}({w1:.2f}) vs {p2!r}({w2:.2f})",
                    "nodes": [child, p1, p2],
                })
        violations.sort(key=lambda v: (v["code"], tuple(v["nodes"])))
        return violations

    @staticmethod
    def _violation_edges(violation: dict,
                         parents: dict[str, str]) -> list[tuple[str, str]]:
        """把违规映射到可剔除的候选 (child, parent) 边(按确定性顺序)。"""
        code = violation["code"]
        nodes = violation["nodes"]
        if code in ("TIER_INVERSION", "SCALE_SKIP") and len(nodes) >= 2:
            return [(nodes[0], nodes[1])]
        if code == "CYCLE":
            # cycle 路径是 child→parent→…→child(末元素重复首元素);
            # 按排序顺序依次尝试,断开第一条非豁免边即可。
            return sorted(
                (nodes[i], nodes[i + 1]) for i in range(len(nodes) - 1)
            )
        if code == "HOMONYM_AMBIGUOUS" and len(nodes) >= 3:
            child, p1, p2 = nodes[0], nodes[1], nodes[2]
            current = parents.get(child)
            if current in (p1, p2):
                return [(child, current)]
            return []
        # NOISE_ROOT 等无 child 边的违规:只能报告,无法剔除
        return []

    async def execute(self, snapshot: HierarchySnapshot) -> SkillResult:
        report_only = evolve_param("auditor.report_only", False)
        # 剔除的最低严重级:"warning"(默认,warning+error 都剔除)或
        # "error"(只剔 error 级 CYCLE/TIER_INVERSION;warning 级只记录)。
        # 实测 SCALE_SKIP(warning)批量剔除会破坏合法 building→mountain
        # 边(水浒 chain_accuracy -0.0222),故留此开关。
        min_severity = evolve_param("auditor.enforce_min_severity", "warning")
        virtual_roots = await self._load_virtual_roots()
        violations = self._find_violations(snapshot, virtual_roots)

        prior_edges = snapshot.prior_edges
        parents = snapshot.location_parents
        overrides: dict[str, str | None] = {}
        removed: list[dict] = []
        exempted: list[dict] = []
        for v in violations:
            if min_severity == "error" and v["severity"] != "error":
                continue
            candidates = self._violation_edges(v, parents)
            for edge in candidates:
                if edge in prior_edges:
                    # 权威豁免:prior 边永不剔除
                    exempted.append({"edge": list(edge), "code": v["code"]})
                    continue
                removed.append({"edge": list(edge), "code": v["code"]})
                if not report_only:
                    overrides[edge[0]] = None
                break  # 每条违规剔除一条边即可(CYCLE 断一即解)

        result = SkillResult(skill_name=self.name, parent_overrides=overrides)
        result.metadata["auditor"] = {
            "report_only": report_only,
            "violation_count": len(violations),
            "violations": violations,
            "removable": removed,
            "prior_exempted": exempted,
        }
        result.logs.append(
            f"审计: {len(violations)} 违规, 可剔除 {len(removed)}"
            f"({'仅记录' if report_only else '已剔除'}), "
            f"先验豁免 {len(exempted)}"
        )
        logger.info(
            "AuditorSkill: %d violations, %d removed, %d prior-exempted "
            "(report_only=%s)",
            len(violations), len(removed), len(exempted), report_only,
        )
        return result
