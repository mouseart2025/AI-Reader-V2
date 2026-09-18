"""GeoResolver — match novel location names to real-world GeoNames coordinates.

Supports multiple geographic datasets:
  - "cn"    → GeoNames CN.zip  (comprehensive Chinese locations, ~10MB)
  - "world" → GeoNames cities5000.zip (global cities with pop > 5000, ~5MB)

Auto-detects which dataset to use based on novel genre and location name
characteristics. Provides geo_type detection (realistic/mixed/fantasy) and
Mercator projection to canvas coordinates.

Architecture is extensible: add a new GeoDatasetConfig entry for custom
datasets (e.g., game worlds with a hand-crafted TSV).
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import random
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import httpx

from src.infra.config import GEONAMES_DIR

logger = logging.getLogger(__name__)

# ── Chinese alternate name index (from zh_geonames.tsv) ──
# Lazy-loaded by _load_zh_alias_index(). Only used with "world" dataset.
# Format: {zh_name: [(lat, lng, pop, feature_code, country_code, geonameid), ...]}
_zh_alias_index: dict[str, list[tuple[float, float, int, str, str, int]]] | None = None
_ZH_GEONAMES_TSV = Path(__file__).resolve().parent.parent.parent / "data" / "zh_geonames.tsv"

# ── Dataset configuration ────────────────────────────────


@dataclass(frozen=True)
class GeoDatasetConfig:
    """Configuration for a single geographic dataset."""
    key: str                # unique identifier: "cn", "world", ...
    url: str                # download URL
    zip_member: str         # expected filename inside the zip
    description: str = ""


# Built-in datasets
DATASET_CN = GeoDatasetConfig(
    key="cn",
    url="https://download.geonames.org/export/dump/CN.zip",
    zip_member="CN.txt",
    description="GeoNames China — comprehensive Chinese locations",
)

DATASET_WORLD = GeoDatasetConfig(
    key="world",
    url="https://download.geonames.org/export/dump/cities5000.zip",
    zip_member="cities5000.txt",
    description="GeoNames cities5000 — global cities with pop > 5000",
)

DATASET_REGISTRY: dict[str, GeoDatasetConfig] = {
    "cn": DATASET_CN,
    "world": DATASET_WORLD,
}


# ── Constants ────────────────────────────────────────────

# Overall cap (seconds) for a single GeoNames dataset download. The per-request
# httpx timeout only bounds individual socket stalls; a slow-but-progressing
# download could otherwise keep a map request awaiting indefinitely (#82).
_GEO_DOWNLOAD_OVERALL_TIMEOUT_S = 300.0


class GeoDataUnavailableError(RuntimeError):
    """GeoNames dataset could not be downloaded or loaded.

    Callers must degrade to the fictional layout instead of letting the
    request hang. Never persist a geo_type derived from this failure —
    the next request should retry the download.
    """

# Common Chinese geographic suffixes to strip for fuzzy matching
_GEO_SUFFIXES = re.compile(
    r"(城|府|州|县|镇|村|寨|山|河|湖|泊|谷|寺|庙|宫|殿|关|岭|峰|洞|岛|港|塘|坊|营|堡|隘|驿)$"
)

# Feature codes that represent administrative/populated places (preferred in disambiguation)
_ADMIN_CODES = frozenset({
    "PPLC",   # capital
    "PPLA",   # seat of first-order admin
    "PPLA2",  # seat of second-order admin
    "PPLA3",
    "PPLA4",
    "PPL",    # populated place
    "ADM1",   # first-order admin
    "ADM2",
    "ADM3",
    "ADM4",
})

# Stricter subset for geo_type DETECTION — excludes PPL (generic villages with pop=0
# that share names with common Chinese words) and ADM4/PPLA4 (sub-district level, too
# granular — e.g. 大观园 is an ADM4 in Beijing, 玉皇庙 is an ADM4 in Shaanxi)
_NOTABLE_FEATURE_CODES = frozenset({
    "PPLC",   # capital
    "PPLA",   # seat of first-order admin (province capital)
    "PPLA2",  # seat of second-order admin (prefecture capital)
    "PPLA3",  # seat of third-order admin (county seat)
    "ADM1",   # first-order admin (province/state)
    "ADM2",   # second-order admin (prefecture)
    "ADM3",   # third-order admin (county)
})

# Genre hints that are definitively fantasy → skip geo resolution entirely
_FANTASY_GENRES = frozenset({"fantasy", "xianxia"})

# ── Supplementary geo data ──────────────────────────────
# Supplementary geo data — entries NOT covered by zh_geonames.tsv or cities5000.
# zh_geonames.tsv covers ~25K city-level Chinese names; these fill the gaps.
# Categories: continents, oceans, countries, rivers, states (ADM1), landmarks,
# ambiguous overrides, Taiwan transliterations, historical/literary names.
_SUPPLEMENT_GEO: dict[str, tuple[float, float]] = {
    # ── Continents (not in GeoNames city data) ──
    "亚洲": (34.05, 100.62), "欧洲": (48.69, 9.14), "非洲": (1.65, 17.70),
    "北美洲": (48.17, -101.85), "南美洲": (-8.78, -55.49),
    "大洋洲": (-22.74, 140.02), "美洲": (19.43, -99.13),
    "南极洲": (-82.86, 135.0),
    # ── Oceans / seas / water bodies (not in GeoNames city data) ──
    "太平洋": (0.0, -160.0), "大西洋": (14.60, -28.27),
    "印度洋": (-20.0, 80.0), "北冰洋": (84.0, 0.0),
    "地中海": (35.0, 18.0), "红海": (20.0, 38.5),
    "波斯湾": (26.0, 52.0), "南海": (12.0, 113.0),
    "东海": (29.0, 126.0), "黄海": (35.0, 123.0),
    "渤海": (38.5, 119.5), "加勒比海": (15.0, -75.0),
    "黑海": (43.0, 35.0), "里海": (41.0, 51.0),
    "阿拉伯海": (14.0, 65.0), "墨西哥湾": (25.0, -90.0),
    "南太平洋": (-20.0, -140.0),
    # ── Countries (not in cities5000, only city-level entries there) ──
    "中国": (35.86, 104.20), "日本": (36.20, 138.25),
    "韩国": (36.50, 127.77), "朝鲜": (40.34, 127.51),
    "印度": (20.59, 78.96), "泰国": (15.87, 100.99),
    "越南": (14.06, 108.28), "缅甸": (19.76, 96.08),
    "马来西亚": (4.21, 101.98), "印度尼西亚": (-0.79, 113.92),
    "菲律宾": (12.88, 121.77),
    "英国": (55.38, -3.44), "法国": (46.23, 2.21),
    "德国": (51.17, 10.45), "意大利": (41.87, 12.57),
    "西班牙": (40.46, -3.75), "葡萄牙": (39.40, -8.22),
    "荷兰": (52.13, 5.29), "比利时": (50.50, 4.47),
    "瑞士": (46.82, 8.23), "奥地利": (47.52, 14.55),
    "瑞典": (60.13, 18.64), "挪威": (60.47, 8.47),
    "丹麦": (56.26, 9.50), "芬兰": (61.92, 25.75),
    "波兰": (51.92, 19.15), "希腊": (39.07, 21.82),
    "土耳其": (38.96, 35.24), "俄罗斯": (61.52, 105.32),
    "苏联": (61.52, 105.32),  # historical
    "美国": (37.09, -95.71), "加拿大": (56.13, -106.35),
    "墨西哥": (23.63, -102.55), "巴西": (-14.24, -51.93),
    "阿根廷": (-38.42, -63.62), "澳大利亚": (-25.27, 133.78),
    "新西兰": (-40.90, 174.89), "南非": (-30.56, 22.94),
    "埃及": (26.82, 30.80), "摩洛哥": (31.79, -7.09),
    "伊朗": (32.43, 53.69), "伊拉克": (33.22, 43.68),
    "沙特阿拉伯": (23.89, 45.08), "以色列": (31.05, 34.85),
    "巴勒斯坦": (31.95, 35.23), "叙利亚": (34.80, 38.99),
    "阿富汗": (33.94, 67.71), "巴基斯坦": (30.38, 69.35),
    "斯里兰卡": (7.87, 80.77), "尼泊尔": (28.39, 84.12),
    "蒙古": (46.86, 103.85), "孟加拉": (23.68, 90.36),
    "冰岛": (64.96, -19.02),
    "澳洲": (-25.27, 133.78),  # colloquial for 澳大利亚
    "新几内亚": (-6.0, 147.0),
    "寮国": (19.86, 102.50),  # Taiwan for 老挝
    "老挝": (19.86, 102.50),
    # ── Rivers (not in GeoNames city data) ──
    "恒河": (25.0, 83.0), "尼罗河": (26.0, 32.0),
    "密西西比河": (32.0, -91.0), "亚马逊河": (-3.4, -58.5),
    "多瑙河": (45.0, 29.0), "莱茵河": (50.0, 7.5),
    "伏尔加河": (55.0, 49.0),
    # ── Straits / canals (not in GeoNames city data) ──
    "暹罗湾": (9.0, 101.0), "孟加拉湾": (14.0, 88.0),
    "曼德海峡": (12.58, 43.33), "苏伊士运河": (30.46, 32.34),
    "巴拿马运河": (9.08, -79.68), "马六甲海峡": (2.5, 101.5),
    "英吉利海峡": (50.2, -1.0), "爱尔兰海峡": (53.5, -5.0),
    # ── Historical / literary names (not in GeoNames or wrong match) ──
    "锡兰": (7.87, 80.77),  # Sri Lanka old name
    "暹罗": (15.87, 100.99),  # Thailand old name
    "波斯": (32.43, 53.69),  # Iran old name
    "交趾支那": (10.82, 106.63),  # Cochinchina
    "安南": (16.46, 107.59),  # Annam
    "苏门答腊": (0.59, 101.34), "爪哇": (-7.61, 110.20),
    "婆罗洲": (0.96, 114.55), "好望角": (-34.36, 18.47),
    "果阿": (15.30, 74.12), "迦太基": (36.85, 10.33),
    # ── Cities not in zh_geonames.tsv (no zh alternate in GeoNames) ──
    "横滨": (35.44, 139.64), "布林迪西": (40.63, 17.94),
    "卡迪夫": (51.48, -3.18), "长崎": (32.75, 129.88),
    "马德拉斯": (13.08, 80.27), "贝拿勒斯": (25.32, 83.01),
    "阿拉哈巴德": (25.43, 81.85), "昌德纳戈尔": (22.87, 88.38),
    "大阪": (34.69, 135.50), "名古屋": (35.18, 136.91),
    "广岛": (34.40, 132.46), "菲尼克斯": (33.45, -112.07),
    "火奴鲁鲁": (21.31, -157.86),
    # ── Ambiguous city overrides (zh_alias picks wrong city by population) ──
    "华盛顿": (38.91, -77.04),  # override: zh_alias→UK Washington (pop 53K)
    "汉城": (37.57, 126.98),  # override: zh_alias→湖北汉城 (not Seoul)
    "伯明翰": (33.52, -86.80),  # override: Birmingham AL (not England)
    "剑桥": (42.37, -71.11),  # override: Cambridge MA (not UK)
    "西贡": (10.82, 106.63),  # override: Saigon/HCMC (not HK 西贡)
    "圣路易斯": (38.63, -90.20),  # override: St. Louis MO (not Brazil)
    "圣迭戈": (32.72, -117.16), "圣地亚哥": (32.72, -117.16),  # override: San Diego (not Chile)
    "里奇蒙": (37.54, -77.44),  # override: Richmond VA (not CA)
    "路易斯维尔": (38.25, -85.76),  # override: Louisville KY (not CO)
    # ── US states (ADM1 — not in cities5000, which only has city-level) ──
    "阿拉巴马": (32.32, -86.90), "阿拉巴马州": (32.32, -86.90),
    "佐治亚": (32.17, -82.90), "佐治亚州": (32.17, -82.90),
    "密西西比": (32.35, -89.40), "密西西比州": (32.35, -89.40),
    "田纳西": (35.52, -86.58), "田纳西州": (35.52, -86.58),
    "弗吉尼亚": (37.43, -78.66), "弗吉尼亚州": (37.43, -78.66),
    "加利福尼亚": (36.78, -119.42), "加利福尼亚州": (36.78, -119.42),
    "得克萨斯": (31.97, -99.90), "得克萨斯州": (31.97, -99.90),
    "佛罗里达": (27.66, -81.52), "佛罗里达州": (27.66, -81.52),
    "马萨诸塞": (42.41, -71.38), "马萨诸塞州": (42.41, -71.38),
    "伊利诺伊": (40.63, -89.40), "伊利诺伊州": (40.63, -89.40),
    "宾夕法尼亚": (41.20, -77.19), "宾夕法尼亚州": (41.20, -77.19),
    "俄亥俄": (40.42, -82.91), "俄亥俄州": (40.42, -82.91),
    "纽约州": (42.17, -74.95),
    "路易斯安那": (30.98, -91.96), "路易斯安那州": (30.98, -91.96),
    "北卡罗来纳": (35.76, -79.02), "北卡罗来纳州": (35.76, -79.02),
    "南卡罗来纳": (33.84, -81.16), "南卡罗来纳州": (33.84, -81.16),
    "科罗拉多": (39.55, -105.78), "科罗拉多州": (39.55, -105.78),
    "华盛顿州": (47.75, -120.74),
    "印第安纳": (40.27, -86.13), "印第安纳州": (40.27, -86.13),
    "密苏里": (37.96, -91.83), "密苏里州": (37.96, -91.83),
    "马里兰": (39.05, -76.64), "马里兰州": (39.05, -76.64),
    "康涅狄格": (41.60, -72.76), "康涅狄格州": (41.60, -72.76),
    "阿拉斯加": (64.20, -152.49), "阿拉斯加州": (64.20, -152.49),
    # ── US state abbreviations (colloquial Chinese) ──
    "加州": (36.78, -119.42), "德州": (31.97, -99.90),
    "麻省": (42.41, -71.38), "华府": (38.91, -77.04),
    # ── Fictional locations ──
    "绿弓镇": (32.32, -86.90),  # Greenbow (Forrest Gump), placed in Alabama
    # ── Taiwan-style transliterations (台湾译法, not in GeoNames) ──
    "亚拉巴马": (32.32, -86.90), "亚拉巴马州": (32.32, -86.90),
    "乔治亚": (32.17, -82.90), "乔治亚州": (32.17, -82.90),
    "印第安那": (40.27, -86.13), "印第安那州": (40.27, -86.13),
    "北卡罗莱纳": (35.76, -79.02), "北卡罗莱纳州": (35.76, -79.02),
    "南卡罗莱纳": (33.84, -81.16), "南卡罗莱纳州": (33.84, -81.16),
    "印第安那波里": (39.77, -86.16),  # Indianapolis (Taiwan)
    "木比耳": (30.69, -88.04),  # Mobile (Taiwan)
    "纳许维尔": (36.16, -86.78),  # Nashville (Taiwan)
    "沙凡纳": (32.08, -81.10),  # Savannah (Taiwan)
    "纽奥尔良": (29.95, -90.07),  # New Orleans (Taiwan)
    "曼菲斯": (35.15, -90.05),  # Memphis (Taiwan)
    "查尔斯屯": (32.78, -79.93),  # Charleston (Taiwan)
    "蒙乔乌利": (32.37, -86.30),  # Montgomery (Taiwan)
    "蒙夕": (32.37, -86.30),  # Montgomery (abbreviated)
    "休士顿": (29.76, -95.37),  # Houston (Taiwan)
    "德州休士顿": (29.76, -95.37),  # "Texas Houston" compound
    "萨瓦纳": (32.08, -81.10), "萨瓦那": (32.08, -81.10),  # Savannah variants
    # ── Vietnamese cities (common in war novels) ──
    "归仁": (13.77, 109.22),  # Quy Nhon
    "波来古": (13.97, 108.00),  # Pleiku
    # ── Landmarks / institutions (not in GeoNames city data) ──
    "维多利亚港": (22.29, 114.17),  # Victoria Harbour, HK
    "白宫": (38.90, -77.04), "国会山庄": (38.89, -77.01),
    "国会山": (38.89, -77.01), "华特·里德医院": (38.98, -77.10),
    "哈佛大学": (42.37, -71.12), "乔治亚大学": (33.95, -83.37),
    "迪斯尼乐园": (33.81, -117.92), "迪士尼乐园": (33.81, -117.92),
    # ── US military bases ──
    "狄克斯堡": (40.02, -74.58),  # Fort Dix
    "班宁堡": (32.35, -84.95), "乔治亚州班宁堡": (32.35, -84.95),
    "北极": (71.0, -156.0),  # Arctic (use Barrow)
    # ── City disambiguation overrides (zh_geonames.tsv picks wrong entry) ──
    "纽约": (40.71, -74.01),       # New York City (NOT Nebraska!)
    "北极海": (84.0, 0.0),          # Arctic Ocean (alt name for 北冰洋)
    # ── Maritime / oceanic (海底两万里, 神秘岛, adventure novels) ──
    "南冰洋": (-65.0, 0.0),          # Southern Ocean
    "南极海": (-65.0, 0.0),          # Southern Ocean (alt name)
    "中国海": (18.0, 114.0),         # China Sea (generic)
    "南中国海": (12.0, 113.0),       # South China Sea
    "北太平洋": (30.0, -160.0),      # North Pacific
    "北大西洋": (40.0, -30.0),       # North Atlantic
    "合恩角": (-55.98, -67.27),      # Cape Horn
    "锡兰岛": (7.87, 80.77),        # Ceylon (Sri Lanka)
    "苏门答腊岛": (0.59, 101.34),    # Sumatra Island
    "爪哇岛": (-7.61, 110.20),       # Java Island
    "婆罗洲岛": (0.96, 114.55),      # Borneo Island
    "马达加斯加": (-18.77, 46.87),   # Madagascar
    "马达加斯加岛": (-18.77, 46.87), # Madagascar Island
    "克里特岛": (35.24, 24.90),      # Crete
    "直布罗陀": (36.14, -5.35),      # Gibraltar
    "直布罗陀海峡": (35.97, -5.50),  # Strait of Gibraltar
    "托雷斯海峡": (-10.0, 142.0),    # Torres Strait
    "白令海峡": (65.77, -169.0),     # Bering Strait
    "莫桑比克海峡": (-17.0, 42.0),   # Mozambique Channel
    "德雷克海峡": (-59.0, -62.0),    # Drake Passage
    "几内亚湾": (3.0, 3.0),          # Gulf of Guinea
    "哈得逊湾": (60.0, -85.0),       # Hudson Bay
    "阿拉伯半岛": (23.0, 45.0),      # Arabian Peninsula
    "安的列斯群岛": (17.0, -62.0),    # Antilles Islands (Caribbean)
    "克利亚峡": (52.0, -5.0),        # Unclear — Irish Sea area (Clew Bay?)
    "新赫布里底群岛": (-17.7, 168.3),# New Hebrides (now Vanuatu)
    "帕摩图群岛": (-17.5, -145.5),   # Tuamotu Archipelago
    "马贵斯群岛": (-9.0, -139.5),    # Marquesas Islands
    "克利斯波岛": (18.7, -111.2),    # Clarion Island (Crespo in novel)
    "格波罗尔岛": (-2.0, 134.0),     # Fictional — placed in Papua area
    "夏威夷群岛": (20.5, -157.0),    # Hawaiian Islands
    "瓦尼科罗岛": (-11.7, 166.9),    # Vanikoro Island
    "桑尼科夫岛": (73.0, 145.0),     # Sannikov Land (legendary Arctic island)
    "巴布亚": (-6.0, 147.0),         # Papua New Guinea
    "内布拉斯加": (41.5, -100.0),    # Nebraska
    "内布拉斯加州": (41.5, -100.0),  # Nebraska state
    "哈利法克斯": (44.65, -63.57),   # Halifax, Nova Scotia
    "利物浦": (53.41, -2.98),        # Liverpool
    "普鲁士": (52.52, 13.41),        # Prussia (Berlin area)
    "北美": (48.17, -101.85),        # North America
    "赤道": (0.0, 30.0),             # Equator (generic)
    "赤道线": (0.0, 30.0),           # Equator line
    "南极": (-82.86, 135.0),         # South Pole
    # ── Additional maritime / adventure novel supplements ──
    "麦哲伦海峡": (-52.5, -70.0),     # Strait of Magellan
    "日本海": (40.0, 135.0),          # Sea of Japan
    "托列斯海峡": (-10.0, 142.0),     # Torres Strait (alt transliteration)
    "珊瑚海": (-18.0, 155.0),        # Coral Sea
    "爱琴海": (38.5, 25.0),          # Aegean Sea
    "亚得里亚海": (42.5, 16.0),       # Adriatic Sea
    "巴芬湾": (73.0, -68.0),         # Baffin Bay
    "南极圈": (-66.5, 0.0),          # Antarctic Circle
    "北回归线": (23.5, 0.0),          # Tropic of Cancer
    "南回归线": (-23.5, 0.0),         # Tropic of Capricorn
    "维哥湾": (42.24, -8.72),        # Vigo Bay (Spain)
    "万尼科罗群岛": (-11.6, 166.9),   # Vanikoro Islands
    "罗夫丹群岛": (68.25, 14.5),      # Lofoten Islands (Norway)
    "青角群岛": (16.0, -24.0),        # Cape Verde Islands
    "阿梭尔群岛": (38.72, -27.22),    # Azores Islands
    "维蒂群岛": (-18.0, 178.0),       # Viti Levu (Fiji)
    "马露因群岛": (-51.75, -59.0),    # Falkland Islands (Malvinas)
    "纽芬兰岛": (48.5, -56.0),       # Newfoundland
    "纽芬兰": (48.5, -56.0),         # Newfoundland (short)
    "塞得港": (31.25, 32.29),        # Port Said (Egypt)
    "亚丁港": (12.78, 45.04),        # Aden (Yemen)
    "摩卡港": (13.32, 43.25),        # Mocha (Yemen)
    "墨尔本港": (-37.81, 144.96),     # Port Melbourne
    "斯勃齐堡": (78.0, 16.0),        # Spitsbergen
    "斯匹次卑尔根": (78.0, 16.0),     # Spitsbergen (alt)
    "新荷兰岛": (-25.0, 134.0),       # New Holland (old name for Australia)
    "马达邦角": (36.39, 22.48),       # Cape Matapan (Greece)
    "马纳尔岛": (9.03, 79.45),        # Mannar Island (Sri Lanka)
    "巴塔戈尼亚": (-41.0, -68.0),     # Patagonia
    "科罗曼德尔": (10.5, 79.8),       # Coromandel Coast (India, not Brazil)
    "台维斯海峡": (66.5, -58.0),      # Davis Strait
    "哈提拉斯角": (35.22, -75.53),    # Cape Hatteras
    "圣劳伦斯河": (47.0, -70.0),      # St. Lawrence River
    "密苏里河": (39.0, -94.5),        # Missouri River (at Kansas City)
    "魁北克": (46.81, -71.21),        # Quebec City
    "长岛": (40.79, -73.13),          # Long Island, NY
    "新泽西": (40.06, -74.41),        # New Jersey
    "新泽西州": (40.06, -74.41),      # New Jersey state
    "新泽西州海岸": (40.06, -74.41),   # New Jersey coast
    "多尔湾": (27.0, 34.0),           # Bay of Tor (Red Sea, Egypt)
    "巴哈麻水道": (25.0, -77.0),      # Bahamas Channel
    "捷萨别克湾": (37.5, -76.0),      # Chesapeake Bay
    "童女峡": (37.4, -75.8),          # (near Chesapeake, mapped contextually)
    "山德兰港": (54.91, -1.38),       # Sunderland (UK port)
    # ── Major world cities (missing from GeoNames zh_geonames resolution) ──
    "上海": (31.23, 121.47),          # Shanghai
    "伦敦": (51.51, -0.13),           # London
    "巴黎": (48.86, 2.35),            # Paris
    "威尼斯": (45.44, 12.34),         # Venice
    "格拉斯哥": (55.86, -4.25),       # Glasgow
    "旧金山": (37.78, -122.42),       # San Francisco
    "亚历山大港": (31.20, 29.92),      # Alexandria
    "苏伊士": (29.97, 32.55),         # Suez
    "庞贝城": (40.75, 14.49),         # Pompeii
    "俄国": (55.75, 37.62),           # Russia (old name, Moscow center)
    # ── European features ──
    "不列颠群岛": (54.0, -2.0),        # British Isles
    "翡翠岛": (53.4, -8.0),           # Ireland (Emerald Isle)
    "爱尔兰": (53.4, -8.0),           # Ireland
    "西班牙半岛": (40.0, -4.0),        # Iberian Peninsula
    "葡萄牙海岸": (39.0, -9.1),        # Portuguese coast
    "爱尔兰海岸": (53.0, -8.0),        # Irish coast
    "普罗文沙海岸": (43.3, 5.4),       # Provence coast
    "欧洲海岸": (43.0, 5.0),           # European coast (generic)
    "圣文孙特角": (37.0, -9.0),        # Cape St. Vincent
    "费罗哀群岛": (62.0, -7.0),        # Faroe Islands
    "哈夫尔港": (49.49, 0.11),         # Le Havre
    "圣马罗港": (48.65, -2.0),         # Saint-Malo
    "阿尔": (43.68, 4.63),            # Arles (Provence)
    "几沿尼群岛": (49.5, -2.5),        # Channel Islands (Guernsey area)
    # ── Mediterranean seas & islands ──
    "亚德里亚海": (42.5, 16.0),        # Adriatic Sea
    "亚德里亚海口": (40.0, 19.0),      # Adriatic entrance (Strait of Otranto)
    "爱奥尼亚海": (38.0, 19.0),        # Ionian Sea
    "西西里岛": (37.5, 14.0),          # Sicily
    "墨西哥海峡": (38.2, 15.6),        # Strait of Messina (old transliteration)
    "利比亚海峡": (37.0, 11.5),        # Strait of Sicily
    "利比亚海岸": (32.5, 15.0),        # Libyan coast
    "利亚沿海": (32.5, 15.0),          # Libyan coast (alt)
    "叙利亚海岸": (35.0, 35.8),        # Syrian coast
    "突尼斯": (33.9, 9.5),             # Tunisia
    "突尼斯海岸": (37.0, 10.0),        # Tunisian coast
    "北非": (30.0, 10.0),              # North Africa
    "阿尔及利亚": (28.0, 3.0),         # Algeria
    "希腊群岛": (37.5, 25.0),          # Greek Archipelago
    "斯波拉群岛": (39.0, 24.0),        # Sporades
    "罗得岛": (36.4, 28.2),            # Rhodes
    "嘉巴托斯岛": (35.6, 27.2),        # Karpathos
    "桑多休岛": (36.4, 25.4),          # Santorini
    "列卡岛": (38.7, 20.7),            # Lefkada
    "佐治岛": (37.6, 24.0),            # Greek island
    "铁那女神岛": (37.5, 25.2),        # Tinos
    "阿夫罗沙小岛": (35.9, 23.3),      # Cerigo area
    "雪利哥海面": (36.2, 23.0),        # Cerigo/Kythera sea
    "夫利那角": (41.0, 9.3),           # Cap Farina (Tunisia/Corsica area)
    # ── Red Sea & Arabia ──
    "亚丁湾": (12.0, 47.0),            # Gulf of Aden
    "亚喀巴湾": (28.5, 34.5),          # Gulf of Aqaba
    "苏伊士湾": (28.5, 33.5),          # Gulf of Suez
    "尤巴尔海峡": (12.6, 43.3),        # Bab el-Mandeb
    "铁哈马海岸": (15.0, 42.5),        # Tihama coast
    "丕林岛": (12.65, 43.43),          # Perim Island
    "北路斯海湾": (28.5, 34.0),        # Gulf area near Red Sea
    "北路斯城": (31.40, 30.42),        # Rosetta, Egypt
    "萨依斯城": (30.97, 30.77),        # Sais, ancient Egypt
    "苏阿京": (19.10, 37.33),          # Suakin, Sudan
    "光享达": (19.60, 37.20),          # Gondokoro/Red Sea port area
    "阿拉伯海底地道": (28.0, 33.5),    # Suez isthmus tunnel (fictional)
    "何烈山": (28.54, 33.97),          # Mount Horeb (=Sinai)
    "西奈山": (28.54, 33.97),          # Mount Sinai
    "哈达拉毛": (15.5, 48.0),          # Hadramaut, Yemen
    "马拉": (28.4, 33.8),              # Marah (biblical, near Sinai)
    "奇达": (21.49, 39.19),            # Jeddah
    "埃及海岸": (31.0, 30.0),          # Egyptian coast
    # ── Indian Ocean ──
    "印度半岛": (20.0, 78.0),          # Indian Peninsula
    "印度海": (15.0, 72.0),            # Indian Sea
    "阿曼海": (24.0, 60.0),            # Sea of Oman
    "阿曼": (21.5, 56.0),              # Oman
    "中东": (30.0, 45.0),              # Middle East
    "马尔代夫群岛": (3.2, 73.2),        # Maldives
    "拉克代夫群岛": (11.0, 72.5),      # Laccadive Islands
    "马斯卡林群岛": (-20.5, 57.5),     # Mascarene Islands
    "波旁岛": (-21.1, 55.5),           # Réunion (Île Bourbon)
    "吉檀岛": (7.0, 80.0),            # Near Sri Lanka
    "奶海": (10.0, 75.0),              # "Sea of Milk" (Indian Ocean area)
    "佐治玉呷": (11.75, 79.77),        # Cape Comorin area (Pondicherry)
    "马纳尔小岛": (9.03, 79.45),       # Mannar area
    "马纳尔湾": (8.9, 79.5),           # Gulf of Mannar
    "马纳尔礁石岩脉": (9.1, 79.4),     # Adam's Bridge reef
    # ── Southeast Asia / Oceania ──
    "东印度群岛": (-2.0, 118.0),        # East Indies
    "摩鹿加群岛": (-2.0, 127.0),        # Moluccas
    "巴布亚岛": (-5.0, 141.0),          # Papua
    "日本本土": (36.0, 138.0),          # Japan mainland
    "三罗格罗": (6.0, 121.0),           # Sulu (Philippines)
    "卡彭塔里亚海湾": (-14.0, 139.0),  # Gulf of Carpentaria
    "澳大利亚海岸": (-25.0, 150.0),    # Australian coast
    "甘伯兰海道": (-10.0, 142.0),      # Cumberland Passage (Torres Strait)
    "盎波尼岛": (-3.7, 128.2),         # Ambon Island
    "奥卢岛": (-17.7, 168.3),           # Vanuatu area
    "西林加巴当": (-13.6, 122.0),       # Seringapatam Reef
    "崩角": (18.5, 120.6),             # Cape Bojeador (Philippines)
    "白呷": (8.08, 77.55),             # Cape Comorin / Kanyakumari
    "尖呷": (1.26, 103.85),            # Singapore area cape
    # ── Pacific Ocean ──
    "社会群岛": (-17.5, -149.5),        # Society Islands
    "阿留申群岛": (52.0, -175.0),      # Aleutian Islands
    "阿留地安群岛": (52.0, -175.0),    # Aleutian Islands (alt)
    "东加塔布群岛": (-21.2, -175.2),   # Tongatapu (Tonga)
    "北加罗林群岛": (7.5, 150.0),      # Caroline Islands
    "航海家群岛": (-14.0, -171.0),     # Navigator Islands (Samoa)
    "留力口夷群岛": (7.0, 171.0),      # Marshall Islands area
    "留加衣群岛": (1.5, 173.0),        # Gilbert Islands (Kiribati)
    "加利福尼亚湾": (28.0, -112.0),    # Gulf of California
    "巴拿马湾": (8.0, -79.0),          # Gulf of Panama
    "火地岛": (-54.8, -68.3),          # Tierra del Fuego
    "加德路披岛": (29.2, -118.3),      # Guadalupe Island (Mexico)
    "帝文岛": (75.1, -87.0),           # Devon Island (Arctic)
    "铁匿利夫岛": (28.3, -16.5),       # Tenerife
    "嘉斯贡尼海湾": (44.5, -3.0),      # Bay of Biscay (Gascony)
    "塞内加尔岛": (14.67, -17.43),     # Gorée Island, Senegal
    "拉·白鲁斯群岛": (45.8, 142.0),   # La Pérouse Strait area
    "企林岛": (-3.0, 152.0),           # Keelung? / New Ireland area
    "帝位海": (10.0, 127.0),           # "Throne Sea" — Sulu Sea area
    "罗地小岛": (-10.5, 123.4),        # Roti Island (Indonesia)
    "韦塞尔角": (-12.0, 136.8),        # Cape Wessel (Australia)
    "小纹贝礁石": (-16.5, 145.5),      # Small reef near Barrier Reef
    "摩宜礁石": (20.8, -156.5),        # Molokai reef (Hawaii)
    "斯各脱暗礁群": (-14.7, 121.8),    # Scott Reef (Indian Ocean)
    "维多利亚暗礁": (-10.0, 142.0),    # Victoria Reef (Torres Strait)
    "依比尼亚": (40.0, -4.0),          # Iberia
    "嘉地埃": (36.53, -6.28),          # Cadiz
    "马露因海面": (-51.75, -59.0),     # Falklands sea area
    # ── Atlantic features ──
    "佛罗里达海峡": (24.0, -81.0),      # Florida Straits
    "佛罗里达湾": (25.0, -81.0),        # Florida Bay
    "加纳里群岛": (28.1, -15.4),        # Canary Islands
    "马德尔群岛": (32.6, -16.9),        # Madeira
    "萨尔加斯海": (30.0, -60.0),        # Sargasso Sea
    "挪威海": (68.0, 0.0),              # Norwegian Sea
    "桑罗克角": (-5.5, -35.3),          # Cape São Roque
    "圣·保罗岛": (0.92, -29.35),       # Saint Paul Rocks
    "阿棱尔群岛": (38.72, -27.22),     # Likely Azores (alt transliteration)
    "火岛": (40.63, -73.16),            # Fire Island (NY)
    "圣罗喀角": (-5.5, -35.3),          # Cape São Roque (alt)
    "佛利奥呷海面": (-23.0, -42.0),     # Cape Frio (Brazil)
    "美岛峡": (18.0, -67.9),            # Mona Passage
    "南大西洋": (-15.0, -25.0),         # South Atlantic
    "大西洋暖流": (35.0, -45.0),        # Gulf Stream area
    "大西洋洲": (30.0, -40.0),          # Atlantis
    "大西洋洲平原": (30.0, -40.0),      # Atlantis plain
    "大西洋海底": (30.0, -40.0),        # Atlantic seabed
    "纽芬兰岛暗礁脉": (46.0, -48.0),   # Grand Banks
    "海底电线": (45.0, -30.0),          # Transatlantic cable
    # ── South America ──
    "巴塔戈尼亚海岸": (-45.0, -67.0),  # Patagonian coast
    "巴西海岸": (-15.0, -39.0),         # Brazilian coast
    "非洲海岸": (0.0, 10.0),            # African coast
    "美洲联邦海岸": (38.0, -76.0),      # US east coast
    "日本海岸": (36.0, 136.0),          # Japanese coast
    "挪威海岸": (62.0, 5.0),            # Norwegian coast
    # ── Arctic ──
    "格陵兰岛": (72.0, -40.0),          # Greenland
    "喀拉海": (75.0, 65.0),             # Kara Sea
    "白海": (65.0, 38.0),               # White Sea
    "鄂毕湾": (70.0, 72.0),             # Ob Bay
    "李亚洛夫群岛": (74.0, 140.0),      # Liakhov Islands
    "北角": (71.17, 25.78),             # North Cape (Nordkapp)
    "南奥克内群岛": (-60.6, -45.5),     # South Orkney Islands
    "南设德兰群岛": (-62.0, -58.0),     # South Shetland Islands
    # ── Other adventure novel locations ──
    "圣·约翰港": (47.56, -52.71),       # St. John's, Newfoundland
    "种族角": (46.63, -53.07),          # Cape Race, Newfoundland
    "内心港": (47.56, -52.71),          # Heart's Content (telegraph station)
    "纽藏伯尔": (78.2, 15.6),           # Ny-Ålesund / Svalbard area
    "斯勃齐堡湾": (78.0, 16.0),        # Spitsbergen bay area
    # ── 神秘岛 (Jules Verne) — real-world references ──
    "里士满": (37.54, -77.44),          # Richmond, Virginia
    "伊利诺斯": (40.0, -89.0),          # Illinois
    "衣阿华州": (42.0, -93.5),          # Iowa
    "格林威治": (51.48, 0.0),           # Greenwich
    "墨尔本": (-37.81, 144.96),         # Melbourne
    "墨西哥暖流": (30.0, -80.0),        # Gulf Stream
    "诺福克岛": (-29.03, 167.95),       # Norfolk Island
    "泰地岛": (-17.5, -149.5),          # Tahiti (old transliteration)
    "阿姆斯特丹群岛": (-37.85, 77.55),  # Amsterdam Island (Indian Ocean)
    "玻里尼西亚群岛": (-17.0, -150.0),  # Polynesia
    "福克兰群岛": (-51.75, -59.0),      # Falkland Islands
    "卡利松": (43.75, 4.43),            # Callison / Cailloux area
    "本德尔汗德": (25.5, 80.0),         # Bundelkhand, India
    "安全岛": (-34.0, -152.0),          # Safety Island (fictional, placed in Pacific)
    "百老汇大街": (40.76, -73.98),      # Broadway, NYC
    "西瑞": (36.2, -5.4),              # Ceuta area
    "罗佛敦群岛": (68.25, 14.5),        # Lofoten (alt transliteration)
}

# ── Chinese historical / literary geographic supplement ──
# These override GeoNames because GeoNames matches them to wrong modern places.
# E.g., "西域" → GeoNames finds a village in Zhejiang; correct: Xinjiang region.
# Checked BEFORE GeoNames to prevent mismatches.
_SUPPLEMENT_CN: dict[str, tuple[float, float]] = {
    # Historical regions (武侠/历史小说常用)
    "中原": (34.75, 113.65),      # Central Plains (Henan area)
    "西域": (40.0, 80.0),          # Western Regions (Xinjiang / Central Asia)
    "江南": (30.5, 120.0),         # South of Yangtze (Jiangsu/Zhejiang)
    "塞外": (42.0, 112.0),         # Beyond the Great Wall (Inner Mongolia)
    "关外": (42.0, 123.0),         # Beyond Shanhai Pass (Manchuria)
    "关内": (34.5, 109.0),         # Inside the passes (Guanzhong)
    "关中": (34.3, 108.9),         # Guanzhong Plain (Shaanxi)
    "大漠": (42.0, 105.0),         # Gobi Desert
    "岭南": (23.1, 113.3),         # South of the Nanling Mountains (Guangdong)
    "塞北": (42.0, 112.0),         # North of the Great Wall
    "漠北": (46.0, 105.0),         # Northern desert (Mongolia)
    "漠南": (41.0, 112.0),         # Southern desert (Inner Mongolia)
    "江北": (32.0, 118.0),         # North of Yangtze
    "河北": (38.0, 114.5),         # Historical Hebei (north of Yellow River)
    "河南": (34.0, 113.5),         # Historical Henan (south of Yellow River)
    "河东": (35.5, 111.0),         # East of Yellow River (Shanxi)
    "河西": (38.5, 100.0),         # Hexi Corridor (Gansu)
    "山东": (36.5, 117.0),         # Shandong
    "山西": (37.5, 112.0),         # Shanxi
    "淮西": (32.0, 116.0),         # West of Huai River
    "淮南": (32.5, 117.0),         # South of Huai River
    "淮北": (33.5, 117.0),         # North of Huai River
    "川蜀": (30.5, 104.0),         # Sichuan
    "巴蜀": (30.5, 104.0),         # Ba-Shu (Sichuan/Chongqing)
    "荆楚": (30.5, 112.0),         # Jingchu (Hubei)
    "荆襄": (32.0, 112.0),         # Jingxiang region
    "燕赵": (39.0, 116.0),         # Yan-Zhao (Hebei/Beijing area)
    "苗疆": (27.0, 109.0),         # Miao territory (Guizhou/Hunan border)
    "回疆": (40.0, 78.0),          # Muslim territories (southern Xinjiang)
    "藏地": (31.0, 91.0),          # Tibet
    "吐蕃": (31.0, 91.0),          # Tubo (historical Tibet)
    # Historical capitals & cities (容易被 GeoNames 匹配到同名现代小区)
    "长安": (34.26, 108.94),       # Ancient Xi'an (NOT Shijiazhuang Chang'an Qu)
    "汴梁": (34.80, 114.35),       # Kaifeng (Song capital)
    "汴京": (34.80, 114.35),       # Kaifeng
    "东京": (34.80, 114.35),       # Kaifeng (Song-era Eastern Capital)
    "临安": (30.25, 120.17),       # Hangzhou (Southern Song capital)
    "金陵": (32.06, 118.80),       # Nanjing
    "建康": (32.06, 118.80),       # Nanjing (historical)
    "姑苏": (31.30, 120.62),       # Suzhou
    "平江": (31.30, 120.62),       # Suzhou (Song-era name)
    "大都": (39.90, 116.40),       # Beijing (Yuan capital)
    "燕京": (39.90, 116.40),       # Beijing (historical)
    "北平": (39.90, 116.40),       # Beijing (Republic era)
    "襄阳": (32.01, 112.14),       # Xiangyang
    "成都": (30.57, 104.07),       # Chengdu
    "大理": (25.69, 100.18),       # Dali (Yunnan)
    "昆明": (25.04, 102.68),       # Kunming
    # Famous mountains & landmarks (武侠常用)
    "天山": (42.0, 85.0),          # Tianshan Mountains (Xinjiang, NOT Inner Mongolia)
    "昆仑山": (36.0, 84.0),        # Kunlun Mountains
    "昆仑": (36.0, 84.0),          # Kunlun
    "华山": (34.48, 110.09),       # Mount Hua (Shaanxi)
    "泰山": (36.25, 117.10),       # Mount Tai (Shandong)
    "嵩山": (34.48, 112.95),       # Mount Song (Henan, home of Shaolin)
    "武当山": (32.40, 111.00),     # Wudang Mountain (Hubei)
    "峨眉山": (29.52, 103.33),     # Mount Emei (Sichuan)
    "衡山": (27.25, 112.65),       # Mount Heng (Hunan)
    "恒山": (39.68, 113.73),       # Mount Heng (Shanxi)
    "少林寺": (34.51, 112.94),     # Shaolin Temple
    "武当": (32.40, 111.00),       # Wudang
    "峨眉": (29.52, 103.33),       # Emei
    "终南山": (34.05, 108.85),     # Zhongnan Mountain
    "点苍山": (25.67, 100.10),     # Cangshan (Dali, Yunnan)
    "苍山": (25.67, 100.10),       # Cangshan
    "桃花岛": (29.78, 122.17),     # Peach Blossom Island
    "光明顶": (30.14, 118.17),     # Bright Summit (Huangshan)
    "黄山": (30.14, 118.17),       # Huangshan
    # Passes & strategic points
    "玉门关": (40.36, 93.86),      # Yumen Pass
    "阳关": (39.93, 94.05),        # Yang Pass
    "雁门关": (39.17, 112.87),     # Yanmen Pass
    "山海关": (40.00, 119.75),     # Shanhai Pass
    "函谷关": (34.52, 110.86),     # Hangu Pass
    "潼关": (34.49, 110.24),       # Tong Pass
    "剑门关": (32.29, 105.57),     # Jianmen Pass
    # Chinese seas (also in SUPPLEMENT_GEO, but needed here for CN-scope novels)
    "渤海": (38.5, 119.5),         # Bohai Sea
    "东海": (29.0, 126.0),         # East China Sea
    "黄海": (35.0, 123.0),         # Yellow Sea
    "南海": (12.0, 113.0),         # South China Sea
    # Rivers & water bodies
    "洞庭湖": (29.30, 112.80),     # Dongting Lake
    "鄱阳湖": (29.15, 116.27),     # Poyang Lake
    "太湖": (31.22, 120.13),       # Taihu Lake
    "西湖": (30.24, 120.14),       # West Lake (Hangzhou)
    "长江": (30.0, 115.0),         # Yangtze River (central section)
    "黄河": (35.0, 110.0),         # Yellow River
    "钱塘江": (30.20, 120.20),     # Qiantang River
    "大运河": (33.0, 117.0),       # Grand Canal
    "塔克拉玛干": (39.0, 83.0),    # Taklamakan Desert
    # Historical regions / states (西域)
    "高昌": (42.86, 89.53),        # Gaochang (Turpan, Xinjiang)
    "楼兰": (40.52, 89.73),        # Loulan (ancient Xinjiang city)
    "龟兹": (41.72, 82.97),        # Kucha (Xinjiang)
    "于阗": (37.12, 79.92),        # Khotan (Xinjiang)
    "敦煌": (40.14, 94.66),        # Dunhuang
    # ── Three Kingdoms / historical states & administrative divisions ──
    # Ancient state names (kingdom-level entities, no modern GeoNames match)
    "蜀": (30.57, 104.07),         # Shu (Sichuan)
    "蜀汉": (30.57, 104.07),       # Shu-Han
    "蜀国": (30.57, 104.07),       # Kingdom of Shu
    "西蜀": (30.57, 104.07),       # Western Shu
    "魏": (36.0, 114.0),           # Wei (northern China)
    "曹魏": (36.0, 114.0),         # Cao-Wei
    "魏国": (36.0, 114.0),         # Kingdom of Wei
    "东吴": (31.0, 120.0),         # Eastern Wu (Jiangnan)
    "吴国": (31.0, 120.0),         # Kingdom of Wu
    "江东": (31.0, 119.0),         # East of the Yangtze (Wu territory)
    "西川": (30.5, 104.0),         # Western Sichuan
    "东川": (31.0, 105.0),         # Eastern Sichuan (Bazhong area)
    # Ancient 州 (provinces / regions — many differ from modern city of same name)
    "冀州": (37.5, 115.5),         # Ji Province (Hebei)
    "兖州": (35.5, 116.8),         # Yan Province (Shandong)
    "豫州": (33.0, 114.0),         # Yu Province (Henan)
    "益州": (30.5, 104.0),         # Yi Province (Sichuan)
    "扬州": (31.5, 119.0),         # Yang Province (historical: Jiangsu/Anhui/Zhejiang)
    "幽州": (39.9, 116.4),         # You Province (Beijing area)
    "并州": (37.5, 112.0),         # Bing Province (Taiyuan)
    "凉州": (37.9, 102.6),         # Liang Province (Wuwei, Gansu)
    "雍州": (34.3, 108.9),         # Yong Province (Guanzhong)
    "交州": (21.0, 105.8),         # Jiao Province (Vietnam/Guangxi)
    "司隶": (34.6, 112.4),         # Sili (capital region, Luoyang area)
    "西凉": (37.9, 102.6),         # Western Liang (Gansu)
    # Ancient cities (different names from modern, or too small for GeoNames)
    "许都": (34.0, 113.8),         # Xu Capital (Xuchang)
    "许昌": (34.0, 113.8),         # Xuchang
    "邺城": (36.3, 114.6),         # Ye City (Wei capital, near Handan)
    "邺郡": (36.3, 114.6),         # Ye Commandery
    "下邳": (34.3, 117.9),         # Xiapi (northern Jiangsu)
    "小沛": (34.7, 116.6),         # Xiaopei (near Pei County)
    "江夏": (30.6, 114.3),         # Jiangxia (Wuhan area)
    "夏口": (30.6, 114.3),         # Xiakou (Wuhan area)
    "樊城": (32.0, 112.1),         # Fancheng (north of Xiangyang)
    "新野": (32.5, 112.4),         # Xinye (Nanyang, Henan)
    "江陵": (30.3, 112.2),         # Jiangling (Jingzhou core)
    "南郡": (30.3, 112.2),         # Nan Commandery (Jingzhou)
    "柴桑": (29.7, 116.0),         # Chaisang (Jiujiang area)
    "建业": (32.06, 118.80),       # Jianye (Nanjing, Wu capital)
    "寿春": (32.6, 116.8),         # Shouchun (Anhui)
    "合淝": (31.8, 117.3),         # Hefei (ancient spelling)
    "宛城": (33.0, 112.5),         # Wan City (Nanyang)
    "汝南": (33.0, 114.4),         # Runan (Henan)
    "陈留": (34.8, 114.3),         # Chenliu (Kaifeng area)
    "南阳": (33.0, 112.5),         # Nanyang
    "陇西": (35.0, 104.6),         # Longxi (Gansu)
    "南郑": (33.0, 106.9),         # Nanzheng (Hanzhong area)
    "汉中": (33.1, 107.0),         # Hanzhong
    # Battle sites & strategic locations
    "官渡": (34.8, 114.0),         # Guandu (Battle of Guandu)
    "赤壁": (29.7, 113.9),         # Chibi (Battle of Red Cliffs)
    "长坂坡": (30.8, 111.8),       # Changbanpo (Battle of Changban)
    "华容道": (29.5, 112.7),       # Huarong Path
    "虎牢关": (34.8, 113.2),       # Hulao Pass (Tiger Trap Pass)
    "白帝城": (31.0, 109.5),       # Baidi City (Fengjie, Chongqing)
    "五丈原": (34.2, 107.6),       # Wuzhangyuan (Zhuge Liang's last campaign)
    "街亭": (34.7, 105.9),         # Jieting (Ma Su's defeat)
    "定军山": (33.1, 106.8),       # Mount Dingjun
    "麦城": (30.7, 111.8),         # Mai City (Guan Yu's death)
    "阳平关": (33.0, 106.5),       # Yangping Pass
    "斜谷": (33.9, 107.5),         # Xie Valley (northern Sichuan route)
    "剑阁": (32.2, 105.5),         # Jiange Pass (Shu gateway)
    "葭萌关": (32.4, 105.8),       # Jiameng Pass
    "褒斜道": (33.5, 107.2),       # Baoxie Road (Qinling crossing)
    # Ancient commanderies / counties (三国演义 + 水浒传 + general historical)
    "巴西": (31.4, 106.4),         # 巴西郡 in Sichuan (NOT Brazil!)
    "河内": (35.0, 113.0),         # Henei commandery (NOT Hanoi!)
    "蓝田": (34.2, 109.3),         # Lantian, Shaanxi (NOT Hong Kong!)
    "平原": (37.2, 116.4),         # Pingyuan county, Shandong
    "碣石": (39.9, 119.5),         # Jieshi, Hebei coast
    "涿郡": (39.5, 115.9),         # Zhuo commandery (Hebei)
    "涿县": (39.5, 115.9),         # Zhuo county (Hebei)
    "桃园": (39.5, 115.9),         # Peach Garden (Zhuo, NOT Taiwan!)
    "广宗": (37.1, 115.1),         # Guangzong (Hebei, Yellow Turban battle)
    "颍川": (34.2, 113.5),         # Yingchuan commandery (Henan)
    "陈仓": (34.4, 107.4),         # Chencang (Baoji, Shaanxi)
    "上方谷": (34.0, 107.3),       # Shangfang Valley
    "白马": (35.5, 114.6),         # Baima (Hua county, Henan)
    "安邑": (35.0, 111.0),         # Anyi (southern Shanxi)
    "乌林": (29.8, 113.5),         # Wulin (south bank of Red Cliffs)
    # Other historical place names commonly used across Chinese novels
    "洛阳": (34.62, 112.45),       # Luoyang (override for correct ancient center)
    "荆州": (30.33, 112.24),       # Jingzhou (override for historical center)
    "徐州": (34.26, 117.19),       # Xuzhou
    "青州": (36.7, 118.5),         # Qingzhou
    "长沙": (28.23, 112.94),       # Changsha
    # ── Literary fiction → real prototype mappings ──
    # 平凡的世界 (路遥) — set in northern Shaanxi (陕北), fictional names map to real places
    "黄土高原": (36.5, 109.0),     # Loess Plateau (陕北 center, override GeoNames)
    "原西": (36.58, 110.17),       # 原西县 → 延川县 (Yanchuan, Shaanxi)
    "原西县": (36.58, 110.17),     # Same as above
    "黄原": (36.59, 109.49),       # 黄原地区 → 延安 (Yan'an)
    "黄原地区": (36.59, 109.49),   # Same as above
    "黄原城": (36.59, 109.49),     # Same — the city center
    "铜城": (34.90, 108.95),       # 铜城 → 铜川 (Tongchuan, Shaanxi)
    "双水村": (36.60, 110.20),     # Fictional village near 延川
    "石圪节": (36.55, 110.15),     # Fictional town near 延川
    "石圪节公社": (36.55, 110.15), # Same — commune level
    "大牙湾煤矿": (34.95, 109.00),# Fictional mine near 铜川
    # 白鹿原 (陈忠实) — set in 关中平原 near Xi'an
    "白鹿原": (34.20, 109.10),     # Bailuyuan → east of Xi'an (Lantian area)
    "白鹿村": (34.20, 109.10),     # Same — the village
    "滋水县": (34.15, 109.05),     # Fictional county near Xi'an
    # ── 神秘岛 (Jules Verne) — Lincoln Island is in the South Pacific ──
    # The novel places it at ~34°57'S, 150°30'W. Key island landmarks must be
    # in the supplement to prevent GeoNames false matches (e.g., 富兰克林山→Tennessee).
    "林肯岛": (-34.95, -150.50),   # Lincoln Island (South Pacific)
    "富兰克林山": (-34.93, -150.48),  # Mount Franklin (island volcano, NOT Tennessee)
    "慈悲河": (-34.97, -150.52),   # Mercy River (flows from Mount Franklin)
    "红河": (-34.96, -150.53),     # Red Creek / Falls River
    "格兰特湖": (-34.94, -150.51), # Lake Grant
    "联合湾": (-34.98, -150.49),   # Union Bay
    "鲨鱼湾": (-34.99, -150.47),   # Shark Gulf
    "眺望岗": (-34.92, -150.47),   # Prospect Heights
    "达卡洞": (-34.96, -150.48),   # Dakkar Grotto (Captain Nemo's base)
    "盘蛇半岛": (-34.99, -150.52), # Serpentine Peninsula
    "花岗石宫": (-34.95, -150.49), # Granite House (main dwelling)
    "壁炉": (-34.95, -150.49),     # The Chimneys (first shelter)
    "眺望岗高地": (-34.92, -150.46), # Prospect Heights plateau
}

# Patterns indicating a name is NOT a real geographic place (for detection filtering)
_NON_GEO_PATTERNS = re.compile(
    r"(号$|车厢|车站|码头|包厢|酒吧|饭店|旅店|旅馆|俱乐部|协会|学会|法庭|银行|"
    r"商行|商店|办公|仓库|警[署卫]|领事馆|教堂$|大厅|售票|围墙|祭坛|行政|"
    r"火车$|列车|客轮|雪橇|甲板|大街|街$|院$|栅栏|灌木|丛林|树丛|"
    r"林间|密林|空[地场]|道上|河滨$|河边$|郊外$|花园$|舞台$|餐厅|烟馆|理发|"
    # Interior / positional — common in mansion/estate novels
    r"房$|房中$|房内$|房里$|门口$|门前$|门外$|"
    r"前边|后面|外边|里边|隔壁|对门|旁边|前头|外头|里头|上面|下面|"
    # Generic/directional — matches GeoNames places in wrong locations
    r"^北方$|^南方$|^东方$|^西方$|^北岸$|^南岸$|^东岸$|^西岸$|^海滨$|"
    r"^中军帐$|^大寨$|^水寨$|^旱寨$|^蜀营$|^魏寨$|^吴营$|"
    # Vessels / vehicles / ship interior — not geographic
    r"号船|号舰|客厅$|图书室$|舱房$|甲板$|^平台$|"
    r"^小艇$|^潜水船$|^潜水艇$|^牢狱$|楼梯$|铁梯$|^餐厅$|^走廊$|"
    r"珊瑚墓地|珊瑚王国|海底地道|阿拉伯海底地道|"
    # Abstract / meta-geographic — matches GeoNames but wrong semantics
    r"^海洋$|^地球$|^世界$|^冰山$|^大陆$|^黑水$|^海底$|^暖流$|^黑潮$|"
    # Generic structures — CN GeoNames is very noisy; nearly any 2-3 char word
    # matches some village. These are common non-geographic references in novels.
    r"^井下$|^井口$|^井底$|^沟底$|^水库$|^水井$|^池塘$|^鱼池$|"
    r"^操场$|^体育场$|^阳台$|^院子$|院子里$|^食堂$|小食堂$|^宿舍$|"
    r"^图书馆$|^大礼堂$|^机场$|^矿区$|^砖场$|砖瓦厂$|"
    r"^公路$|公路边$|^铁路$|^公社$|^街道$|^小镇$|^二楼$|"
    r"^窑洞$|石窑洞$|^土窑$|^破庙$|^戏台$|^杂货铺$|^铁匠铺$|"
    r"^花坛$|^草滩$|^碾盘$|^坟地$|^烟地$|^土场$|"
    r"^山梁$|^山洼$|^山湾$|山背后$|^对面山$|^小山沟$|"
    r"^报社$|^学校$|学校院子$|^中学$|^省城$|家属区$|"
    r"^土台子$|^老坑$|^城门洞$|^彩门$|^风门$|"
    # Generic natural features — Chinese terms that match Japanese/US GeoNames
    # entries via kanji/transliteration (e.g., 高山→Takayama, 河流→JP river town)
    r"^高山$|^河流$|^对岸$|^左岸$|^右岸$|^海岸$|^海角$|^海面$|"
    r"^悬崖$|^峭壁$|^沙丘$|^岩石$|^山石$|^石穴$|^石窟$|^岩洞$|"
    r"^火山$|^火山口$|^火山锥$|^高地$|^高原$|^平原$|^陆地$|^通道$|"
    r"^森林$|^竹林$|^松林$|^矮树林$|^瀑布$|^小湖$|^小岛$|^池子$|"
    r"^港湾$|^河湾$|^河岸$|^满潮线$|^水平线$|"
    # Man-made structures / farming / mining — fictional island locations
    r"^棚屋$|^畜栏$|^兽棚$|^鸽棚$|^猪圈$|^牲口棚$|^菜园$|^家禽场$|"
    r"^风磨$|^磨坊$|^煤矿$|^铁矿$|^煤层$|^硫磺泉$|^造船所$|^麦田$|"
    r"^营地$|^营棚$|^广场$|^吊篮$|^气球$|^大车$|^平底船$|"
    # Geological / terrain descriptors — compound terms
    r"^花岗石|^玄武岩|^熔岩|^乳香树|灌木地带$|"
    r"啄木鸟林$|有加利树林$|松柏科森林$|"
    # Additional terrain / water generics
    r"^分水岭$|^峡谷$|^山谷$|^山坡$|^山脚$|^山腰$|^礁石$|^暗礁$|"
    r"^栅栏$|^厩房$|^前仓$|^潜水艇$|^南军$|^北军$)"
)


# ── Chinese alternate name index ─────────────────────────


def _load_zh_alias_index() -> dict[str, list[tuple[float, float, int, str, str, int]]]:
    """Lazy-load zh_geonames.tsv into an in-memory lookup dict.

    TSV format (no header): zh_name \\t lat \\t lng \\t pop \\t feature_code \\t country_code \\t geonameid
    Same zh_name can map to multiple geonames entries (e.g., 孟菲斯 → Memphis TN + Memphis FL).
    Entries per name are sorted by population descending (done at build time).

    Returns {zh_name: [(lat, lng, pop, feature_code, country_code, geonameid), ...]}.
    """
    global _zh_alias_index
    if _zh_alias_index is not None:
        return _zh_alias_index

    if not _ZH_GEONAMES_TSV.exists():
        logger.warning("zh_geonames.tsv not found at %s — Chinese alias lookup disabled", _ZH_GEONAMES_TSV)
        _zh_alias_index = {}
        return _zh_alias_index

    index: dict[str, list[tuple[float, float, int, str, str, int]]] = {}
    count = 0
    with open(_ZH_GEONAMES_TSV, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 7:
                continue
            try:
                zh_name = parts[0]
                lat = float(parts[1])
                lng = float(parts[2])
                pop = int(parts[3])
                feat = parts[4]
                cc = parts[5]
                gid = int(parts[6])
            except (ValueError, IndexError):
                continue
            index.setdefault(zh_name, []).append((lat, lng, pop, feat, cc, gid))
            count += 1

    _zh_alias_index = index
    logger.info("zh_alias_index loaded: %d entries, %d unique names", count, len(index))
    return _zh_alias_index


def _resolve_from_zh_alias(
    name: str,
    parent_coord: tuple[float, float] | None = None,
) -> tuple[float, float] | None:
    """Look up a name in the Chinese alternate name index.

    Disambiguation:
      - If parent_coord is provided and multiple entries exist, prefer the one
        closest to the parent (within 1000km).
      - Otherwise, pick the entry with the highest population.
    """
    idx = _load_zh_alias_index()
    entries = idx.get(name)
    if not entries:
        return None

    if len(entries) == 1:
        return (entries[0][0], entries[0][1])

    # Multiple entries: disambiguate
    if parent_coord:
        # Prefer entry closest to parent within 1000km
        closest = None
        closest_dist = float("inf")
        for lat, lng, _pop, _feat, _cc, _gid in entries:
            dist = _haversine_km((lat, lng), parent_coord)
            if dist < closest_dist:
                closest_dist = dist
                closest = (lat, lng)
        if closest and closest_dist < 1000:
            return closest

    # Fallback: highest population (entries already sorted by pop desc from build)
    return (entries[0][0], entries[0][1])


# ── Data model ───────────────────────────────────────────


@dataclass(slots=True)
class GeoEntry:
    """A single GeoNames record."""
    lat: float
    lng: float
    feature_code: str
    population: int
    name: str  # primary name for logging


# ── GeoResolver ──────────────────────────────────────────


class GeoResolver:
    """Resolve place names to real-world coordinates via GeoNames.

    Supports multiple datasets. Index is cached at class level per dataset key
    to avoid redundant parsing across requests.
    """

    # Class-level index caches: {dataset_key: {name: [GeoEntry, ...]}}
    _indexes: ClassVar[dict[str, dict[str, list[GeoEntry]]]] = {}

    def __init__(self, dataset_key: str = "cn") -> None:
        if dataset_key not in DATASET_REGISTRY:
            raise ValueError(f"Unknown geo dataset: {dataset_key!r}")
        self.dataset_key = dataset_key
        self.config = DATASET_REGISTRY[dataset_key]

    # ── Data download & loading ──────────────────────────

    def _tsv_path(self) -> Path:
        return GEONAMES_DIR / self.config.zip_member

    async def _ensure_data(self) -> None:
        """Download the dataset zip from GeoNames if the TSV doesn't exist.

        Raises GeoDataUnavailableError on any failure (network, timeout,
        corrupt zip) so callers can degrade instead of hanging (#82).
        """
        tsv = self._tsv_path()
        if tsv.exists():
            return
        try:
            GEONAMES_DIR.mkdir(parents=True, exist_ok=True)
            logger.info(
                "Downloading GeoNames dataset [%s] from %s ...",
                self.dataset_key, self.config.url,
            )
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                resp = await asyncio.wait_for(
                    client.get(self.config.url),
                    timeout=_GEO_DOWNLOAD_OVERALL_TIMEOUT_S,
                )
                resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                # Extract the specific target file (not readme.txt etc.)
                target = self.config.zip_member
                if target in zf.namelist():
                    zf.extract(target, GEONAMES_DIR)
                    logger.info("Extracted %s to %s", target, GEONAMES_DIR)
                else:
                    # Fallback: extract largest .txt file (likely the data file)
                    txt_members = [
                        m for m in zf.namelist()
                        if m.endswith(".txt") and not m.lower().startswith("readme")
                    ]
                    if txt_members:
                        chosen = max(txt_members, key=lambda m: zf.getinfo(m).file_size)
                        zf.extract(chosen, GEONAMES_DIR)
                        # Rename to expected name if different
                        if chosen != target:
                            (GEONAMES_DIR / chosen).rename(GEONAMES_DIR / target)
                        logger.info("Extracted %s as %s", chosen, target)
        except GeoDataUnavailableError:
            raise
        except Exception as exc:
            raise GeoDataUnavailableError(
                f"GeoNames dataset [{self.dataset_key}] download failed: {exc}"
            ) from exc
        if not tsv.exists():
            raise GeoDataUnavailableError(f"Expected {tsv} after extraction")
        logger.info("GeoNames dataset [%s] ready at %s", self.dataset_key, tsv)

    def _load_index(self) -> dict[str, list[GeoEntry]]:
        """Parse the GeoNames TSV into an in-memory lookup dict.

        Key = place name (primary + Chinese/CJK alternate names).
        Value = list of GeoEntry (multiple entries can share the same name).

        GeoNames TSV columns (tab-separated, 19 fields):
          0:geonameid  1:name  2:asciiname  3:alternatenames
          4:latitude  5:longitude  6:feature_class  7:feature_code
          8:country_code  9:cc2  10:admin1  11:admin2  12:admin3  13:admin4
          14:population  15:elevation  16:dem  17:timezone  18:modification_date
        """
        if self.dataset_key in GeoResolver._indexes:
            return GeoResolver._indexes[self.dataset_key]

        tsv = self._tsv_path()
        logger.info("Loading GeoNames index [%s] from %s ...", self.dataset_key, tsv)
        index: dict[str, list[GeoEntry]] = {}
        count = 0

        with open(tsv, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 15:
                    continue
                try:
                    lat = float(parts[4])
                    lng = float(parts[5])
                    feature_code = parts[7]
                    population = int(parts[14]) if parts[14] else 0
                except (ValueError, IndexError):
                    continue

                primary_name = parts[1].strip()
                entry = GeoEntry(
                    lat=lat, lng=lng,
                    feature_code=feature_code,
                    population=population,
                    name=primary_name,
                )

                # Index by primary name
                if primary_name:
                    index.setdefault(primary_name, []).append(entry)

                # Index by alternate names (focus on CJK names for Chinese lookup)
                alt_names = parts[3] if len(parts) > 3 else ""
                if alt_names:
                    for alt in alt_names.split(","):
                        alt = alt.strip()
                        if not alt or alt == primary_name:
                            continue
                        # For "cn" dataset: only index CJK alternate names
                        # For "world" dataset: index all alternate names
                        #   (catches Chinese translations like 伦敦, 巴黎, etc.)
                        if self.dataset_key == "cn" and not _has_cjk(alt):
                            continue
                        index.setdefault(alt, []).append(entry)
                count += 1

        GeoResolver._indexes[self.dataset_key] = index
        logger.info(
            "GeoNames index [%s] loaded: %d records, %d unique lookup keys",
            self.dataset_key, count, len(index),
        )
        return index

    # ── Name resolution ──────────────────────────────────

    async def ensure_ready(self) -> None:
        """Ensure dataset is downloaded and index is loaded.

        Raises GeoDataUnavailableError when the dataset cannot be obtained
        or parsed — callers must degrade to fictional layout (#82).
        """
        await self._ensure_data()
        try:
            self._load_index()
        except Exception as exc:
            raise GeoDataUnavailableError(
                f"GeoNames dataset [{self.dataset_key}] index load failed: {exc}"
            ) from exc

    def resolve_names(
        self, names: list[str],
        parent_map: dict[str, str | None] | None = None,
    ) -> dict[str, tuple[float, float]]:
        """Resolve a list of place names to (lat, lng) coordinates.

        Resolution order (curated data beats noisy data):
          1. Curated supplement dictionaries (CN then GEO for cn dataset,
             GEO then CN for world dataset)
          2. Chinese alternate name index (zh_geonames.tsv, world dataset only)
          3. Exact match from GeoNames index
          4. Suffix stripping (remove common Chinese geographic suffixes)
          5. Disambiguation: prefer higher admin level, then population

        Two-pass strategy when parent_map is provided:
          - Pass 1: resolve all names (supplement + GeoNames)
          - Pass 2: validate ALL non-supplement GeoNames matches against
            parent coordinates. Threshold is dataset-aware: 1000km for CN
            (tight — catches same-name cities in wrong provinces), 5000km
            for world (loose — novel parent hierarchies are often wrong or
            overly broad, and real locations can be far from country centroids).
            This catches false positives like 桃园→台湾 (should be near
            涿郡 in Hebei) and 巴西→Brazil (should be in Sichuan).

        Returns dict of {name: (lat, lng)} for successfully resolved names.
        """
        index = self._load_index()
        use_zh_alias = self.dataset_key == "world"
        result: dict[str, tuple[float, float]] = {}
        supplement_names: set[str] = set()  # supplement matches (trusted, skip validation)
        geonames_matches: set[str] = set()  # GeoNames matches (need parent validation)

        # Dataset-aware supplement priority: CN dataset checks CN first,
        # world dataset checks GEO first. This handles ambiguous names like
        # 巴西 (Sichuan commandery vs Brazil country).
        if self.dataset_key == "cn":
            sup_order = (_SUPPLEMENT_CN, _SUPPLEMENT_GEO)
        else:
            sup_order = (_SUPPLEMENT_GEO, _SUPPLEMENT_CN)

        for name in names:
            if not name or len(name) < 2:
                continue

            # Level 1: curated supplement (highest priority — prevents mismatches
            # like 西域→浙江西域村, 长安→石家庄长安区, 天山→内蒙古天山镇)
            sup = sup_order[0].get(name) or sup_order[1].get(name)
            if sup:
                result[name] = sup
                supplement_names.add(name)
                continue

            # Skip obviously non-geographic names before GeoNames lookup.
            # Generic Chinese words like 丛林(jungle), 花园(garden), 河边(riverside)
            # can match real Chinese place names in GeoNames, causing wrong coordinates.
            if _NON_GEO_PATTERNS.search(name):
                continue

            # Level 2: Chinese alternate name index (world dataset only)
            if use_zh_alias:
                parent_coord = None
                if parent_map:
                    p = parent_map.get(name)
                    if p and p in result:
                        parent_coord = result[p]
                zh_coord = _resolve_from_zh_alias(name, parent_coord)
                if zh_coord:
                    result[name] = zh_coord
                    geonames_matches.add(name)
                    continue

            # Level 3: exact match from GeoNames
            entries = index.get(name)

            # Level 4: suffix stripping
            if not entries:
                stripped = _GEO_SUFFIXES.sub("", name)
                if stripped and stripped != name and len(stripped) >= 2:
                    entries = index.get(stripped)

            if entries:
                # Disambiguation: pick best entry
                best = _pick_best_entry(entries)
                result[name] = (best.lat, best.lng)
                geonames_matches.add(name)

        # Pass 2: validate ALL non-supplement matches against parent proximity.
        # GeoNames is noisy — common Chinese words match real places in wrong
        # provinces (桃园→台湾, 平原→云南, 海滨→加州). Parent proximity catches these.
        # Threshold is dataset-aware: CN=1000km (tight), world=5000km (loose).
        # World dataset needs larger radius because:
        #   1. Novel parent hierarchies are unreliable (旧金山→内布拉斯加州)
        #   2. Country centroids can be far from coastal cities (上海→中国=1800km)
        #   3. zh_geonames.tsv already disambiguates by population, reducing false positives
        max_dist_km = 1000 if self.dataset_key == "cn" else 5000
        if parent_map and geonames_matches:
            to_remove: list[str] = []
            for name in geonames_matches:
                if name not in result:
                    continue
                parent = parent_map.get(name)
                if not parent:
                    continue
                parent_coord = result.get(parent)
                if not parent_coord:
                    continue
                child_coord = result[name]
                dist = _haversine_km(child_coord, parent_coord)
                if dist > max_dist_km:
                    logger.info(
                        "Discarding GeoNames match %s→(%.1f,%.1f): "
                        "%.0fkm from parent %s→(%.1f,%.1f)",
                        name, child_coord[0], child_coord[1],
                        dist, parent, parent_coord[0], parent_coord[1],
                    )
                    to_remove.append(name)
            for name in to_remove:
                del result[name]

        logger.info(
            "GeoResolver[%s]: resolved %d / %d names (%.0f%%, %d supplement + %d GeoNames)",
            self.dataset_key, len(result), len(names),
            100 * len(result) / max(len(names), 1),
            len(supplement_names), len(result) - len(supplement_names),
        )
        return result

    def detect_geo_type(self, names: list[str]) -> str:
        """Detect whether the novel's locations are realistic, mixed, or fantasy.

        Two-stage filtering:
          1. Exclude obviously non-geographic names (rooms, positional words, etc.)
          2. Only count "notable" matches — places with population >= 5000 or
             county-level+ administrative feature codes. This prevents the massive
             false-positive rate caused by tiny villages (pop=0) in GeoNames CN
             that share names with common Chinese words (上房, 后门, 角门, 稻香村).

        Thresholds are lower than naive matching because the notable filter
        dramatically reduces false positives (e.g., 红楼梦 drops from 21.7%
        raw match to 3.5% notable match).

        Returns:
          - "realistic": >= 20% notable matches (travel/adventure novels)
          - "mixed": >= 15% notable matches (historical/wuxia with real geography)
          - "fantasy": < 15% notable matches (mansion/xianxia/pure fiction)
        """
        if not names:
            return "fantasy"

        # Filter to plausible geographic names only
        geo_names = [n for n in names if not _NON_GEO_PATTERNS.search(n)]
        if not geo_names:
            return "fantasy"

        # Count only notable matches (pop >= 5000 or admin-level)
        notable_count = self._count_notable_matches(geo_names)
        ratio = notable_count / len(geo_names)

        if ratio >= 0.20:
            geo_type = "realistic"
        elif ratio >= 0.15:
            geo_type = "mixed"
        else:
            geo_type = "fantasy"

        logger.info(
            "GeoResolver[%s]: geo_type=%s (notable %d/%d geo-plausible = %.0f%%, "
            "filtered %d non-geo from %d total)",
            self.dataset_key, geo_type, notable_count, len(geo_names),
            ratio * 100, len(names) - len(geo_names), len(names),
        )
        return geo_type

    def _count_notable_matches(self, names: list[str]) -> int:
        """Count names that match notable geographic entries for detection.

        Stricter than resolve_names():
          - Exact match only (no suffix stripping — "宁国府"→"宁国" creates
            false positives for mansion novels like 红楼梦)
          - Only county-level+ admin divisions (ADM1-3, PPLA-PPLA3, PPLC)
            or places with population >= 5000 count as notable
          - Excludes PPL (generic populated place, pop=0) and ADM4 (sub-district)
            which match common Chinese words like 后门, 角门, 大观园, 玉皇庙
        """
        index = self._load_index()
        use_zh_alias = self.dataset_key == "world"
        zh_idx = _load_zh_alias_index() if use_zh_alias else {}
        count = 0
        for name in names:
            if not name or len(name) < 2:
                continue
            # Curated supplement entries are always notable
            if name in _SUPPLEMENT_CN or name in _SUPPLEMENT_GEO:
                count += 1
                continue
            # Chinese alternate name index: entries with pop >= 5000 or notable feature
            if use_zh_alias and name in zh_idx:
                entries = zh_idx[name]
                # Best entry = first (sorted by pop desc at build time)
                best = entries[0]
                best_pop, best_feat = best[2], best[3]
                if best_feat in _NOTABLE_FEATURE_CODES or best_pop >= 5000:
                    count += 1
                    continue
            # Exact match only — no suffix stripping for detection
            entries = index.get(name)
            if entries:
                best = _pick_best_entry(entries)
                if best.feature_code in _NOTABLE_FEATURE_CODES or best.population >= 5000:
                    count += 1
        return count

    # ── Mercator projection ──────────────────────────────

    def project_to_canvas(
        self,
        resolved: dict[str, tuple[float, float]],
        locations: list[dict],
        canvas_w: int,
        canvas_h: int,
        *,
        padding: float = 0.08,
    ) -> list[dict]:
        """Project resolved lat/lng to canvas coordinates using Mercator projection.

        Returns a layout list compatible with layout_to_list() output format:
          [{"name": str, "x": float, "y": float, "radius": int}, ...]

        Only includes resolved locations. Unresolved locations are handled
        separately by place_unresolved_near_neighbors().
        """
        if not resolved:
            return []

        # Mercator projection: lng → x, lat → y via log(tan)
        projected: dict[str, tuple[float, float]] = {}
        for name, (lat, lng) in resolved.items():
            mx = lng  # longitude maps linearly to x
            my = math.degrees(
                math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
            )
            projected[name] = (mx, my)

        # Compute bounding box of projected points
        xs = [p[0] for p in projected.values()]
        ys = [p[1] for p in projected.values()]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        # Avoid division by zero if all points are at the same location
        span_x = max_x - min_x or 1.0
        span_y = max_y - min_y or 1.0

        # Fit to canvas with padding, preserving aspect ratio
        pad_x = canvas_w * padding
        pad_y = canvas_h * padding
        usable_w = canvas_w - 2 * pad_x
        usable_h = canvas_h - 2 * pad_y

        scale = min(usable_w / span_x, usable_h / span_y)
        # Center the map
        offset_x = pad_x + (usable_w - span_x * scale) / 2
        offset_y = pad_y + (usable_h - span_y * scale) / 2

        # Build location lookup for radius calculation
        loc_by_name = {loc["name"]: loc for loc in locations}

        result: list[dict] = []
        for name, (mx, my) in projected.items():
            cx = offset_x + (mx - min_x) * scale
            # Invert Y axis (canvas Y increases downward, latitude increases upward)
            cy = offset_y + (max_y - my) * scale

            loc = loc_by_name.get(name, {})
            mention = loc.get("mention_count", 1)
            level = loc.get("level", 0)
            radius = max(15, min(60, 10 + mention * 2 + (3 - level) * 5))

            result.append({
                "name": name,
                "x": round(cx, 1),
                "y": round(cy, 1),
                "radius": radius,
            })

        return result


# ── Geo scope detection ──────────────────────────────────


def detect_geo_scope(
    genre_hint: str | None,
    location_names: list[str],
) -> str:
    """Determine which geo dataset to use for a novel.

    Returns:
      - "cn"    — primarily Chinese locations (historical, wuxia, realistic, urban)
      - "world" — international / global locations (adventure, translated novels)
      - "none"  — fantasy / xianxia (skip geo resolution)

    Detection strategy:
      1. If genre is known fantasy → "none"
      2. Check for world-level signals: if location names match ≥ 3 distinct
         countries/continents/oceans from the supplement → "world" (overrides genre)
      3. If genre is known Chinese type → "cn"
      4. Otherwise, analyze location name characteristics
    """
    genre = (genre_hint or "").lower()

    # Definite fantasy → skip
    if genre in _FANTASY_GENRES:
        return "none"

    # Known Chinese genre → CN dataset (checked BEFORE world-level signals,
    # because historical novels reference Chinese seas 渤海/东海/黄海/南海
    # and places like 巴西 that coincidentally match world-level entries).
    # "realistic" novels (平凡的世界) are set in China with mixed real/fictional names.
    # NOTE: "wuxia" is NOT included — some xianxia novels are misclassified
    # as wuxia; they should go through detect_geo_type() for accurate detection.
    if genre in ("historical", "realistic"):
        return "cn"

    # Check world-level signals for unknown/realistic/adventure genres.
    # If the novel mentions multiple countries/continents/oceans, it's world-scope.
    if location_names:
        world_matches = sum(1 for n in location_names if n in _SUPPLEMENT_GEO)
        if world_matches >= 3:
            logger.info(
                "detect_geo_scope: %d names match supplement → world scope",
                world_matches,
            )
            return "world"

    # For unknown/adventure/realistic/urban/other genres: analyze location names
    if not location_names:
        return "cn"  # default

    cjk_only_count = 0
    for name in location_names:
        if _is_cjk_only(name):
            cjk_only_count += 1

    cjk_ratio = cjk_only_count / len(location_names)

    if cjk_ratio > 0.6:
        return "cn"
    else:
        return "world"


async def auto_resolve(
    genre_hint: str | None,
    location_names: list[str],
    major_names: list[str],
    parent_map: dict[str, str | None] | None = None,
    known_geo_type: str | None = None,
) -> tuple[str, str, GeoResolver | None, dict[str, tuple[float, float]]]:
    """High-level entry point: detect scope, load dataset, resolve names.

    Args:
        genre_hint: WorldStructure.novel_genre_hint
        location_names: all location names for resolution
        major_names: major location names (level <= 3) for geo_type detection
        parent_map: {location_name: parent_name} for proximity validation
        known_geo_type: if provided, skip detection and use this geo_type directly.
            Useful when geo_type is already cached on WorldStructure to avoid
            re-detection oscillation across different chapter ranges.

    Returns:
        (geo_scope, geo_type, resolver_or_none, resolved_coords)
    """
    # ── Fast path: caller already knows the geo_type (cached) ──
    if known_geo_type:
        # Apply genre override even on cached values — historical/realistic novels
        # may have been incorrectly classified as "fantasy" before the supplement
        # was expanded.  Upgrade to "mixed" so coordinate resolution runs.
        # NOTE: "wuxia" excluded — xianxia novels (凡人修仙传) are often
        # misclassified as wuxia, and the override incorrectly forces geographic mode.
        if (
            known_geo_type not in ("realistic", "mixed")
            and genre_hint
            and genre_hint.lower() in ("historical", "realistic")
        ):
            logger.info(
                "GeoResolver: cached geo_type '%s' overridden to 'mixed' "
                "for genre '%s'",
                known_geo_type, genre_hint,
            )
            known_geo_type = "mixed"
        if known_geo_type not in ("realistic", "mixed"):
            return ("", known_geo_type, None, {})
        # Need coordinate resolution — still need a dataset
        geo_scope = detect_geo_scope(genre_hint, location_names)
        if geo_scope == "none":
            return (geo_scope, known_geo_type, None, {})
        resolver = GeoResolver(dataset_key=geo_scope)
        await resolver.ensure_ready()
        resolved = resolver.resolve_names(location_names, parent_map)
        return (geo_scope, known_geo_type, resolver, resolved)

    # ── Normal path: detect geo_type from scratch ──
    geo_scope = detect_geo_scope(genre_hint, location_names)

    if geo_scope == "none":
        return geo_scope, "fantasy", None, {}

    resolver = GeoResolver(dataset_key=geo_scope)
    await resolver.ensure_ready()

    geo_type = resolver.detect_geo_type(major_names)

    # Historical/realistic novels are set in the real world — override fantasy to mixed.
    # Ancient place names (荆州, 冀州, 许都) often fail modern GeoNames matching,
    # causing false "fantasy" classification.  Realistic novels (平凡的世界) mix
    # fictional place names with real geography, also triggering false "fantasy".
    # NOTE: "wuxia" excluded — xianxia novels are often misclassified as wuxia,
    # and real wuxia novels have enough real place names for auto-detection.
    if geo_type == "fantasy" and genre_hint and genre_hint.lower() in ("historical", "realistic"):
        logger.info(
            "GeoResolver: genre '%s' overrides geo_type fantasy → mixed "
            "(historical/wuxia novels use real-world geography)",
            genre_hint,
        )
        geo_type = "mixed"

    # If CN dataset matches poorly, try world dataset as fallback
    if geo_type == "fantasy" and geo_scope == "cn":
        logger.info("CN dataset matched poorly, trying world dataset as fallback")
        resolver_world = GeoResolver(dataset_key="world")
        await resolver_world.ensure_ready()
        geo_type_world = resolver_world.detect_geo_type(major_names)
        if geo_type_world != "fantasy":
            # World dataset matched better — use it
            resolved = resolver_world.resolve_names(location_names, parent_map)
            return "world", geo_type_world, resolver_world, resolved

    if geo_type == "fantasy":
        return geo_scope, "fantasy", None, {}

    resolved = resolver.resolve_names(location_names, parent_map)
    return geo_scope, geo_type, resolver, resolved


# ── Module-level helpers ─────────────────────────────────


_FEATURE_RANK: dict[str, int] = {
    "PPLC": 10,   # national capital
    "ADM1": 9,    # first-order admin (province/state)
    "PPLA": 8,    # seat of first-order admin
    "ADM2": 7,    # second-order admin (prefecture)
    "PPLA2": 6,   # seat of second-order admin
    "ADM3": 5,    # third-order admin (county)
    "PPLA3": 4,   # seat of third-order admin
    "ADM4": 3,    # fourth-order admin
    "PPLA4": 2,   # seat of fourth-order admin
    "PPL": 1,     # populated place (generic)
}


def _feature_rank(code: str) -> int:
    """Return an importance rank for a GeoNames feature code."""
    return _FEATURE_RANK.get(code, 0)


def _haversine_km(
    coord1: tuple[float, float], coord2: tuple[float, float],
) -> float:
    """Approximate distance in km between two (lat, lng) points."""
    lat1, lng1 = math.radians(coord1[0]), math.radians(coord1[1])
    lat2, lng2 = math.radians(coord2[0]), math.radians(coord2[1])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 6371 * 2 * math.asin(min(1.0, math.sqrt(a)))


def _has_cjk(text: str) -> bool:
    """Check if text contains any CJK Unified Ideograph characters."""
    for ch in text:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF:
            return True
    return False


def _is_cjk_only(text: str) -> bool:
    """Check if text consists only of CJK characters (no Latin/digits)."""
    for ch in text:
        cp = ord(ch)
        if not (0x4E00 <= cp <= 0x9FFF):
            return False
    return True


def _pick_best_entry(entries: list[GeoEntry]) -> GeoEntry:
    """Pick the best entry when multiple GeoNames records share the same name.

    Priority:
      1. Administrative level rank (higher admin = more notable place)
      2. Population (higher = more notable)

    This prevents a small town with pop=12894 from beating a county-level
    ADM3 with pop=0 (e.g., 梁山 in Shandong vs Gansu).
    """
    if len(entries) == 1:
        return entries[0]

    return max(entries, key=lambda e: (_feature_rank(e.feature_code), e.population))


# ── Unresolved location geo_coords estimation ──────────


_GOLDEN_ANGLE = math.pi * (3 - math.sqrt(5))  # ≈ 2.3999... rad ≈ 137.5°


def _find_resolved_ancestor(
    name: str,
    parent_map: dict[str, str | None],
    resolved: dict[str, tuple[float, float]],
) -> tuple[float, float] | None:
    """Walk up the parent chain to find the first resolved ancestor.

    Returns the ancestor's (lat, lng) or None if no resolved ancestor exists.
    Cycle-safe: stops after visiting 20 nodes.
    """
    current = parent_map.get(name)
    visited: set[str] = {name}
    depth = 0
    while current and depth < 20:
        if current in resolved:
            return resolved[current]
        if current in visited:
            break  # cycle
        visited.add(current)
        current = parent_map.get(current)
        depth += 1
    return None


def _find_name_containment_anchor(
    name: str,
    resolved: dict[str, tuple[float, float]],
) -> tuple[float, float] | None:
    """Check if the unresolved name contains a resolved location name.

    E.g., "旧金山机场" contains "旧金山" → use 旧金山's coordinates.
    Picks the longest matching resolved name to avoid false positives.
    """
    best_match: str | None = None
    best_len = 0
    for resolved_name in resolved:
        if len(resolved_name) < 2:
            continue
        if resolved_name in name and resolved_name != name and len(resolved_name) > best_len:
            best_match = resolved_name
            best_len = len(resolved_name)
    if best_match:
        return resolved[best_match]
    return None


def _compute_largest_cluster_centroid(
    resolved: dict[str, tuple[float, float]],
) -> tuple[float, float]:
    """Compute centroid of the largest geographic cluster of resolved locations.

    Uses simple grid-based clustering: divide the world into 30° cells,
    find the cell with the most points, compute its centroid.
    This avoids the "Atlantic Ocean centroid" problem when resolved locations
    span multiple continents (e.g., US + China + Vietnam).
    """
    if not resolved:
        return (0.0, 0.0)

    # Grid-based clustering (30° cells ≈ 3000km)
    cell_size = 30.0
    cells: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for lat, lng in resolved.values():
        cell = (int(lat // cell_size), int(lng // cell_size))
        cells.setdefault(cell, []).append((lat, lng))

    # Find the largest cluster
    largest = max(cells.values(), key=len)
    avg_lat = sum(c[0] for c in largest) / len(largest)
    avg_lng = sum(c[1] for c in largest) / len(largest)
    return (avg_lat, avg_lng)


def _estimate_geo_scale(name: str) -> float:
    """Estimate geographic radius (degrees) from location name suffix.

    Chinese place names encode geographic scale via suffix:
      省/洲/高原 → large region (~2°)
      市/地区/盆地 → city/prefecture (~0.5°)
      县/区 → county (~0.15°)
      镇/乡/公社 → town (~0.05°)
      村/寨/屯 → village (~0.02°)
      街/路/巷/家/铺 → micro (~0.008°)
    """
    if re.search(r"省$|洲$|高原$|平原$|山脉$", name):
        return 2.0
    if re.search(r"市$|地区$|盆地$|流域$", name):
        return 0.5
    if re.search(r"县$|区$|城$", name):
        return 0.15
    if re.search(r"镇$|乡$|公社$|矿$", name):
        return 0.05
    if re.search(r"村$|寨$|屯$|庄$|坪$|湾$|沟$", name):
        return 0.02
    if re.search(r"街$|路$|巷$|铺$|家$|楼$|院$|窑$|窑洞$", name):
        return 0.008
    return 0.1  # default: county-ish


def place_unresolved_geo_coords(
    unresolved_names: list[str],
    resolved: dict[str, tuple[float, float]],
    parent_map: dict[str, str | None],
) -> dict[str, tuple[float, float]]:
    """Estimate lat/lng for unresolved locations by proximity to resolved neighbors.

    Resolution strategies (in priority order):
      1. Walk parent chain → find first resolved ancestor
      2. Name containment → "旧金山机场" contains resolved "旧金山"
      3. Resolved sibling → same parent has a resolved child
      4. Largest-cluster centroid → centroid of densest geographic cluster
         (avoids placing orphans in the ocean for multi-continent novels)

    Uses golden-angle (sunflower seed) distribution with:
      - Adaptive radius based on parent's geographic scale (省/市/县/镇/村)
      - Group-size scaling (more children → slightly larger radius)
      - Gaussian noise to break geometric ring patterns

    Returns {name: (lat, lng)} for each unresolved name that could be placed.
    """
    if not resolved or not unresolved_names:
        return {}

    rng = random.Random(42)  # deterministic but natural-looking

    # Build reverse parent map: parent -> [children]
    children_of: dict[str, list[str]] = {}
    for child, parent in parent_map.items():
        if parent:
            children_of.setdefault(parent, []).append(child)

    # Fallback centroid: largest cluster, not global average
    fallback_centroid = _compute_largest_cluster_centroid(resolved)

    result: dict[str, tuple[float, float]] = {}

    # Group unresolved by anchor strategy for better scatter
    groups: dict[str, list[str]] = {}  # anchor_key -> [names]
    anchors: dict[str, tuple[float, float]] = {}  # anchor_key -> (lat, lng)
    anchor_parents: dict[str, str] = {}  # anchor_key -> parent name (for scale)

    for name in unresolved_names:
        if name in resolved:
            continue  # already resolved

        anchor_coord = None
        anchor_key = None
        anchor_parent_name = ""

        # Strategy 1: walk up parent chain to find ANY resolved ancestor
        ancestor_coord = _find_resolved_ancestor(name, parent_map, resolved)
        if ancestor_coord:
            # Use the direct parent's name for grouping if possible
            direct_parent = parent_map.get(name, "")
            anchor_coord = ancestor_coord
            anchor_key = f"ancestor:{direct_parent or name}"
            anchor_parent_name = direct_parent or name
        else:
            # Strategy 2: name containment (旧金山机场 → 旧金山)
            containment_coord = _find_name_containment_anchor(name, resolved)
            if containment_coord:
                anchor_coord = containment_coord
                anchor_key = f"contain:{name}"
                anchor_parent_name = name
            else:
                # Strategy 3: find a resolved sibling (same parent)
                # Skip when siblings span > 40° (multi-continent parent like "天下"
                # whose children are 美国, 南美洲, 澳洲 → centroid in ocean)
                parent = parent_map.get(name)
                if parent and parent in children_of:
                    siblings = children_of[parent]
                    sibling_coords = [
                        resolved[s] for s in siblings
                        if s in resolved and s != name
                    ]
                    if sibling_coords:
                        lats = [c[0] for c in sibling_coords]
                        lngs = [c[1] for c in sibling_coords]
                        lat_span = max(lats) - min(lats)
                        lng_span = max(lngs) - min(lngs)
                        if lat_span <= 40 and lng_span <= 40:
                            avg_lat = sum(lats) / len(lats)
                            avg_lng = sum(lngs) / len(lngs)
                            anchor_coord = (avg_lat, avg_lng)
                            anchor_key = f"sibling:{parent}"
                            anchor_parent_name = parent

        # Strategy 4: largest-cluster centroid
        if anchor_coord is None:
            # Skip generic terrain terms (森林, 高山, 河流, etc.) that have no
            # parent chain — they are not meaningful geographic positions and
            # placing them at the cluster centroid pollutes the map.
            if _NON_GEO_PATTERNS.search(name):
                continue
            anchor_coord = fallback_centroid
            anchor_key = "cluster"
            anchor_parent_name = "region"

        anchors[anchor_key] = anchor_coord
        groups.setdefault(anchor_key, []).append(name)
        anchor_parents[anchor_key] = anchor_parent_name

    # Place each group using sunflower seed + noise around its anchor
    for anchor_key, names in groups.items():
        center = anchors[anchor_key]
        n = len(names)

        # Adaptive radius from parent scale (county=0.15°, village=0.02°, etc.)
        parent_name = anchor_parents.get(anchor_key, "")
        base_radius = _estimate_geo_scale(parent_name)
        # Scale up slightly for large groups to avoid overcrowding
        group_scale = 1.0 + 0.3 * math.log2(max(n, 1))
        radius = base_radius * group_scale

        for i, name in enumerate(names):
            angle = i * _GOLDEN_ANGLE
            # sqrt scaling fills the circle from center outward
            frac = (i + 1) / max(n, 1)
            r = radius * (0.3 + 0.7 * math.sqrt(frac))
            # Gaussian noise breaks ring pattern (±15% of current radius)
            r *= 1.0 + rng.gauss(0, 0.15)
            angle += rng.gauss(0, 0.2)  # ±11° angular jitter
            lat = center[0] + r * math.cos(angle)
            lng = center[1] + r * math.sin(angle)
            result[name] = (lat, lng)

    if result:
        logger.info(
            "place_unresolved_geo_coords: estimated %d / %d unresolved locations",
            len(result), len(unresolved_names),
        )
    return result
# ── GeoEvolve delta begin（自动生成，勿手改）──
_SUPPLEMENT_GEO.update({
    "一出坡": (34.2598, 117.277),
    "一山坡": (27.0053, 114.355),
    "七宝村": (35.5, 111.0),
    "七宝莲台": (30.24958, 104.66357),
    "七星坛": (25.68825, 117.62695),
    "万丈深潭": (28.5469, 112.236),
    "万圣龙宫": (26.1025, 105.879),
    "万城": (32.01036, 112.08142),
    "万安溪内": (27.77839, 110.21478),
    "万安溪草庵": (27.77839, 110.21478),
    "万安隐者草庵": (27.77839, 110.21478),
    "万寿门": (33.62973, 113.75769),
    "万松林": (31.65439, 109.43082),
    "三个大营": (29.84431, 117.71937),
    "三仙岛": (32.5714, 105.868),
    "三十五里溪": (28.98214, 109.66636),
    "三块青石头": (32.0827, 118.136),
    "三宫殿": (37.5, 112.0),
    "三山五岳": (29.52, 103.33),
    "三山关驿": (26.07925, 119.11205),
    "三山门": (30.24958, 104.66357),
    "三山门外": (30.24958, 104.66357),
    "三座关": (36.7, 118.5),
    "三江城大路": (25.78029, 109.50508),
    "三江界路": (26.30451, 115.73934),
    "三江面": (26.30451, 115.73934),
    "三济仓": (28.98214, 109.66636),
    "三清坛场": (36.66292, 116.99029),
    "三清殿": (37.03138, 112.00171),
    "三清观宇": (31.72742, 105.06106),
    "三碗不过冈酒店": (36.15889, 115.85139),
    "三辅": (30.5, 104.0),
    "上夜房": (36.66292, 116.99029),
    "上大夫府": (28.98214, 109.66636),
    "上庸": (30.3, 112.2),
    "上庸·馆驿": (30.3, 112.2),
    "上林苑": (34.26, 108.94),
    "上水头": (28.982, 120.078),
    "上洛": (30.5, 104.0),
    "上流头": (31.0, 119.0),
    "上邽": (34.65222, 105.80699),
    "下流": (35.0, 110.0),
    "下辨": (30.5, 104.0),
    "丛绿堂": (30.48328, 118.95604),
    "东京·金殿": (34.8, 114.35),
    "东京·馆驿": (34.8, 114.35),
    "东京去沧州路上": (34.8, 114.35),
    "东京太师府": (34.8, 114.35),
    "东京紫宸殿": (34.8, 114.35),
    "东京进奏院": (34.8, 114.35),
    "东京郊外": (34.8, 114.35),
    "东兴大堤": (29.62419, 105.13662),
    "东兴郡": (29.62419, 105.13662),
    "东凹里": (41.45638, 121.55976),
    "东北上路": (24.382, 103.11),
    "东北水寨": (35.79019, 116.12797),
    "东华门": (34.8, 114.35),
    "东南一隅": (31.3, 120.62),
    "东南城楼": (37.5, 115.5),
    "东南水寨": (35.80228, 116.11701),
    "东厕": (31.72742, 105.06106),
    "东厢": (39.41667, 114.01667),
    "东吴宫殿": (31.0, 120.0),
    "东吴船只": (31.0, 120.0),
    "东吴边界": (31.0, 120.0),
    "东寨": (35.79019, 116.12797),
    "东小巷": (34.8, 114.35),
    "东山凹": (23.36558, 116.69761),
    "东山顶上": (27.23428, 115.91849),
    "东岭关": (34.62, 112.45),
    "东岸": (30.5, 104.0),
    "东崖": (31.18756, 106.70378),
    "东崖上": (31.18756, 106.70378),
    "东廊三间小正房": (25.31817, 110.29869),
    "东汉": (30.5, 104.0),
    "东海之滨": (29.0, 126.0),
    "东海许州": (29.0, 126.0),
    "东海龙宫": (29.0, 126.0),
    "东禅堂": (36.66292, 116.99029),
    "东莱黄县": (31.5, 119.0),
    "东边小角门": (34.60914, 109.53352),
    "东边崖上": (37.82707, 114.29897),
    "东郭门": (34.8, 114.35),
    "东门": (38.26667, 116.75),
    "东门厢": (40.05756, 116.57455),
    "东门外": (38.26667, 116.75),
    "丞相府": (30.57, 104.07),
    "两军阵前": (34.83333, 112.46667),
    "两川": (34.23694, 108.95389),
    "两廊": (30.5, 104.0),
    "两淮": (32.5, 117.0),
    "两船之间": (31.0, 119.0),
    "两边树上": (30.77592, 120.70525),
    "严州": (30.5, 120.0),
    "中军帐": (22.62831, 107.12476),
    "中军帐房": (22.62831, 107.12476),
    "中军旗": (22.62831, 107.12476),
    "中央下寨": (25.78029, 109.50508),
    "中央大战船": (32.0, 118.0),
    "中途草舍": (29.13828, 121.2473),
    "中门外": (27.8609, 112.874),
    "临县": (35.5, 111.0),
    "临安伯府": (32.5, 117.0),
    "临安伯府里": (32.5, 117.0),
    "临沮山僻小路": (30.5, 104.0),
    "临淄": (30.5, 104.0),
    "临淮东川": (31.5, 119.0),
    "临潼关": (30.5482, 120.08),
    "临潼关·囹圄": (34.36915, 109.1915),
    "临潼关外": (34.36915, 109.1915),
    "临潼关外草茵": (34.36915, 109.1915),
    "临潼关山冈": (34.36915, 109.1915),
    "临潼关帅府": (34.36915, 109.1915),
    "临潼关殿前": (34.36915, 109.1915),
    "临潼关监中": (34.36915, 109.1915),
    "临潼帅府": (34.36915, 109.1915),
    "临潼扎板": (34.36915, 109.1915),
    "临潼闩锁": (34.36915, 109.1915),
    "丹墀下": (27.23428, 115.91849),
    "丹徒西山": (33.5156, 118.805),
    "乌伤": (31.5, 119.0),
    "乌巢": (29.90478, 115.0822),
    "乌戈国": (31.4313, 117.8323),
    "乌林子": (33.81265, 113.00431),
    "乌程": (30.5, 104.0),
    "乌龙神庙": (38.69292, 115.22721),
    "乐乡": (31.52324, 118.62014),
    "乐嘉城": (32.5, 117.0),
    "乐嘉桥": (32.5, 117.0),
    "乔国老宅": (31.0, 119.0),
    "九仙山桃源洞": (28.80315, 114.94527),
    "九叉头杨树": (25.4586, 100.545),
    "九宫八卦阵": (38.0, 114.5),
    "九宫山白鹤洞": (29.5188, 114.6814),
    "九曲栏": (35.60795, 114.19363),
    "九曲栏枰": (35.60795, 114.19363),
    "九曲盘桓洞": (33.88391, 120.28412),
    "九曲雕栏": (35.60795, 114.19363),
    "九曲雕栏之外": (35.60795, 114.19363),
    "九江": (30.5, 104.0),
    "九间大殿": (35.60795, 114.19363),
    "九霄碧汉": (40.05756, 116.57455),
    "九霄空内": (29.68817, 106.58852),
    "九鼎铁叉山": (30.5482, 120.08),
    "九龙侍席": (35.60795, 114.19363),
    "九龙岛": (31.72913, 115.91241),
    "九龙池": (34.0, 113.8),
    "书院": (28.98214, 109.66636),
    "乱石山碧波潭": (41.10406, 114.45016),
    "乱石磷磷": (25.4586, 100.545),
    "乾元山金光洞": (30.5482, 120.08),
    "乾方集": (28.58111, 112.95333),
    "二仙山麻姑洞": (36.5258, 116.995),
    "二天门": (32.4, 111.0),
    "二寨": (30.5, 104.0),
    "二层厂厅": (24.29833, 115.35056),
    "二层山门": (30.48328, 118.95604),
    "二山门": (30.77592, 120.70525),
    "二府之门": (30.48328, 118.95604),
    "二郎真君之庙": (27.23428, 115.91849),
    "二龙山黄峰岭": (28.80315, 114.94527),
    "二龙桥": (35.60795, 114.19363),
    "云南": (39.51966, 116.22107),
    "云堂": (35.5, 111.0),
    "云楼宫殿": (20.04312, 110.58231),
    "云霄洞": (36.0717, 113.265),
    "云龙门": (34.62, 112.45),
    "五关": (34.83333, 111.8),
    "五凤楼": (35.60795, 114.19363),
    "五凤楼前": (35.60795, 114.19363),
    "五台山秘魔岩": (25.9266, 102.66763),
    "五夷山": (29.50131, 121.01407),
    "五夷山山坡": (29.50131, 121.01407),
    "五夷山白云洞": (29.50131, 121.01407),
    "五岗镇": (37.5, 115.5),
    "五岳": (41.49958, 119.58752),
    "五岳楼": (34.8, 114.35),
    "五庄观·庭下": (30.77592, 120.70525),
    "五庄观·廊下": (30.77592, 120.70525),
    "五庄观·正殿": (30.77592, 120.70525),
    "五庄观二层门": (30.77592, 120.70525),
    "五庄观厨房": (30.77592, 120.70525),
    "五庄观山门": (30.77592, 120.70525),
    "五庄观正殿": (30.77592, 120.70525),
    "五庄观花园": (30.77592, 120.70525),
    "五庄观菜园": (30.77592, 120.70525),
    "五庄观道房": (30.77592, 120.70525),
    "五府六部各衙门": (23.5633, 116.2691),
    "五方队伍": (28.98214, 109.66636),
    "五显祠": (37.40855, 120.98354),
    "五界山": (30.43755, 114.8748),
    "五色琉璃塔": (29.68817, 106.58852),
    "五行山·山根下": (46.05841, 125.69651),
    "五里": (37.5, 115.5),
    "五间正殿": (30.48328, 118.95604),
    "五龙山云霄洞": (36.0717, 113.265),
    "京兆": (30.5, 104.0),
    "亭台里面": (26.1025, 105.879),
    "亭外头棚下": (31.8218, 105.201),
    "亭阁": (29.13828, 121.2473),
    "人参园": (30.77592, 120.70525),
    "仁清巷": (31.3, 120.62),
    "仓亭": (35.22222, 109.51722),
    "仓房": (28.54541, 92.31998),
    "仙庄·穿堂": (29.31904, 120.38129),
    "伏完府": (34.0, 113.8),
    "会稽": (30.5, 104.0),
    "低泽": (24.58542, 106.52965),
    "余化府": (28.98214, 109.66636),
    "佳梦关": (30.5482, 120.08),
    "佳梦关帅府": (30.5482, 120.08),
    "佳梦关教场": (30.5482, 120.08),
    "使臣房": (36.5, 117.0),
    "侯河小城": (32.08972, 120.21948),
    "便殿": (28.98214, 109.66636),
    "倒厅": (38.06667, 114.58333),
    "候潮门": (30.29365, 120.16142),
    "偃城": (34.26, 117.19),
    "假山石后": (42.02639, 121.73194),
    "僧堂": (34.8, 114.35),
    "元觉洞": (31.03054, 106.78437),
    "光禄寺库上": (29.8904, 104.328),
    "八卦阵": (28.98214, 109.66636),
    "八宝灵光洞": (30.5482, 120.08),
    "八纳洞": (34.26, 117.19),
    "公子宅": (30.33, 112.24),
    "公馆": (36.5, 117.0),
    "六和塔": (30.29365, 120.16142),
    "六宫": (35.60795, 114.19363),
    "关东": (34.34516, 110.77297),
    "关公宅": (31.60596, 106.65094),
    "关公营寨": (31.60596, 106.65094),
    "关外陇右": (30.5, 104.0),
    "关索寨": (33.1, 107.0),
    "其他诸侯国": (29.52, 103.33),
    "典赡所": (40.05756, 116.57455),
    "养性楼": (23.36558, 116.69761),
    "冀县": (33.0, 114.0),
    "冀城": (34.65222, 105.80699),
    "冀川": (41.49958, 119.58752),
    "冀州城东角楼": (37.53333, 115.46667),
    "冀州殿上": (37.5, 115.5),
    "冀州殿前": (37.5, 115.5),
    "冀州狱中": (37.5, 115.5),
    "冀州馆驿": (37.5, 115.5),
    "内宅": (34.50641, 109.06076),
    "内宫": (35.60795, 114.19363),
    "内宫门": (38.06667, 114.58333),
    "内庭": (28.98214, 109.66636),
    "内廷": (28.98214, 109.66636),
    "内苑": (30.87709, 103.59035),
    "内院": (26.43472, 118.83686),
    "军师府": (30.57, 104.07),
    "冷宫": (35.60795, 114.19363),
    "净土": (22.27723, 109.15539),
    "净室": (28.98214, 109.66636),
    "净慈港": (30.29365, 120.16142),
    "净水池": (25.50083, 111.98278),
    "凌云渡": (22.27723, 109.15539),
    "凝曦轩": (35.5565, 116.707),
    "凤仙郡·新寺": (40.05756, 116.57455),
    "凤仙郡界": (40.05756, 116.57455),
    "凤仪亭": (28.98214, 109.66636),
    "凤凰楼": (34.0, 113.8),
    "凤辇龙车": (28.54541, 92.31998),
    "凸碧山庄": (36.66292, 116.99029),
    "函关": (36.0717, 113.265),
    "分宫楼": (39.51966, 116.22107),
    "分宫楼下": (39.51966, 116.22107),
    "刑部": (30.5, 120.0),
    "列柳城": (29.66722, 112.86021),
    "刘备荆益": (30.5, 104.0),
    "刘太公庄院": (36.5, 117.0),
    "刘姥姥家": (30.48328, 118.95604),
    "刘家": (36.5, 117.0),
    "刘家山庄": (29.8253, 103.24634),
    "刘郎浦": (31.0, 119.0),
    "别宫": (34.62, 112.45),
    "刺史住宅": (42.59008, 124.43232),
    "前堂": (39.78302, 113.02171),
    "前山": (30.5482, 120.08),
    "前山坡下": (30.24701, 111.62726),
    "前山门": (30.77592, 120.70525),
    "前村": (28.98214, 109.66636),
    "前门": (21.56842, 110.00843),
    "剥皮亭": (21.56842, 110.00843),
    "功臣阁": (35.60795, 114.19363),
    "化血阵": (28.98214, 109.66636),
    "化龙池": (22.27723, 109.15539),
    "北关门": (30.29365, 120.16142),
    "北宫": (30.5, 104.0),
    "北山": (34.26, 117.19),
    "北山路": (34.26, 117.19),
    "北岸": (30.0, 115.0),
    "北府内宫门": (34.60914, 109.53352),
    "北府前殿": (34.60914, 109.53352),
    "北府精致小院": (34.60914, 109.53352),
    "北彝陵": (30.3, 112.2),
    "北掖门": (34.26, 108.94),
    "北海": (30.5482, 120.08),
    "北海眼": (30.5482, 120.08),
    "北海郡": (30.5482, 120.08),
    "北界墙根下": (36.66292, 116.99029),
    "北疆": (30.5482, 120.08),
    "北门外": (25.31817, 110.29869),
    "北门外桥上": (25.31817, 110.29869),
    "十字街": (36.66292, 116.99029),
    "十绝阵": (28.98214, 109.66636),
    "十里亭": (35.60795, 114.19363),
    "十里街": (31.3, 120.62),
    "十里长亭": (35.60795, 114.19363),
    "千步廊南": (39.51966, 116.22107),
    "千花洞": (29.13828, 121.2473),
    "午门外": (36.8205, 110.66925),
    "华光破屋": (34.59543, 119.13163),
    "华夷楼": (27.23428, 115.91849),
    "华夷阁": (35.60795, 114.19363),
    "华州牢": (34.51523, 109.75759),
    "单于界": (37.53333, 115.46667),
    "单身房": (22.53949, 107.45718),
    "南中七郡": (30.33, 112.24),
    "南安郡": (24.99583, 118.40749),
    "南安郡王祭棚": (30.48328, 118.95604),
    "南山冈": (22.56416, 113.95319),
    "南山涧": (22.56416, 113.95319),
    "南山涧下": (22.56416, 113.95319),
    "南岸": (30.0, 115.0),
    "南庄人家": (23.7188, 102.815),
    "南彝陵": (30.3, 112.2),
    "南徐": (31.0, 119.0),
    "南徐·馆驿": (31.0, 119.0),
    "南徐北固山": (31.0, 119.0),
    "南洋苦海": (12.0, 113.0),
    "南海普陀山": (12.0, 113.0),
    "南海普陀落伽山": (12.0, 113.0),
    "南海落伽山": (12.0, 113.0),
    "南粤": (30.5482, 120.08),
    "南谷": (30.43755, 114.8748),
    "南郊": (28.98214, 109.66636),
    "南郊山中": (28.98214, 109.66636),
    "南门": (28.98214, 109.66636),
    "南门头破瓦窑": (30.70271, 81.00236),
    "南阙": (30.5, 104.0),
    "南阳陵坡": (33.0, 112.5),
    "南韶道": (25.31817, 110.29869),
    "南顿": (30.5, 104.0),
    "博望坡": (33.0, 112.5),
    "博陵渡口": (30.5, 104.0),
    "卢员外家": (38.0, 114.5),
    "卢龙口": (35.5, 111.0),
    "卤城": (30.5, 104.0),
    "卧虎石": (22.53949, 107.45718),
    "卧龙冈": (33.0, 112.5),
    "卫国": (30.5482, 120.08),
    "厨下": (45.09034, 127.11454),
    "叁济仓": (28.98214, 109.66636),
    "受禅台": (34.26, 108.94),
    "受禅坛": (30.5, 104.0),
    "右城": (34.68737, 116.92484),
    "右寨": (27.8609, 112.874),
    "右扶风郿": (36.0, 109.0),
    "右营": (30.5482, 120.08),
    "司天台": (35.60795, 114.19363),
    "司狱司前": (36.5, 117.0),
    "司马懿寨": (30.33, 112.24),
    "各关隘": (33.1, 107.0),
    "各处角门": (36.66292, 116.99029),
    "各寺院": (34.7, 116.6),
    "后厅": (37.5, 115.5),
    "后宫·鳷鹊宫": (35.35778, 111.39832),
    "后宫门": (35.35778, 111.39832),
    "后帐": (28.98214, 109.66636),
    "后殿": (34.0, 113.5),
    "后营": (28.98214, 109.66636),
    "后街": (45.09034, 127.11454),
    "后角门": (36.66292, 116.99029),
    "吕布寨": (34.7, 116.6),
    "吕布寨·辕门": (34.7, 116.6),
    "吴会": (31.0, 119.0),
    "吴侯府": (31.0, 119.0),
    "吴宫": (32.06, 118.8),
    "吴押狱家": (31.5, 119.0),
    "吴郡": (33.5156, 118.805),
    "吴郡·馆驿": (33.5156, 118.805),
    "吴郡富春": (33.5156, 118.805),
    "周朝营寨": (28.98214, 109.66636),
    "周瑜寨": (31.0, 119.0),
    "周瑜寨·中军帐": (31.0, 119.0),
    "周营": (28.98214, 109.66636),
    "周营·中军": (28.98214, 109.66636),
    "周营·中军帐": (28.98214, 109.66636),
    "周营·辕门": (28.98214, 109.66636),
    "周营辕门": (28.98214, 109.66636),
    "命馆": (35.60795, 114.19363),
    "哈咇国": (30.70271, 81.00236),
    "商容府": (35.60795, 114.19363),
    "商朝": (30.5482, 120.08),
    "商朝大营": (30.5482, 120.08),
    "商朝营": (30.5482, 120.08),
    "商朝营地": (30.5482, 120.08),
    "商朝营寨": (30.5482, 120.08),
    "商朝营寨·辕门": (30.5482, 120.08),
    "商朝辕门": (30.5482, 120.08),
    "商朝领地": (30.5482, 120.08),
    "商营": (28.98214, 109.66636),
    "商郊": (30.5482, 120.08),
    "商阵": (28.98214, 109.66636),
    "善才庵": (38.06667, 114.58333),
    "嘉兴": (30.5, 104.0),
    "嘉德殿": (35.60795, 114.19363),
    "嘉德门": (27.26705, 116.80926),
    "囚车": (30.5482, 120.08),
    "四不象": (29.50131, 121.01407),
    "四众生祠": (40.05756, 116.57455),
    "四十三郡": (31.0, 120.0),
    "四姑娘屋子": (36.66292, 116.99029),
    "四寨·中军帐": (25.9632, 108.922),
    "四州": (31.0, 120.0),
    "团营": (28.98214, 109.66636),
    "园中·侧门": (26.10454, 119.35934),
    "园中小门": (26.10454, 119.35934),
    "园中花园": (26.10454, 119.35934),
    "园中院落": (26.10454, 119.35934),
    "园内厨房": (25.05379, 118.15261),
    "园内腰门": (25.05379, 118.15261),
    "园后墙": (36.66292, 116.99029),
    "园后门": (36.66292, 116.99029),
    "园子·角门": (39.04589, 112.95579),
    "园门角门": (36.66292, 116.99029),
    "围场": (28.98214, 109.66636),
    "囹圄": (40.33333, 119.13333),
    "土井": (39.22237, 121.66548),
    "土冈子": (32.0, 116.0),
    "土地庙": (39.51966, 116.22107),
    "土阜": (30.5, 104.0),
    "地灵县": (42.59008, 124.43232),
    "地烈阵": (28.98214, 109.66636),
    "地藏庵": (27.7633, 112.548),
    "坟庵": (38.69292, 115.22721),
    "垂柳阴": (36.66292, 116.99029),
    "城东南": (30.98021, 104.28081),
    "城南二百多地": (23.27217, 116.60502),
    "城南邮亭": (30.57, 104.07),
    "城南门外石子岗": (32.06, 118.8),
    "城外营中": (28.98214, 109.66636),
    "城敌楼": (27.8671, 87.4139),
    "城池·后宫": (30.00828, 108.71423),
    "城濠": (34.72164, 111.10136),
    "城隍庙": (39.72445, 115.84575),
    "塔郎甸": (34.26, 117.19),
    "墙缺内": (30.5, 104.0),
    "壶关口": (23.55553, 110.06518),
    "夏侯楙府": (24.99583, 118.40749),
    "夔关": (30.87709, 103.59035),
    "外宫门": (38.06667, 114.58333),
    "外水": (30.5, 104.0),
    "外营": (28.98214, 109.66636),
    "外藩公馆": (35.5565, 116.707),
    "外边": (45.09034, 127.11454),
    "外间台矶": (36.66292, 116.99029),
    "多宝台": (39.51966, 116.22107),
    "多浑虫家": (30.48328, 118.95604),
    "大书房": (30.48328, 118.95604),
    "大兴山": (39.5, 115.9),
    "大冲之内": (27.33458, 114.84651),
    "大圣禅寺": (32.91963, 118.54769),
    "大堂·暖阁": (22.37428, 112.97796),
    "大堂廊下": (22.37428, 112.97796),
    "大相国寺": (34.8, 114.35),
    "大艨艟": (29.5, 112.7),
    "大西天天雷音寺": (25.9266, 102.66763),
    "大观园·嘉荫堂": (36.66292, 116.99029),
    "大观园·夹道": (36.66292, 116.99029),
    "大观园·正门": (36.66292, 116.99029),
    "大观园·甬道": (36.66292, 116.99029),
    "大观园·角门": (36.66292, 116.99029),
    "大观园厨房": (36.66292, 116.99029),
    "大观园后门": (36.66292, 116.99029),
    "大观园后院": (36.66292, 116.99029),
    "大观园正门": (36.66292, 116.99029),
    "大辕门": (30.5482, 120.08),
    "大门口": (45.09034, 127.11454),
    "大雄宝殿": (30.24958, 104.66357),
    "大香铺": (31.0959, 108.13693),
    "天井": (41.45638, 121.55976),
    "天井中": (41.45638, 121.55976),
    "天地坛": (35.60795, 114.19363),
    "天水诸郡": (34.65222, 105.80699),
    "天水郡": (34.65222, 105.80699),
    "天水郡府": (34.65222, 105.80699),
    "天竺国后宫": (40.05756, 116.57455),
    "天竺国外郡": (40.05756, 116.57455),
    "天竺国都城": (40.05756, 116.57455),
    "天绝阵": (28.98214, 109.66636),
    "天荡山": (33.1, 107.0),
    "天香楼下": (30.48328, 118.95604),
    "天香楼下·外间": (30.48328, 118.95604),
    "天香楼下·里间": (30.48328, 118.95604),
    "天香楼下箭道": (30.48328, 118.95604),
    "太华山云宵洞": (31.1633, 119.63558),
    "太华山云霄洞": (31.1633, 119.63558),
    "太华山支霄洞": (31.1633, 119.63558),
    "太和宫": (32.4, 111.0),
    "太平街": (35.60795, 114.19363),
    "太庙": (35.60795, 114.19363),
    "太极东堂": (28.98214, 109.66636),
    "太湖石": (35.60795, 114.19363),
    "夹山峪": (30.5482, 120.08),
    "夹脊小路": (26.43472, 118.83686),
    "夹道东门": (36.66292, 116.99029),
    "夹龙山猛兽崖": (24.58542, 106.52965),
    "夹龙山飞云洞": (24.58542, 106.52965),
    "奉口镇": (30.29365, 120.16142),
    "女墙": (34.91667, 112.75),
    "女娲宫": (35.60795, 114.19363),
    "女道丹房": (36.66292, 116.99029),
    "妆台": (21.56842, 110.00843),
    "妙岩宫": (33.88391, 120.28412),
    "姊归": (27.8609, 112.874),
    "姑苏城": (31.3, 120.62),
    "姜子牙命馆": (35.60795, 114.19363),
    "威胜城": (35.5, 111.0),
    "子牙中军大营": (34.83333, 111.8),
    "子牙大营": (28.98214, 109.66636),
    "孔函谷": (30.33, 112.24),
    "孔宣营中": (28.98214, 109.66636),
    "孔宣行营": (28.98214, 109.66636),
    "孔宣行营後": (28.98214, 109.66636),
    "孙权大寨": (31.8, 117.3),
    "孙权江东": (30.5, 104.0),
    "孙权营": (31.8, 117.3),
    "孙桓寨": (30.28742, 111.38034),
    "孙策灵柩前": (33.5156, 118.805),
    "孝堂": (34.89689, 108.5646),
    "孟津大营": (34.83333, 112.46667),
    "孟津战场": (34.83333, 112.46667),
    "孟获大寨": (40.11267, 114.15537),
    "孱陵": (30.33, 112.24),
    "宁国府·上房": (30.48328, 118.95604),
    "宁国府·暖阁": (30.48328, 118.95604),
    "宁国府·正房": (30.48328, 118.95604),
    "宁国府·正门": (30.48328, 118.95604),
    "宁国府正室": (30.48328, 118.95604),
    "守园门小厮班房": (36.66292, 116.99029),
    "安众隘口": (23.55553, 110.06518),
    "安喜县": (22.52306, 113.37912),
    "安喜县·馆驿": (22.52306, 113.37912),
    "宋国": (30.5482, 120.08),
    "宋太公庄院": (34.4469, 109.30169),
    "宋异人庄上": (35.60795, 114.19363),
    "宋异人後花园": (39.78302, 113.02171),
    "宋江大寨": (35.79019, 116.12797),
    "宋江行寨": (31.66715, 119.66527),
    "宗祠": (30.48328, 118.95604),
    "官渡隘口": (34.8, 114.0),
    "定军山北": (33.1, 106.8),
    "定军山大寨": (33.1, 106.8),
    "定陶": (30.87709, 103.59035),
    "宝林寺山门": (41.45638, 121.55976),
    "宝林寺正殿": (41.45638, 121.55976),
    "宝林寺禅堂": (41.45638, 121.55976),
    "宝玉书房": (36.66292, 116.99029),
    "宝玉屋": (36.66292, 116.99029),
    "宝玉屋·外间": (36.66292, 116.99029),
    "宝玉屋·里间": (36.66292, 116.99029),
    "宝玉房": (36.66292, 116.99029),
    "宝玉房·里间": (36.66292, 116.99029),
    "宝玉院内": (36.66292, 116.99029),
    "宝莲台": (12.0, 113.0),
    "宝莲台下": (12.0, 113.0),
    "宝莲座": (25.9266, 102.66763),
    "宝莲池": (12.0, 113.0),
    "宝钗新房": (36.66292, 116.99029),
    "审理厅": (40.05756, 116.57455),
    "客舍": (28.98214, 109.66636),
    "宣城": (22.56416, 113.95319),
    "宣平门": (34.26, 108.94),
    "宣德楼": (27.41786, 111.151),
    "家学": (39.51966, 116.22107),
    "寅将军洞府": (41.20904, 125.8874),
    "密室": (28.98214, 109.66636),
    "寇员外家": (42.59008, 124.43232),
    "寇家": (42.59008, 124.43232),
    "富池口": (31.0, 119.0),
    "寒冰阵": (28.98214, 109.66636),
    "寝宫": (39.51966, 116.22107),
    "寝殿": (34.62, 112.45),
    "寿仙宫": (35.60795, 114.19363),
    "封国": (30.5482, 120.08),
    "封神台": (28.98214, 109.66636),
    "小师桥": (31.8, 117.3),
    "小花厅": (36.66292, 116.99029),
    "小西天山坡": (34.89689, 108.5646),
    "小金桥": (28.98214, 109.66636),
    "小雷音寺后边": (37.40855, 120.98354),
    "少华山山寨": (34.51523, 109.75759),
    "尤三姐绣房": (27.7633, 112.548),
    "尤浑府": (35.60795, 114.19363),
    "山僻小路": (30.8681, 108.753),
    "山僻庄": (30.3, 112.2),
    "山凹中": (32.0827, 118.136),
    "山凹之间": (30.70271, 81.00236),
    "山凹草舍": (28.5469, 112.236),
    "山凹里": (21.56842, 110.00843),
    "山前树下": (36.66292, 116.99029),
    "山北凹": (30.24701, 111.62726),
    "山北凹里": (30.24701, 111.62726),
    "山南水寨": (28.5563, 92.55684),
    "山南里": (28.54541, 92.31998),
    "山坞": (36.0, 109.0),
    "山坡底下": (36.66292, 116.99029),
    "山坡桂树底下": (36.66292, 116.99029),
    "山子石": (36.66292, 116.99029),
    "山子石后": (36.66292, 116.99029),
    "山巅": (28.5469, 112.236),
    "山川社稷": (35.60795, 114.19363),
    "山石之后": (36.66292, 116.99029),
    "山石背后": (36.66292, 116.99029),
    "山窟窿": (36.66292, 116.99029),
    "山脚石穴": (24.58542, 106.52965),
    "山脚边": (36.66292, 116.99029),
    "山野": (33.81265, 113.00431),
    "山门外头墙根": (36.66292, 116.99029),
    "山门外头墙根下": (36.66292, 116.99029),
    "山阳昌邑": (33.45611, 109.96333),
    "山阴": (30.5, 104.0),
    "岐山": (30.5482, 120.08),
    "岐山纣营": (34.41139, 107.69972),
    "岐州": (28.98214, 109.66636),
    "峨嵋山": (30.5, 104.0),
    "峨眉山清凉洞": (29.52, 103.33),
    "峨眉山罗浮洞": (29.52, 103.33),
    "峪口": (30.5, 104.0),
    "崆峒山元阳洞": (40.33112, 119.40086),
    "崇营": (37.5, 115.5),
    "崇营中军": (37.5, 115.5),
    "崇营后营": (37.5, 115.5),
    "崇营辕门": (37.5, 115.5),
    "崇阁": (36.66292, 116.99029),
    "崔毅庄": (34.918, 113.361),
    "川中": (30.5, 104.0),
    "巢湖口": (31.4313, 117.8323),
    "左城": (29.66722, 112.86021),
    "巨鹿郡": (30.5, 104.0),
    "巴丘": (31.0, 119.0),
    "巴西梓潼": (31.4, 106.4),
    "巴西阆中": (31.4, 106.4),
    "巴陵郡": (31.0, 119.0),
    "市曹": (22.53949, 107.45718),
    "市镇": (35.5, 111.0),
    "布塞亭": (25.64766, 117.21688),
    "布金寺": (32.13497, 119.07287),
    "帘杏溪桃": (36.66292, 116.99029),
    "常山": (38.0, 114.5),
    "平坦宽阔大路": (36.66292, 116.99029),
    "平川": (31.5, 119.0),
    "平昌门": (35.60795, 114.19363),
    "幽尼佛寺": (36.66292, 116.99029),
    "幽魂白骨": (34.36915, 109.1915),
    "庄农人家": (45.09034, 127.11454),
    "庐江": (30.57, 104.07),
    "度月": (36.66292, 116.99029),
    "度月门": (36.66292, 116.99029),
    "庭下": (30.77592, 120.70525),
    "延津": (30.5, 104.0),
    "廷尉厅": (30.5, 104.0),
    "建业·皇宫": (32.06, 118.8),
    "建威": (30.5, 104.0),
    "建宁": (30.5, 104.0),
    "建平": (30.5, 104.0),
    "建章殿": (34.62, 112.45),
    "异人庄": (35.60795, 114.19363),
    "异箱轩": (32.11409, 119.17504),
    "弘农": (37.5, 112.0),
    "弘农县": (34.59543, 119.13163),
    "张嶷寨": (33.1, 107.0),
    "张布府": (32.06, 118.8),
    "张桂芳大营": (28.98214, 109.66636),
    "张桂芳营": (36.0, 84.0),
    "张桂芳营·辕门": (36.0, 84.0),
    "张翼寨": (33.1, 107.0),
    "张辽宅": (34.0, 113.8),
    "张都监宅": (34.91667, 112.75),
    "张青家": (34.91667, 112.75),
    "张飞寨": (34.26, 117.19),
    "张飞庄": (39.5, 115.9),
    "弱河": (31.9349, 104.087),
    "彝陵": (30.3, 112.2),
    "彝陵城": (30.3, 112.2),
    "彝陵营": (30.3, 112.2),
    "彩楼": (29.52, 103.33),
    "彭城": (34.26, 117.19),
    "往后角门": (36.66292, 116.99029),
    "待客馆": (28.58111, 112.95333),
    "後厅": (28.98214, 109.66636),
    "後花园": (39.78302, 113.02171),
    "後营": (30.5482, 120.08),
    "徐州·馆驿": (34.26, 117.19),
    "御书阁": (35.60795, 114.19363),
    "御园": (35.60795, 114.19363),
    "御林军营": (34.0, 113.8),
    "御案": (40.05756, 116.57455),
    "御花园": (35.60795, 114.19363),
    "德州平原县": (37.5, 112.0),
    "怡红院·书架后": (36.66292, 116.99029),
    "怡红院·后门": (36.66292, 116.99029),
    "怡红院·回廊": (36.66292, 116.99029),
    "怡红院·抱厦": (36.66292, 116.99029),
    "怡红院·暖阁": (36.66292, 116.99029),
    "怡红院·里间": (36.66292, 116.99029),
    "总兵府": (34.36915, 109.1915),
    "总章观": (34.0, 113.8),
    "悬崖削壁": (21.56842, 110.00843),
    "惜春卧房": (36.66292, 116.99029),
    "惜春房": (36.66292, 116.99029),
    "惜春院": (36.66292, 116.99029),
    "惜春院内": (36.66292, 116.99029),
    "惠陵": (30.5, 104.0),
    "慈云寺": (23.36558, 116.69761),
    "慈云寺后园": (23.36558, 116.69761),
    "慎县": (33.0, 114.0),
    "成汤大营": (30.5482, 120.08),
    "成汤营": (28.98214, 109.66636),
    "成汤营·中军": (28.98214, 109.66636),
    "成汤营·右营": (28.98214, 109.66636),
    "成汤营·后营": (28.98214, 109.66636),
    "成汤营·左营": (28.98214, 109.66636),
    "成汤营·辕门": (28.98214, 109.66636),
    "成皋": (30.5482, 120.08),
    "截教": (31.03054, 106.78437),
    "截教道场": (31.03054, 106.78437),
    "房陵": (30.87709, 103.59035),
    "扈家庄": (39.54948, 113.57455),
    "打麦场": (36.5, 117.0),
    "抄事房": (22.53949, 107.45718),
    "折带朱栏板桥": (36.66292, 116.99029),
    "披素亭": (34.89689, 108.5646),
    "抱厦厅": (30.48328, 118.95604),
    "抱犊山": (38.0, 114.5),
    "探春卧室": (36.66292, 116.99029),
    "探春房": (39.22237, 121.66548),
    "探春院": (36.66292, 116.99029),
    "摘星楼": (35.60795, 114.19363),
    "摩陂": (34.0, 113.8),
    "操寨": (31.8291, 108.98),
    "操营": (31.8291, 108.98),
    "敌楼": (30.5482, 120.08),
    "救生寺": (32.73126, 110.23837),
    "敖仓": (30.5, 104.0),
    "教军场": (28.98214, 109.66636),
    "教场": (35.60795, 114.19363),
    "散关": (33.1, 107.0),
    "文书房": (27.71637, 111.51171),
    "文武衙门": (29.8904, 104.328),
    "文陵": (30.5, 104.0),
    "斜谷口": (33.9, 107.5),
    "斜谷道": (33.9, 107.5),
    "断金亭": (35.79019, 116.12797),
    "断金亭子": (35.79019, 116.12797),
    "方椿家": (31.0959, 108.13693),
    "旁门": (41.45638, 121.55976),
    "无为军": (22.53949, 107.45718),
    "日规台": (30.77592, 120.70525),
    "旧宅": (30.48328, 118.95604),
    "旧所": (36.66292, 116.99029),
    "旱寨": (32.0, 118.0),
    "旻天县": (40.05756, 116.57455),
    "昆仑山玉虚宫": (36.0, 84.0),
    "昆仑山金霞岭": (36.0, 84.0),
    "昆仑山麒麟崖": (36.0, 84.0),
    "易京楼": (39.9, 116.4),
    "易州": (22.52306, 113.37912),
    "昭德城北十里": (36.5, 117.0),
    "昭德城北门": (36.5, 117.0),
    "昭德城南门": (36.5, 117.0),
    "昭明宫": (32.06, 118.8),
    "昭烈之庙": (30.57, 104.07),
    "昭烈庙": (30.57, 104.07),
    "昱岭关": (30.23333, 119.71667),
    "显庆殿": (35.60795, 114.19363),
    "显德殿": (35.60795, 114.19363),
    "晁盖庄上": (28.62753, 120.76732),
    "晋国": (30.5482, 120.08),
    "晋宁": (35.5, 111.0),
    "晋朝": (30.28742, 111.38034),
    "晓翠堂": (36.66292, 116.99029),
    "普陀山": (12.0, 113.0),
    "普陀山落伽洞": (34.2598, 117.277),
    "普陀岩": (12.0, 113.0),
    "普陀崖": (12.0, 113.0),
    "晴雯屋": (36.66292, 116.99029),
    "晴雯旧屋": (36.66292, 116.99029),
    "晴雯被窝里": (36.66292, 116.99029),
    "暴纱亭": (28.58111, 112.95333),
    "曲径": (36.66292, 116.99029),
    "曹兵寨": (23.55553, 110.06518),
    "曹兵旱寨": (31.8, 117.3),
    "曹军水寨": (32.0, 118.0),
    "曹国": (30.5482, 120.08),
    "曹寨": (32.0, 118.0),
    "曹操势力范围": (30.83116, 111.83337),
    "曹操寨": (30.83116, 111.83337),
    "曹操府": (30.83116, 111.83337),
    "曹操旱寨": (30.83116, 111.83337),
    "曹操水寨": (30.83116, 111.83337),
    "曹操营寨": (30.83116, 111.83337),
    "曹爽府": (34.0, 113.8),
    "曹爽府宅": (34.0, 113.8),
    "曹真寨": (33.9, 107.5),
    "曹真寨·中军帐": (33.9, 107.5),
    "曹营": (34.0, 113.8),
    "曼荆凹": (28.5469, 112.236),
    "月下": (34.09509, 117.4115),
    "月洞窗": (36.66292, 116.99029),
    "月洞门": (36.66292, 116.99029),
    "有凤来仪": (36.66292, 116.99029),
    "望经楼": (39.51966, 116.22107),
    "朝会殿": (28.98214, 109.66636),
    "朝内": (32.06, 118.8),
    "朝歌·偏殿": (27.71637, 111.51171),
    "朝歌·后宫": (35.60795, 114.19363),
    "朝歌·御花园": (35.60795, 114.19363),
    "朝歌·正宫": (35.60795, 114.19363),
    "朝歌·馆驿": (35.60795, 114.19363),
    "朝歌东门": (35.60795, 114.19363),
    "朝歌南门": (35.60795, 114.19363),
    "朝歌城四门": (35.60795, 114.19363),
    "朝歌城馆驿": (35.60795, 114.19363),
    "朝歌西门": (35.60795, 114.19363),
    "朝门之外": (23.5633, 116.2691),
    "朝门外": (23.5633, 116.2691),
    "朝阳殿": (34.0, 113.8),
    "木门道": (30.33, 112.24),
    "木香棚": (36.3599, 118.079),
    "本寨·中军帐": (25.6858, 107.761),
    "本观塔下": (36.66292, 116.99029),
    "朱褒营": (30.87709, 103.59035),
    "朱贵酒店": (35.79019, 116.12797),
    "机密房": (36.5, 117.0),
    "李典军中": (36.0, 109.0),
    "李纨妆奁": (36.66292, 116.99029),
    "李纨房": (36.66292, 116.99029),
    "杏帘在望": (36.66292, 116.99029),
    "杏树下": (39.51966, 116.22107),
    "杏花园": (29.0, 126.0),
    "村店": (31.49633, 113.14751),
    "村酒店": (29.75007, 118.61568),
    "杜元铣": (35.60795, 114.19363),
    "杜太师府": (35.60795, 114.19363),
    "杞国": (30.5482, 120.08),
    "杨老庄院": (33.84772, 106.43039),
    "杨雄家": (38.0, 114.5),
    "松根旁": (30.77592, 120.70525),
    "极乐世界": (29.52, 103.33),
    "枕翠庵": (36.66292, 116.99029),
    "枕霞阁": (29.64136, 104.99892),
    "林如海家": (32.41161, 119.45177),
    "林子里": (37.40855, 120.98354),
    "枢密院": (34.8, 114.35),
    "枫树底下": (36.66292, 116.99029),
    "枯井": (30.5, 104.0),
    "枹罕": (36.0, 109.0),
    "柏梁台": (34.26, 108.94),
    "查渎": (30.5, 104.0),
    "柳叶渚": (36.66292, 116.99029),
    "柳叶渚至潇湘馆": (36.66292, 116.99029),
    "柳城": (32.5, 117.0),
    "柳嫂子家": (36.66292, 116.99029),
    "柳林坡": (25.4586, 100.545),
    "柳树下": (35.60795, 114.19363),
    "柴桑界首": (29.7, 116.0),
    "柴桑郡": (29.7, 116.0),
    "柴进庄院": (38.26667, 116.75),
    "栏外山石后": (36.66292, 116.99029),
    "树林子后头": (36.66292, 116.99029),
    "桃花园": (36.0, 84.0),
    "桃花水": (30.5, 104.0),
    "桃花渡口": (30.5, 104.0),
    "桐槛": (36.66292, 116.99029),
    "梁都洞": (34.26, 117.19),
    "梓潼郡": (30.5, 104.0),
    "梦中花园": (45.09034, 127.11454),
    "棋盘山": (28.49228, 112.7542),
    "棠木舡": (36.66292, 116.99029),
    "椒房": (34.65222, 105.80699),
    "楚关": (30.5, 104.0),
    "楚国": (37.5, 115.5),
    "楼桑村": (39.5, 115.9),
    "楼船": (31.0, 119.0),
    "榆柳庄": (31.22, 120.13),
    "榆桥门": (30.57, 104.07),
    "榆荫堂": (36.66292, 116.99029),
    "槐绿低窗": (36.66292, 116.99029),
    "樊口": (31.0, 119.0),
    "横江": (30.5, 104.0),
    "横门外": (30.5, 104.0),
    "檀溪": (30.5, 104.0),
    "正南上": (40.05756, 116.57455),
    "正南街": (41.11708, 118.49635),
    "正宫": (35.60795, 114.19363),
    "正宫·后宫": (35.60795, 114.19363),
    "正殿门": (30.77592, 120.70525),
    "正面楼上": (39.51966, 116.22107),
    "此岸": (22.27723, 109.15539),
    "步廊": (42.64188, 130.38515),
    "武关": (30.5, 104.0),
    "武功": (24.84087, 114.94226),
    "武吉家": (28.98214, 109.66636),
    "武大家": (36.15889, 115.85139),
    "武学": (27.41786, 111.151),
    "武库": (30.5, 104.0),
    "武成王府": (35.60795, 114.19363),
    "武担山": (30.57, 104.07),
    "武昌东关": (30.61093, 114.35327),
    "武昌南郊": (30.61093, 114.35327),
    "武王寝宫": (28.98214, 109.66636),
    "武门": (39.78302, 113.02171),
    "武陵城下": (29.05717, 111.67611),
    "武陵界": (29.05717, 111.67611),
    "死囚牢": (34.60417, 114.49722),
    "段谷": (33.0, 114.0),
    "殷上": (35.60795, 114.19363),
    "殷洪营": (28.98214, 109.66636),
    "殷洪行营": (28.98214, 109.66636),
    "殷郊营": (28.98214, 109.66636),
    "比干府": (35.60795, 114.19363),
    "毛颖山": (40.05756, 116.57455),
    "水府": (26.1025, 105.879),
    "水府之西": (26.1025, 105.879),
    "水晶宫": (26.1025, 105.879),
    "水脏洞": (22.53949, 107.45718),
    "水鼋之第": (31.18756, 106.70378),
    "永安": (30.5, 104.0),
    "永安城": (30.5, 104.0),
    "永安宫": (30.5, 104.0),
    "永镇华夷阁": (35.60795, 114.19363),
    "汉嘉郡": (30.33, 112.24),
    "汉水": (33.1, 107.0),
    "汉水之西": (33.1, 107.0),
    "汉江": (27.1276, 116.51384),
    "汉津": (30.5, 104.0),
    "汉阳郡": (30.58222, 114.02018),
    "汜水关": (28.98214, 109.66636),
    "汜水关·辕门": (28.98214, 109.66636),
    "汜水关·馆驿": (28.98214, 109.66636),
    "汜水关帅府": (28.98214, 109.66636),
    "汜水关教场": (28.98214, 109.66636),
    "江东·馆驿": (31.0, 119.0),
    "江南水寨": (30.5, 120.0),
    "江岸": (30.87709, 103.59035),
    "江州牢城营": (22.53949, 107.45718),
    "江州私衙": (32.0827, 118.136),
    "江津": (30.70271, 81.00236),
    "江渚": (30.87709, 103.59035),
    "江西粮道衙门": (27.66667, 115.66667),
    "池上芙蓉": (36.3599, 118.079),
    "池中滴翠亭": (36.66292, 116.99029),
    "池边": (36.66292, 116.99029),
    "沁芳亭子": (36.66292, 116.99029),
    "沁芳桥": (36.66292, 116.99029),
    "沁芳泉": (36.66292, 116.99029),
    "沁芳溪": (36.66292, 116.99029),
    "沁芳闸": (36.66292, 116.99029),
    "沁芳闸桥": (36.66292, 116.99029),
    "沂都": (34.26, 117.19),
    "沈岭": (37.5, 112.0),
    "沓中": (27.8609, 112.874),
    "沔阳": (33.1, 107.0),
    "沙滩空地": (29.52, 103.33),
    "沛城": (34.26, 117.19),
    "沛城西门": (34.26, 117.19),
    "沧州牢城营": (38.26667, 116.75),
    "河北岸": (38.0, 114.5),
    "河南尹": (34.0, 113.5),
    "河心": (31.18756, 106.70378),
    "河渎": (29.0, 126.0),
    "油江口": (32.0, 118.0),
    "法华寺": (36.5, 117.0),
    "法司": (35.60795, 114.19363),
    "法门": (22.27723, 109.15539),
    "泛水关": (29.52, 103.33),
    "泰山郡": (36.25, 117.1),
    "泰山险路": (36.25, 117.1),
    "泾河水府": (26.1025, 105.879),
    "洛水之南": (31.24742, 104.05422),
    "洛阳·后宫": (34.62, 112.45),
    "洛阳南门外": (34.62, 112.45),
    "洪州城隍": (34.63055, 112.39516),
    "洪江口": (27.2329, 110.1205),
    "洪福寺": (39.51966, 116.22107),
    "洪锦中军帐": (28.98214, 109.66636),
    "洪锦营": (28.98214, 109.66636),
    "洹水": (30.5, 104.0),
    "流沙河界": (34.59543, 119.13163),
    "浅山": (30.33, 112.24),
    "测字摊": (42.02639, 121.73194),
    "济北": (22.56416, 113.95319),
    "济州·馆驿": (36.5, 117.0),
    "济州府": (36.5, 117.0),
    "济州府衙": (36.5, 117.0),
    "济郡": (33.0, 114.0),
    "浚山": (37.5, 115.5),
    "浮桥": (34.93694, 109.735),
    "海底": (31.40006, 121.22955),
    "海棠轩": (27.8671, 87.4139),
    "海榴亭": (35.60795, 114.19363),
    "海疆": (42.02639, 121.73194),
    "海藏": (29.0, 126.0),
    "涂中": (30.5, 104.0),
    "涌金门": (30.29365, 120.16142),
    "涪关": (30.5, 104.0),
    "涪水": (30.5, 104.0),
    "涪水关": (30.5, 104.0),
    "淮南径路": (32.5, 117.0),
    "淮水": (30.5, 104.0),
    "清凉瓦舍": (36.66292, 116.99029),
    "清华仙府": (29.8253, 103.24634),
    "清华庄": (25.4586, 100.545),
    "清堂茅舍": (36.66292, 116.99029),
    "清幽观宇": (22.53949, 107.45718),
    "清溪县帮源洞": (28.982, 120.078),
    "清虚观·大殿": (27.7633, 112.548),
    "清虚观·山门": (45.09034, 127.11454),
    "渑池县城下": (34.83333, 111.8),
    "渑池县帅府": (34.83333, 111.8),
    "渑池城下": (34.83333, 111.8),
    "渔阳": (39.9, 116.4),
    "渤海岸": (33.81265, 113.00431),
    "温德殿": (34.62, 112.45),
    "温明园": (34.62, 112.45),
    "渭北": (38.0, 114.5),
    "渭北寨": (38.0, 114.5),
    "渭口": (30.5, 104.0),
    "渭水南山": (27.87131, 118.23894),
    "渭水浮桥": (27.87131, 118.23894),
    "渭滨南岸": (34.28548, 107.08801),
    "港洞": (36.66292, 116.99029),
    "游魂关": (30.5482, 120.08),
    "湘云厢房": (36.3599, 118.079),
    "滏水": (30.5, 104.0),
    "滑州界首": (34.62, 112.45),
    "滕国": (30.5482, 120.08),
    "滥口": (27.8609, 112.874),
    "滴水檐": (27.71637, 111.51171),
    "滴翠亭": (36.66292, 116.99029),
    "潇湘馆·回廊": (36.66292, 116.99029),
    "潇湘馆·套间": (36.66292, 116.99029),
    "潇湘馆·里间": (36.66292, 116.99029),
    "潇湘馆回廊": (36.66292, 116.99029),
    "潇湘馆外": (36.66292, 116.99029),
    "潇湘馆外间空床": (36.66292, 116.99029),
    "潇湘馆平台": (36.66292, 116.99029),
    "潇湘馆里间": (36.66292, 116.99029),
    "潇湘馆门口": (36.66292, 116.99029),
    "潘公家": (38.0, 114.5),
    "潭岸": (41.10406, 114.45016),
    "潮音洞": (12.0, 113.0),
    "潮音洞外": (12.0, 113.0),
    "潼关城上": (34.48639, 110.26361),
    "濡须": (31.0, 119.0),
    "濡须口": (31.0, 119.0),
    "濡须坞": (31.0, 119.0),
    "濡须城": (31.0, 119.0),
    "濦水": (30.5, 104.0),
    "瀛洲海岛": (34.63055, 112.39516),
    "灌口": (34.89689, 108.5646),
    "火焰山土地庙": (25.8042, 107.496),
    "火焰山界": (25.8042, 107.496),
    "灯楼": (40.05756, 116.57455),
    "灵山后崖化龙池": (22.27723, 109.15539),
    "灵山大雷音宝刹": (22.27723, 109.15539),
    "灵山胜境": (22.27723, 109.15539),
    "灵感大王庙": (32.73126, 110.23837),
    "灵旁": (30.48328, 118.95604),
    "灵隐寺": (36.7, 118.5),
    "灵霄殿": (28.98214, 109.66636),
    "灵霄殿外": (28.98214, 109.66636),
    "灵鹫山元觉洞": (31.03054, 106.78437),
    "灵鹫高峰": (22.27723, 109.15539),
    "炮烙": (41.49235, 122.4116),
    "炮烙之刑": (41.49235, 122.4116),
    "点视厅": (22.53949, 107.45718),
    "烈阵": (28.98214, 109.66636),
    "照墙": (35.60795, 114.19363),
    "燕国": (30.5482, 120.08),
    "燕山": (28.98214, 109.66636),
    "牂牁": (30.5, 104.0),
    "牛渚": (31.0, 119.0),
    "牡丹丛": (33.88391, 120.28412),
    "牡丹亭": (35.60795, 114.19363),
    "犄角子": (36.66292, 116.99029),
    "独龙冈前": (39.54948, 113.57455),
    "独龙山前": (39.54948, 113.57455),
    "狮子崖": (28.80315, 114.94527),
    "狱中": (34.62, 112.45),
    "猇亭": (31.31888, 104.56003),
    "猛兽崖": (24.58542, 106.52965),
    "獬豸洞": (21.56842, 110.00843),
    "獬豸洞·二门": (21.56842, 110.00843),
    "獬豸洞·后宫": (21.56842, 110.00843),
    "獬豸洞外": (21.56842, 110.00843),
    "玄德公馆": (34.0, 113.8),
    "玄德寨": (34.26, 117.19),
    "玄德营寨": (30.3, 112.2),
    "玄武池": (34.0, 113.8),
    "玄英洞": (23.36558, 116.69761),
    "玄都洞八景宫": (31.8684, 105.912),
    "玉华县城": (28.58111, 112.95333),
    "玉华王府": (28.58111, 112.95333),
    "玉华王府前院": (28.58111, 112.95333),
    "玉屋洞": (29.5572, 121.044),
    "玉柱洞": (34.05, 108.85),
    "玉泉山金霞洞": (28.49228, 112.7542),
    "玉清观": (31.0, 119.0),
    "玉皇宝殿": (36.66292, 116.99029),
    "玉真观": (22.27723, 109.15539),
    "玉石牌坊": (36.66292, 116.99029),
    "玉英宫": (39.51966, 116.22107),
    "玉虚宫": (36.0, 84.0),
    "玉龙台": (36.3, 114.6),
    "王允宅": (34.26, 108.94),
    "王双营": (24.84087, 114.94226),
    "王夫人屋": (45.09034, 127.11454),
    "王奶奶家": (27.7633, 112.548),
    "王婆茶坊": (36.15889, 115.85139),
    "王宫": (23.5633, 116.2691),
    "王平寨": (25.6858, 107.761),
    "珍楼": (30.24958, 104.66357),
    "琅琊南阳": (32.287, 118.29807),
    "琅琊郡": (32.287, 118.29807),
    "琵琶亭": (23.31281, 116.12187),
    "瑞岩观": (32.91963, 118.54769),
    "瓦口关": (33.1, 107.0),
    "甄府": (31.3, 120.62),
    "甘宁寨": (31.0, 119.0),
    "甘陵": (30.5, 104.0),
    "甘霖普济寺": (40.05756, 116.57455),
    "田庄": (45.09034, 127.11454),
    "田畔": (32.01036, 112.08142),
    "画阁": (30.5, 104.0),
    "界牌关": (30.5482, 120.08),
    "界牌关·中军": (29.7135, 103.55397),
    "界牌关·辕门": (29.7135, 103.55397),
    "界牌关·银安殿": (29.7135, 103.55397),
    "界牌关帅府": (29.7135, 103.55397),
    "留云下院": (34.3425, 108.63556),
    "留守司": (39.9075, 116.39723),
    "留春亭": (35.60795, 114.19363),
    "畸角儿": (36.66292, 116.99029),
    "疆川口": (30.33, 112.24),
    "疏林": (33.0, 112.5),
    "瘟癀阵": (30.5482, 120.08),
    "瘟部": (36.5258, 116.995),
    "登仙阁": (30.48328, 118.95604),
    "白云岛": (29.0, 126.0),
    "白柳村": (28.98214, 109.66636),
    "白檀": (30.5, 104.0),
    "白河上流头": (32.71417, 109.92111),
    "白狼山": (31.11244, 112.6431),
    "白玉阶": (35.60795, 114.19363),
    "白莺林": (30.5482, 120.08),
    "白虎山": (36.7, 118.5),
    "白虎殿": (39.51966, 116.22107),
    "白骨洞": (34.2598, 117.277),
    "白鹤墩": (40.33112, 119.40086),
    "白鹿岛": (29.0, 126.0),
    "白鹿洞前": (29.0, 126.0),
    "白龙神庙": (22.53949, 107.45718),
    "百谷岭": (35.5, 111.0),
    "百里之外": (30.5, 104.0),
    "皇城": (28.98214, 109.66636),
    "皇天后土": (35.60795, 114.19363),
    "皇宫内院": (39.51966, 116.22107),
    "皇榜": (26.07711, 119.29153),
    "皖城": (30.5, 104.0),
    "皮袋": (29.68817, 106.58852),
    "盐渎": (36.5, 117.0),
    "监门": (42.59008, 124.43232),
    "盩厔山": (34.26, 108.94),
    "相国寺": (22.53949, 107.45718),
    "相府": (28.98214, 109.66636),
    "相府·银安殿": (28.98214, 109.66636),
    "相府书房": (28.98214, 109.66636),
    "相府左近宅院": (28.98214, 109.66636),
    "相府皇城": (28.98214, 109.66636),
    "省亲别院": (35.5565, 116.707),
    "省亲正殿": (36.3599, 118.079),
    "矮槐树": (30.77592, 120.70525),
    "石匣": (46.05841, 125.69651),
    "石后大桂树阴": (36.66292, 116.99029),
    "石城": (39.5, 115.9),
    "石头城": (32.06, 118.8),
    "石头崖": (28.5469, 112.236),
    "石屋虚堂": (23.7188, 102.815),
    "石屏山坡": (23.73385, 102.44474),
    "石桌": (25.4586, 100.545),
    "石桥三港": (36.66292, 116.99029),
    "石榴树下": (42.02639, 121.73194),
    "石矶": (39.51966, 116.22107),
    "石邑县": (38.0, 114.5),
    "碧波潭": (41.10406, 114.45016),
    "碧游": (34.05, 108.85),
    "碧游宫": (37.5, 115.5),
    "碧游床": (34.05, 108.85),
    "碧空": (40.05756, 116.57455),
    "碧纱": (29.8253, 103.24634),
    "碾房": (40.05756, 116.57455),
    "磐河": (34.26, 117.19),
    "祁山之东": (29.84431, 117.71937),
    "祁山之右": (29.84431, 117.71937),
    "祁山之左": (29.84431, 117.71937),
    "祁山之西": (29.84431, 117.71937),
    "祁山九寨": (29.84431, 117.71937),
    "祁山前": (29.84431, 117.71937),
    "祁山后": (29.84431, 117.71937),
    "祁山大寨": (29.84431, 117.71937),
    "祁山西路": (29.84431, 117.71937),
    "祝家庄庄门": (34.47236, 107.7806),
    "祝家庄门楼": (34.47236, 107.7806),
    "祭赛国·御花园": (41.11708, 118.49635),
    "禁中": (34.36915, 109.1915),
    "禅台": (35.60795, 114.19363),
    "私宅": (30.5, 104.0),
    "秃龙洞": (24.382, 103.11),
    "秋桐房": (31.0959, 108.13693),
    "秦川": (36.0, 109.0),
    "秦州": (30.5482, 120.08),
    "秦氏卧房": (30.48328, 118.95604),
    "穆太公庄院": (22.53949, 107.45718),
    "穰山": (30.5, 104.0),
    "空地": (29.80933, 116.96895),
    "穿云": (36.66292, 116.99029),
    "穿云关": (30.5482, 120.08),
    "穿云关·府": (30.5482, 120.08),
    "穿云关帅府": (30.5482, 120.08),
    "穿云关府": (30.5482, 120.08),
    "穿云关民家": (30.5482, 120.08),
    "穿云门": (36.66292, 116.99029),
    "窝铺": (23.7188, 102.815),
    "竹林": (12.0, 113.0),
    "笔峰": (36.0, 84.0),
    "第三关": (35.79019, 116.12797),
    "第二关": (38.03756, 114.64131),
    "箕关": (27.8609, 112.874),
    "箕谷": (30.33, 112.24),
    "箕谷口": (30.33, 112.24),
    "管国": (30.5482, 120.08),
    "篱门": (39.22237, 121.66548),
    "篷厂": (28.58111, 112.95333),
    "精舍": (36.66292, 116.99029),
    "紫云崖": (37.5, 115.5),
    "紫宸殿": (34.8, 114.35),
    "紫石街": (36.15889, 115.85139),
    "紫竹林": (12.0, 113.0),
    "紫芝崖": (37.5, 115.5),
    "紫虚观": (35.79019, 116.12797),
    "紫阳洞": (41.49958, 119.58752),
    "紫霄宫": (40.33112, 119.40086),
    "紫鹃房": (36.66292, 116.99029),
    "繁阳": (30.5, 104.0),
    "红水阵": (28.98214, 109.66636),
    "红沙阵": (28.98214, 109.66636),
    "红砖壁下": (25.8042, 107.496),
    "红草坡": (33.81265, 113.00431),
    "红香圃": (36.66292, 116.99029),
    "红香绿玉": (36.66292, 116.99029),
    "纣王营": (34.36915, 109.1915),
    "纣营": (34.41139, 107.69972),
    "纪国": (30.5482, 120.08),
    "终南山玉柱洞": (34.05, 108.85),
    "给孤布金寺": (40.05756, 116.57455),
    "绛芸轩": (36.66292, 116.99029),
    "绣房": (34.09509, 117.4115),
    "绿莎坡": (33.81265, 113.00431),
    "缀锦阁": (36.66292, 116.99029),
    "罗川口": (33.0, 112.5),
    "罗帐": (41.10406, 114.45016),
    "罾口川": (30.5, 120.0),
    "羌王寨": (30.5, 104.0),
    "美后宫": (25.4586, 100.545),
    "羑里": (30.5482, 120.08),
    "羑里城": (30.5482, 120.08),
    "羡溪": (31.0, 119.0),
    "群星列宿": (29.5188, 114.6814),
    "翠岩": (12.0, 113.0),
    "翠岩前": (12.0, 113.0),
    "翠花楼": (28.98214, 109.66636),
    "翰林院": (32.11409, 119.17504),
    "老妖洞府": (41.45638, 121.55976),
    "老妖洞府后园": (41.45638, 121.55976),
    "老营": (30.5482, 120.08),
    "聚铁山": (34.26, 117.19),
    "聚锦门": (36.66292, 116.99029),
    "肉林": (35.60795, 114.19363),
    "背阴山": (36.50443, 109.10787),
    "胡梯": (34.91667, 112.75),
    "舡坞": (36.66292, 116.99029),
    "艮山门": (30.29365, 120.16142),
    "节堂": (34.8, 114.35),
    "芍药圃": (36.66292, 116.99029),
    "芍药栏": (36.66292, 116.99029),
    "芙蓉池": (36.66292, 116.99029),
    "芦篷": (28.98214, 109.66636),
    "芦花荡": (23.09546, 113.79195),
    "花亭": (22.53949, 107.45718),
    "花亭子": (22.53949, 107.45718),
    "花冢": (36.66292, 116.99029),
    "花果山·中军帐": (28.5469, 112.236),
    "花果山·辕门": (28.5469, 112.236),
    "花果山水帘洞": (28.5469, 112.236),
    "花果山水帘洞外": (28.5469, 112.236),
    "花溆": (36.66292, 116.99029),
    "花障子": (36.66292, 116.99029),
    "芳林园": (34.0, 113.8),
    "苍龙": (32.06, 118.8),
    "苏侯中军帐": (28.98214, 109.66636),
    "苏侯大营": (28.98214, 109.66636),
    "苏侯营中军": (30.5482, 120.08),
    "苏侯营外": (28.98214, 109.66636),
    "苏侯行营": (30.5482, 120.08),
    "苏护营": (28.98214, 109.66636),
    "茂林": (28.98214, 109.66636),
    "茅屋": (39.22237, 121.66548),
    "茅庐": (36.0, 109.0),
    "茉藜槛": (41.11708, 118.49635),
    "荆南诸县": (30.33, 112.24),
    "荆州·馆驿": (30.33, 112.24),
    "荆州城门外": (30.00407, 112.50683),
    "荆州大牢": (30.33, 112.24),
    "荆州界首": (30.33, 112.24),
    "荇叶渚": (36.66292, 116.99029),
    "荇褐红篱": (36.66292, 116.99029),
    "草堂": (39.78302, 113.02171),
    "草屋": (38.26667, 116.75),
    "草庐": (33.0, 112.5),
    "草科": (36.50443, 109.10787),
    "草舍": (36.0, 84.0),
    "荒坟": (25.4586, 100.545),
    "荒郊草地": (28.49228, 112.7542),
    "荡石寨": (30.80966, 107.05204),
    "荣府东大院": (27.7633, 112.548),
    "荥阳·馆驿": (34.78333, 113.35),
    "荼蘼架": (36.66292, 116.99029),
    "莒国": (30.5482, 120.08),
    "莲花池": (12.0, 113.0),
    "莲花洞·正厅": (25.50083, 111.98278),
    "菜园": (30.77592, 120.70525),
    "菜市门": (30.29365, 120.16142),
    "菡萏阵": (29.5188, 114.6814),
    "营前": (26.1025, 105.879),
    "营前·中军帐": (25.96707, 119.46322),
    "萧关": (30.87709, 103.59035),
    "萧怀": (27.8609, 112.874),
    "落伽山": (12.0, 113.0),
    "落伽崖": (12.0, 113.0),
    "落凤坡": (30.5, 104.0),
    "落日山": (31.0, 119.0),
    "落魂阵": (28.98214, 109.66636),
    "葛陂": (30.5, 104.0),
    "董亭": (30.5, 104.0),
    "董卓寨": (36.7, 118.5),
    "董承宅": (34.0, 113.8),
    "董承府": (34.23694, 108.95389),
    "董荼那寨": (30.87709, 103.59035),
    "董重府宅": (34.62, 112.45),
    "蒋陵": (30.5, 104.0),
    "蒙头寨": (30.80966, 107.05204),
    "蒙头岩": (33.1, 107.0),
    "蒲团": (36.66292, 116.99029),
    "蒲阪津": (39.5, 115.9),
    "蓟县": (30.5482, 120.08),
    "蓟国": (30.5482, 120.08),
    "蓟州城里": (38.0, 114.5),
    "蓬厂": (28.58111, 112.95333),
    "蓬莱岛": (29.0, 126.0),
    "蓬菜岛": (41.49958, 119.58752),
    "蓼汀花溆": (36.66292, 116.99029),
    "蓼溆": (36.66292, 116.99029),
    "蔡国": (30.5482, 120.08),
    "蔡邕庄": (34.2, 109.3),
    "蔷薇架": (36.66292, 116.99029),
    "蔷薇花架": (36.66292, 116.99029),
    "蔷薇院": (36.66292, 116.99029),
    "蕲黄地面": (31.0, 119.0),
    "薛国": (30.5482, 120.08),
    "薛家厅房后面": (31.86094, 119.91437),
    "薛家铺面": (31.86094, 119.91437),
    "蘅芜苑·床上": (36.66292, 116.99029),
    "蘅芜苑·廊檐": (36.66292, 116.99029),
    "蘅芜苑·案上": (36.66292, 116.99029),
    "蘅芜院": (36.66292, 116.99029),
    "蘅芜院·抱厦": (36.66292, 116.99029),
    "蘅芷清芬": (36.66292, 116.99029),
    "虎儿崖": (34.05, 108.85),
    "虎兒崖": (34.05, 108.85),
    "虎林": (37.5, 115.5),
    "虚皇坛": (28.12346, 116.97592),
    "虞国": (30.5482, 120.08),
    "虢国": (30.5482, 120.08),
    "虿盆": (35.60795, 114.19363),
    "蜀中": (30.33, 112.24),
    "蜀宫": (30.57, 104.07),
    "蜀寨": (29.84431, 117.71937),
    "蜀川": (30.33, 112.24),
    "蜀营": (30.33, 112.24),
    "蜂腰桥": (36.66292, 116.99029),
    "蟠龙岭": (34.83333, 112.46667),
    "蠙城": (32.91963, 118.54769),
    "血盆苦界": (27.2329, 110.1205),
    "行宫": (36.66292, 116.99029),
    "行营": (34.41139, 107.69972),
    "街市": (42.64188, 130.38515),
    "衣架": (32.11409, 119.17504),
    "袁术寨": (34.62, 112.45),
    "袁洪营": (34.83333, 112.46667),
    "袁洪营·中军": (34.83333, 112.46667),
    "袁洪营·辕门": (34.83333, 112.46667),
    "袁绍墓": (37.53333, 115.46667),
    "袁绍家": (37.5, 115.5),
    "袁绍寨": (34.8, 114.0),
    "袁绍营": (34.8, 114.0),
    "袭人家": (27.7633, 112.548),
    "褒中": (36.0, 109.0),
    "褒州": (37.21949, 116.08199),
    "襄平城": (43.52943, 123.53358),
    "襄江": (30.33, 112.24),
    "襄阳东门": (32.01, 112.14),
    "西京新安县": (27.41786, 111.151),
    "西凉州": (37.9, 102.6),
    "西华门": (34.8, 114.35),
    "西天大雷音寺": (30.30773, 106.88104),
    "西天路": (29.13828, 121.2473),
    "西天门外": (30.30773, 106.88104),
    "西宁郡王府": (39.51966, 116.22107),
    "西宁郡王祭棚": (30.48328, 118.95604),
    "西山": (31.0, 119.0),
    "西山小路": (31.0, 119.0),
    "西山庵": (31.0, 119.0),
    "西岐": (30.5482, 120.08),
    "西岐·辕门": (28.98214, 109.66636),
    "西岐·金殿": (28.98214, 109.66636),
    "西岐东门": (28.98214, 109.66636),
    "西岐北门": (28.98214, 109.66636),
    "西岐南门": (28.98214, 109.66636),
    "西岐南门外": (28.98214, 109.66636),
    "西岐城": (30.5482, 120.08),
    "西岐城·内宫": (28.98214, 109.66636),
    "西岐城·宫内": (28.98214, 109.66636),
    "西岐城东门": (28.98214, 109.66636),
    "西岐城北门": (28.98214, 109.66636),
    "西岐城南门": (28.98214, 109.66636),
    "西岐城敌楼": (28.98214, 109.66636),
    "西岐城相府": (28.98214, 109.66636),
    "西岐城芦篷": (28.98214, 109.66636),
    "西岐城西门": (28.98214, 109.66636),
    "西岐山": (30.5482, 120.08),
    "西岐战场": (28.98214, 109.66636),
    "西岐殿": (30.5482, 120.08),
    "西岐相府": (28.98214, 109.66636),
    "西岐纣营": (28.98214, 109.66636),
    "西岐芦篷": (28.98214, 109.66636),
    "西岐营": (30.5482, 120.08),
    "西岐营地": (28.98214, 109.66636),
    "西岳庙": (34.51523, 109.75759),
    "西岸": (30.5, 104.0),
    "西川鹄鸣山": (30.5, 104.0),
    "西掖门": (27.26705, 116.80926),
    "西方极乐世界": (22.53949, 107.45718),
    "西河": (37.5, 115.5),
    "西海九龙岛": (40.43397, 122.31321),
    "西羌": (30.5, 104.0),
    "西街门外": (39.51966, 116.22107),
    "西门外": (28.32818, 121.19824),
    "西门大街": (28.32818, 121.19824),
    "西门小河": (28.32818, 121.19824),
    "西门庆生药铺": (28.32818, 121.19824),
    "西陵桥": (30.24, 120.14),
    "观音院": (29.0, 126.0),
    "誊黄寺": (39.51966, 116.22107),
    "议事厅": (30.48328, 118.95604),
    "许国": (34.83333, 111.8),
    "许昌·后宫": (34.0, 113.8),
    "许昌之南原": (34.0, 113.8),
    "许都·后宫": (34.0, 113.8),
    "许都·馆驿": (34.0, 113.8),
    "诸营寨栅": (25.6858, 107.761),
    "诸路关隘": (24.84087, 114.94226),
    "谯国": (30.5, 104.0),
    "谯国谯县": (30.5, 104.0),
    "谯郡": (31.11244, 112.6431),
    "象牙床": (33.8309, 117.701),
    "豫山": (31.5, 119.0),
    "豫州界": (33.0, 114.0),
    "豫章": (30.5, 104.0),
    "豹头山": (28.58111, 112.95333),
    "豹头山虎口洞": (28.58111, 112.95333),
    "费仲本宅": (35.60795, 114.19363),
    "贾府园": (45.09034, 127.11454),
    "贾政上房": (30.48328, 118.95604),
    "贾政外厢房": (32.41161, 119.45177),
    "贾母内院": (36.66292, 116.99029),
    "贾母套间": (27.7633, 112.548),
    "贾母正面榻": (45.09034, 127.11454),
    "贾琏院": (25.31817, 110.29869),
    "贾芹书房": (29.8904, 104.328),
    "贾蓉居室": (30.48328, 118.95604),
    "贾雨村书房": (29.8904, 104.328),
    "赤井洞": (28.80315, 114.94527),
    "赤坡": (30.87709, 103.59035),
    "赤岸坡": (30.33, 112.24),
    "赤瑕宫": (45.09034, 127.11454),
    "赭山门": (30.51667, 120.95),
    "赵公明营地": (28.98214, 109.66636),
    "赵姨娘房": (30.48328, 118.95604),
    "越嶲": (30.5, 104.0),
    "跃龙潭": (34.62, 112.45),
    "跃龙祠": (34.62, 112.45),
    "轘辕": (30.5, 104.0),
    "轮藏": (22.53949, 107.45718),
    "轵道": (29.66722, 112.86021),
    "辽隧": (30.5, 104.0),
    "达摩庵": (36.66292, 116.99029),
    "迎春房": (36.66292, 116.99029),
    "退步": (36.66292, 116.99029),
    "逍遥津": (31.8, 117.3),
    "逍遥津北": (31.8, 117.3),
    "逗蜂轩": (25.31817, 110.29869),
    "通天教主道场": (37.5, 115.5),
    "通天河东岸": (31.18756, 106.70378),
    "通天河界": (31.18756, 106.70378),
    "通天河石匣": (31.18756, 106.70378),
    "通天河西岸": (31.18756, 106.70378),
    "通天河高崖": (31.18756, 106.70378),
    "邓九公帅府": (26.07925, 119.11205),
    "邓婵玉": (31.72913, 115.91241),
    "邢夫人正房": (27.7633, 112.548),
    "邱鸣山": (30.5482, 120.08),
    "邺城门": (36.3, 114.6),
    "邺郡高陵": (36.3, 114.6),
    "邺都": (34.26, 117.19),
    "邾县": (31.11244, 112.6431),
    "邾国": (30.5482, 120.08),
    "郏下": (30.0, 115.0),
    "郓城县县衙": (35.61972, 115.85556),
    "郕国": (30.5482, 120.08),
    "郡中": (40.05756, 116.57455),
    "郭常庄院": (35.5, 116.8),
    "都土地庙": (39.51966, 116.22107),
    "都城街道": (45.09034, 127.11454),
    "郿坞": (34.26, 108.94),
    "郿城": (35.5, 116.8),
    "里间小炕": (27.7633, 112.548),
    "重围中": (31.0, 119.0),
    "野冢": (31.9349, 104.087),
    "野寨": (27.8609, 112.874),
    "金亭馆": (39.51966, 116.22107),
    "金亭馆驿": (35.60795, 114.19363),
    "金亭馆驿·客房": (35.60795, 114.19363),
    "金亭馆驿客房": (35.60795, 114.19363),
    "金光洞": (30.5482, 120.08),
    "金光阵": (28.98214, 109.66636),
    "金凤台": (36.3, 114.6),
    "金城": (31.5, 119.0),
    "金墉城": (34.62, 112.45),
    "金山寺·法堂": (30.99206, 104.24048),
    "金平府城": (40.05756, 116.57455),
    "金平府府堂": (40.05756, 116.57455),
    "金庭山玉屋洞": (29.5572, 121.044),
    "金桂房": (31.86094, 119.91437),
    "金殿": (28.98214, 109.66636),
    "金沙滩小寨": (35.79019, 116.12797),
    "金灯桥": (23.36558, 116.69761),
    "金灯桥上": (23.36558, 116.69761),
    "金环三结大寨": (27.8609, 112.874),
    "金祎宅": (34.0, 113.8),
    "金銮宝殿": (35.60795, 114.19363),
    "金銮殿": (35.60795, 114.19363),
    "金雁桥": (30.98021, 104.28081),
    "金霞洞": (28.49228, 112.7542),
    "金鸡岭": (28.98214, 109.66636),
    "金龙幔帐": (35.60795, 114.19363),
    "钟会寨": (32.2, 105.5),
    "钟提": (30.5, 104.0),
    "钦法国": (27.23428, 115.91849),
    "钱塘": (36.5, 117.0),
    "铁笼山": (35.5, 116.8),
    "铜雀台": (30.95698, 112.07076),
    "银冾洞": (24.382, 103.11),
    "银子铺": (30.746, 109.80012),
    "锦云窝": (33.88391, 120.28412),
    "锦带山": (30.5, 104.0),
    "锦衣府": (27.7633, 112.548),
    "镐京": (30.5482, 120.08),
    "长史府": (28.58111, 112.95333),
    "长坂城": (30.83116, 111.83337),
    "长坂桥": (30.83116, 111.83337),
    "长安城·皇宫": (39.51966, 116.22107),
    "长安城·金銮殿": (39.51966, 116.22107),
    "长安城西门大街": (39.51966, 116.22107),
    "长朝殿": (35.60795, 114.19363),
    "长沙城下": (28.19874, 112.97087),
    "长沙郡": (28.23, 112.94),
    "长社": (27.8609, 112.874),
    "门斗": (42.02639, 121.73194),
    "闻太师府": (35.60795, 114.19363),
    "闻太师营": (28.98214, 109.66636),
    "阊门外": (31.3, 120.62),
    "阐教": (28.98214, 109.66636),
    "阮小二家": (36.5, 117.0),
    "阳平亭": (34.3379, 107.48986),
    "阳武": (27.8609, 112.874),
    "阳谷县县衙": (36.15889, 115.85139),
    "阳陵坡": (34.0, 113.5),
    "阴山背后": (29.52, 103.33),
    "阴平小路": (34.27728, 118.61231),
    "阴平桥": (34.27728, 118.61231),
    "阴间": (42.02639, 121.73194),
    "阶下矮槐树": (30.77592, 120.70525),
    "阿会喃寨": (30.5, 104.0),
    "陆口寨外临江亭": (34.32071, 119.00178),
    "陇上": (30.5, 104.0),
    "陇右": (30.5, 104.0),
    "陇西临洮": (35.0, 104.6),
    "陇西小路": (35.0, 104.6),
    "陇西诸郡": (35.0, 104.6),
    "陈仓古道": (34.4, 107.4),
    "陈国": (30.5482, 120.08),
    "陈塘关": (30.5482, 120.08),
    "陈塘关城楼": (27.8671, 87.4139),
    "陈塘关帅府": (27.8671, 87.4139),
    "陈塘关帅府后院": (27.8671, 87.4139),
    "陈家厢房": (29.60163, 104.80662),
    "陈家堂前": (29.60163, 104.80662),
    "陈家庄·花园": (32.73126, 110.23837),
    "陈家花园": (29.60163, 104.80662),
    "陈留平邱": (34.8, 114.3),
    "陡崖之下": (37.40855, 120.98354),
    "陵驿": (39.51966, 116.22107),
    "陷车": (28.98214, 109.66636),
    "隆中·林间": (32.01036, 112.08142),
    "雍闿寨": (36.0, 109.0),
    "雒县": (30.57, 104.07),
    "零陵": (30.3, 112.2),
    "雷部": (32.5714, 105.868),
    "雷音古刹": (29.24364, 104.22467),
    "雷音宝刹": (22.27723, 109.15539),
    "雷音寺·二门": (30.24958, 104.66357),
    "雷音寺山门": (30.24958, 104.66357),
    "霍国": (34.83333, 111.8),
    "青岱": (12.0, 113.0),
    "青峰山紫阳洞": (41.49958, 119.58752),
    "青溪": (42.02639, 121.73194),
    "青琐门": (27.26705, 116.80926),
    "青霄阁": (34.0, 113.8),
    "青鸾": (35.60795, 114.19363),
    "青鸾斗阙": (35.60795, 114.19363),
    "青龙关": (30.5482, 120.08),
    "青龙关大路": (40.33333, 119.13333),
    "青龙山": (23.36558, 116.69761),
    "青龙山玄英洞": (23.36558, 116.69761),
    "韩荣帅府": (28.98214, 109.66636),
    "韩遂营": (30.5, 104.0),
    "项岭": (30.33, 112.24),
    "须弥山摩耳崖": (26.43472, 118.83686),
    "颍阴": (36.0, 109.0),
    "风吼阵": (28.98214, 109.66636),
    "风庭月榭": (36.66292, 116.99029),
    "风炉": (39.51966, 116.22107),
    "飞云洞": (24.58542, 106.52965),
    "飞云浦": (34.91667, 112.75),
    "飞云阁": (35.60795, 114.19363),
    "飞凤山": (30.5482, 120.08),
    "飞凤山寨": (30.5482, 120.08),
    "飞廉府": (35.60795, 114.19363),
    "飞楼": (36.66292, 116.99029),
    "飞虎峪": (36.3, 115.23333),
    "饮马川": (38.0, 114.5),
    "馆舍": (37.5, 115.5),
    "馆陶": (30.5, 104.0),
    "香房": (28.98214, 109.66636),
    "香林洼": (35.80228, 116.11701),
    "马援庙": (24.382, 103.11),
    "马超坟墓": (33.1, 107.0),
    "驮梁之上": (36.941, 104.977),
    "骆谷": (30.33, 112.24),
    "骆谷道": (30.33, 112.24),
    "高堂大厦": (25.8042, 107.496),
    "高太公家": (34.3425, 108.63556),
    "高太公家后宅": (34.3425, 108.63556),
    "高峰排戟": (27.81208, 103.31547),
    "高平陵": (34.62, 112.45),
    "高老庄·厅堂": (34.3425, 108.63556),
    "高老庄后宅": (34.3425, 108.63556),
    "高阜": (36.50443, 109.10787),
    "魏宫": (34.26, 108.94),
    "魏寨": (29.84431, 117.71937),
    "魏延寨": (34.2, 107.6),
    "魏征府": (39.51966, 116.22107),
    "魔家四将军营": (28.98214, 109.66636),
    "魔家四将大营": (28.98214, 109.66636),
    "魔家四将营": (28.98214, 109.66636),
    "鱼池": (30.5, 104.0),
    "鱼腹浦": (31.52324, 118.62014),
    "鲁国": (30.5482, 120.08),
    "鲁国曲阜": (30.5, 104.0),
    "鳷鹊宫": (29.68817, 106.58852),
    "鸭嘴滩小寨": (35.79019, 116.12797),
    "鸳鸯楼": (34.91667, 112.75),
    "鹊尾坡": (32.5, 112.4),
    "鹫峰": (22.27723, 109.15539),
    "鹰愁陡涧": (36.50443, 109.10787),
    "鹿台·偏殿": (34.09509, 117.4115),
    "鹿台之下": (34.09509, 117.4115),
    "麒麟山獬豸洞": (21.56842, 110.00843),
    "麒麟崖": (36.0, 84.0),
    "麦城之北": (30.7, 111.8),
    "麴山": (30.5, 104.0),
    "麴山城": (30.5, 104.0),
    "黄堂": (42.59008, 124.43232),
    "黄州界首": (30.43755, 114.8748),
    "黄河阵": (35.0, 110.0),
    "黄泥冈": (36.5, 117.0),
    "黄滚府": (29.7135, 103.55397),
    "黄飞虎军营": (28.98214, 109.66636),
    "黎阳北岸": (35.68226, 114.56658),
    "黑水河中": (37.82707, 114.29897),
    "黑水河水府之西": (26.1025, 105.879),
    "黑水河神府": (37.82707, 114.29897),
    "黑虎府": (29.50131, 121.01407),
    "黑龙江": (30.5, 104.0),
    "黛山": (32.5, 117.0),
    "齐国": (30.5482, 120.08),
    "龙宫法界": (26.1025, 105.879),
    "龙德殿": (35.60795, 114.19363),
    "龙榻": (30.5, 104.0),
    "龙潭虎穴": (34.83333, 112.46667),
    "龙盘石柱": (35.60795, 114.19363),
    "龙神庙": (40.05756, 116.57455),
    "龙舟": (35.0, 110.0),
    "龙门山": (32.0, 116.0),
})
# ── GeoEvolve delta end ──
