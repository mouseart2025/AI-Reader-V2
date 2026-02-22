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

import io
import logging
import math
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

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
    # Historical regions / states
    "高昌": (42.86, 89.53),        # Gaochang (Turpan, Xinjiang)
    "楼兰": (40.52, 89.73),        # Loulan (ancient Xinjiang city)
    "龟兹": (41.72, 82.97),        # Kucha (Xinjiang)
    "于阗": (37.12, 79.92),        # Khotan (Xinjiang)
    "敦煌": (40.14, 94.66),        # Dunhuang
}

# Patterns indicating a name is NOT a real geographic place (for detection filtering)
_NON_GEO_PATTERNS = re.compile(
    r"(号$|车厢|车站|码头|包厢|酒吧|饭店|旅店|旅馆|俱乐部|协会|学会|法庭|银行|"
    r"商行|商店|办公|仓库|警[署卫]|领事馆|教堂$|大厅|售票|围墙|祭坛|行政|"
    r"火车$|列车|客轮|雪橇|甲板|大街|街$|院$|栅栏|灌木|丛林|树丛|"
    r"林间|密林|空[地场]|道上|河滨$|河边$|郊外$|花园$|舞台$|餐厅|烟馆|理发|"
    # Interior / positional — common in mansion/estate novels
    r"房$|房中$|房内$|房里$|门口$|门前$|门外$|"
    r"前边|后面|外边|里边|隔壁|对门|旁边|前头|外头|里头|上面|下面)"
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
        for lat, lng, pop, feat, cc, gid in entries:
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
    _indexes: dict[str, dict[str, list[GeoEntry]]] = {}

    def __init__(self, dataset_key: str = "cn") -> None:
        if dataset_key not in DATASET_REGISTRY:
            raise ValueError(f"Unknown geo dataset: {dataset_key!r}")
        self.dataset_key = dataset_key
        self.config = DATASET_REGISTRY[dataset_key]

    # ── Data download & loading ──────────────────────────

    def _tsv_path(self) -> Path:
        return GEONAMES_DIR / self.config.zip_member

    async def _ensure_data(self) -> None:
        """Download the dataset zip from GeoNames if the TSV doesn't exist."""
        tsv = self._tsv_path()
        if tsv.exists():
            return
        GEONAMES_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Downloading GeoNames dataset [%s] from %s ...",
            self.dataset_key, self.config.url,
        )
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.get(self.config.url)
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
        if not tsv.exists():
            raise FileNotFoundError(f"Expected {tsv} after extraction")
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
        """Ensure dataset is downloaded and index is loaded."""
        await self._ensure_data()
        self._load_index()

    def resolve_names(
        self, names: list[str],
        parent_map: dict[str, str | None] | None = None,
    ) -> dict[str, tuple[float, float]]:
        """Resolve a list of place names to (lat, lng) coordinates.

        Resolution order (curated data beats noisy data):
          1. Curated supplement dictionaries (CN historical terms, world entities)
          2. Chinese alternate name index (zh_geonames.tsv, world dataset only)
          3. Exact match from GeoNames index
          4. Suffix stripping (remove common Chinese geographic suffixes)
          5. Disambiguation: prefer higher admin level, then population

        Two-pass strategy when parent_map is provided:
          - Pass 1: resolve all names (exact + supplement matches first)
          - Pass 2: for suffix-stripped matches, validate against parent's
            coordinates. If parent is resolved and the match is > 1000km away,
            discard the dubious match (e.g., 维多利亚港→塞舌尔 when parent 香港
            is in Hong Kong).

        Returns dict of {name: (lat, lng)} for successfully resolved names.
        """
        index = self._load_index()
        use_zh_alias = self.dataset_key == "world"
        result: dict[str, tuple[float, float]] = {}
        suffix_stripped_names: set[str] = set()  # track which used suffix stripping

        for name in names:
            if not name or len(name) < 2:
                continue

            # Level 1: curated supplement (highest priority — prevents mismatches
            # like 西域→浙江西域村, 长安→石家庄长安区, 天山→内蒙古天山镇)
            sup = _SUPPLEMENT_CN.get(name) or _SUPPLEMENT_GEO.get(name)
            if sup:
                result[name] = sup
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
                    continue

            # Level 3: exact match from GeoNames
            entries = index.get(name)

            # Level 4: suffix stripping
            if not entries:
                stripped = _GEO_SUFFIXES.sub("", name)
                if stripped and stripped != name and len(stripped) >= 2:
                    entries = index.get(stripped)
                    if entries:
                        suffix_stripped_names.add(name)

            if entries:
                # Disambiguation: pick best entry
                best = _pick_best_entry(entries)
                result[name] = (best.lat, best.lng)

        # Pass 2: validate suffix-stripped matches against parent proximity
        if parent_map and suffix_stripped_names:
            to_remove: list[str] = []
            for name in suffix_stripped_names:
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
                if dist > 1000:
                    logger.info(
                        "Discarding suffix-stripped match %s→(%.1f,%.1f): "
                        "%.0fkm from parent %s→(%.1f,%.1f)",
                        name, child_coord[0], child_coord[1],
                        dist, parent, parent_coord[0], parent_coord[1],
                    )
                    to_remove.append(name)
            for name in to_remove:
                del result[name]

        logger.info(
            "GeoResolver[%s]: resolved %d / %d names (%.0f%%)",
            self.dataset_key, len(result), len(names),
            100 * len(result) / max(len(names), 1),
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
          - "mixed": >= 5% notable matches (historical/wuxia with real geography)
          - "fantasy": < 5% notable matches (mansion/xianxia/pure fiction)
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
        elif ratio >= 0.05:
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

    # Check world-level signals BEFORE genre-based routing
    # If the novel mentions multiple countries/continents/oceans, it's world-scope
    if location_names:
        world_matches = sum(1 for n in location_names if n in _SUPPLEMENT_GEO)
        if world_matches >= 3:
            logger.info(
                "detect_geo_scope: %d names match supplement → world scope",
                world_matches,
            )
            return "world"

    # Known Chinese genre → CN dataset
    if genre in ("historical", "wuxia"):
        return "cn"

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

    Uses golden-angle (sunflower seed) distribution for scatter placement.
    Jitter radius ≈ 0.3 degrees (~30km) to keep points close but distinct.

    Returns {name: (lat, lng)} for each unresolved name that could be placed.
    """
    if not resolved or not unresolved_names:
        return {}

    # Build reverse parent map: parent -> [children]
    children_of: dict[str, list[str]] = {}
    for child, parent in parent_map.items():
        if parent:
            children_of.setdefault(parent, []).append(child)

    # Fallback centroid: largest cluster, not global average
    fallback_centroid = _compute_largest_cluster_centroid(resolved)

    result: dict[str, tuple[float, float]] = {}
    jitter_radius = 0.3  # degrees (~30km)

    # Group unresolved by anchor strategy for better scatter
    groups: dict[str, list[str]] = {}  # anchor_key -> [names]
    anchors: dict[str, tuple[float, float]] = {}  # anchor_key -> (lat, lng)

    for name in unresolved_names:
        if name in resolved:
            continue  # already resolved

        anchor_coord = None
        anchor_key = None

        # Strategy 1: walk up parent chain to find ANY resolved ancestor
        ancestor_coord = _find_resolved_ancestor(name, parent_map, resolved)
        if ancestor_coord:
            # Use the direct parent's name for grouping if possible
            direct_parent = parent_map.get(name, "")
            anchor_coord = ancestor_coord
            anchor_key = f"ancestor:{direct_parent or name}"
        else:
            # Strategy 2: name containment (旧金山机场 → 旧金山)
            containment_coord = _find_name_containment_anchor(name, resolved)
            if containment_coord:
                anchor_coord = containment_coord
                anchor_key = f"contain:{name}"
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

        # Strategy 4: largest-cluster centroid
        if anchor_coord is None:
            anchor_coord = fallback_centroid
            anchor_key = "cluster"

        anchors[anchor_key] = anchor_coord
        groups.setdefault(anchor_key, []).append(name)

    # Place each group using sunflower seed distribution around its anchor
    for anchor_key, names in groups.items():
        center = anchors[anchor_key]
        n = len(names)
        for i, name in enumerate(names):
            angle = i * _GOLDEN_ANGLE
            # sqrt scaling fills the circle from center outward
            r = jitter_radius * (0.3 + 0.7 * math.sqrt((i + 1) / max(n, 1)))
            lat = center[0] + r * math.cos(angle)
            lng = center[1] + r * math.sin(angle)
            result[name] = (lat, lng)

    if result:
        logger.info(
            "place_unresolved_geo_coords: estimated %d / %d unresolved locations",
            len(result), len(unresolved_names),
        )
    return result
