"""Tests for TierClassifier._multi_feature_refine (Phase 2a)."""
from collections import Counter

from src.services.geo_skills.tier_classifier import TierClassifier


def _refine(tiers, parents, freq, children_count):
    return TierClassifier._multi_feature_refine(
        tiers=tiers, parents=parents,
        frequencies=Counter(freq), children_count=children_count,
    )


class TestMultiFeatureRefine:
    def test_rule_5_jie_boundary_becomes_region(self):
        """X界 (e.g. 流沙河界) → region, 不应为 city."""
        tiers = {"流沙河界": "city", "流沙河": "region"}
        parents = {"流沙河界": "流沙河"}
        updates = _refine(tiers, parents, {"流沙河界": 3}, {})
        assert updates.get("流沙河界") == "region"

    def test_rule_6_fu_residence_becomes_site(self):
        """长史府 parent=紫云山(region) → site (residence)."""
        tiers = {"长史府": "city", "紫云山": "region"}
        parents = {"长史府": "紫云山"}
        updates = _refine(tiers, parents, {"长史府": 1}, {})
        assert updates.get("长史府") == "site"

    def test_rule_1_parent_child_coherence(self):
        """region内不能有kingdom/continent."""
        tiers = {"东土大唐": "kingdom", "某山": "region"}
        parents = {"东土大唐": "某山"}
        updates = _refine(tiers, parents, {"东土大唐": 9}, {})
        assert updates.get("东土大唐") == "site"

    def test_rule_2_zero_evidence_demoted(self):
        """mc=0 + tier=continent → site (LLM artifact)."""
        tiers = {"神秘大陆": "continent"}
        parents = {}
        updates = _refine(tiers, parents, {}, {})
        assert updates.get("神秘大陆") == "site"

    def test_rule_3_single_mention_leaf_demoted(self):
        """mc=1, no children, tier=kingdom → site."""
        tiers = {"陕西大国": "kingdom"}
        parents = {"陕西大国": "天下"}
        updates = _refine(tiers, parents, {"陕西大国": 1}, {})
        assert updates.get("陕西大国") == "site"

    def test_rule_4_high_evidence_promoted(self):
        """mc>=30, children>=15, tier=site → region."""
        tiers = {"大花园": "site"}
        parents = {}
        updates = _refine(tiers, parents, {"大花园": 50}, {"大花园": 20})
        assert updates.get("大花园") == "region"

    def test_strong_evidence_not_demoted(self):
        """高mc节点不应被 rule 2/3 demote."""
        tiers = {"西牛贺洲": "continent"}
        parents = {"西牛贺洲": "天下"}
        updates = _refine(tiers, parents, {"西牛贺洲": 83}, {"西牛贺洲": 58})
        assert "西牛贺洲" not in updates  # 保持continent

    def test_legitimate_country_kept(self):
        """parent=continent时, kingdom正常保留."""
        tiers = {"天竺国": "kingdom", "西牛贺洲": "continent"}
        parents = {"天竺国": "西牛贺洲"}
        updates = _refine(tiers, parents, {"天竺国": 12}, {"天竺国": 15})
        assert "天竺国" not in updates
