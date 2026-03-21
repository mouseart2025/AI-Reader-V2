/**
 * 地图风格配置系统
 * 每种视觉风格定义为配置对象，所有渲染参数从配置读取。
 */

export interface TerrainStyle {
  fill: [number, number, number, number]  // RGBA, A 为 0-1
  height: number                           // 高斯高度 (Hillshade DEM)
  sigma: number                            // 高斯半径
}

export interface MapStyle {
  id: string
  name: string
  background: string
  paper: {
    noiseFrequency: number
    noiseOctaves: number
    noiseStrength: number
    stainOpacity: number
  }
  vignette: {
    start: number   // 从边缘多少%开始
    opacity: number
    color: string
  }
  terrain: Record<string, TerrainStyle>
  hillshade: {
    azimuth: number
    altitude: number
    zFactor: number
    blendMode: string
    opacity: number
  }
  label: {
    fontFamily: string
    color: string
    haloColor: string
    tierSizes: number[]  // [tier1, tier2, tier3, tier4, tier5, tier6]
  }
}

/** 羊皮纸奇幻（默认/仙侠） */
export const PARCHMENT_STYLE: MapStyle = {
  id: "parchment",
  name: "羊皮纸奇幻",
  background: "#f0e4d0",
  paper: {
    noiseFrequency: 0.65,
    noiseOctaves: 4,
    noiseStrength: 0.15,   // 降低纸张纹理强度，避免 multiply 吃掉 SDF 颜色
    stainOpacity: 0.05,
  },
  vignette: {
    start: 55,
    opacity: 0.2,
    color: "rgba(60,40,20,0.2)",
  },
  terrain: {
    // 提高饱和度：让不同区域在羊皮纸底色上可区分
    realm:    { fill: [180, 150, 90,  0.5], height: 40,   sigma: 150 },
    kingdom:  { fill: [130, 170, 95,  0.6], height: 55,   sigma: 120 },
    city:     { fill: [175, 140, 85,  0.6], height: 65,   sigma: 40 },
    town:     { fill: [185, 165, 110, 0.5], height: 60,   sigma: 35 },
    org:      { fill: [145, 110, 190, 0.6], height: 55,   sigma: 45 },
    mountain: { fill: [120, 100, 70,  0.7], height: 180,  sigma: 40 },
    hill:     { fill: [155, 140, 95,  0.6], height: 120,  sigma: 60 },
    forest:   { fill: [70,  140, 55,  0.7], height: 70,   sigma: 70 },
    water:    { fill: [65,  130, 190, 0.7], height: -70,  sigma: 50 },
    ocean:    { fill: [45,  100, 180, 0.8], height: -120, sigma: 100 },
    desert:   { fill: [200, 160, 70,  0.6], height: 45,   sigma: 80 },
    valley:   { fill: [110, 165, 85,  0.6], height: 100,  sigma: 50 },
    plain:    { fill: [185, 170, 120, 0.5], height: 60,   sigma: 100 },
    // icon-only 类型映射（无直接 terrain 对应的 icon）
    cave:     { fill: [130, 110, 85,  0.6], height: 200,  sigma: 35 },
    temple:   { fill: [160, 120, 180, 0.6], height: 80,   sigma: 35 },
    palace:   { fill: [170, 130, 60,  0.6], height: 90,   sigma: 40 },
    ruins:    { fill: [150, 130, 100, 0.5], height: 50,   sigma: 40 },
    island:   { fill: [100, 160, 140, 0.6], height: 60,   sigma: 50 },
    sacred:   { fill: [170, 140, 200, 0.6], height: 100,  sigma: 45 },
  },
  hillshade: {
    azimuth: 315,
    altitude: 45,
    zFactor: 2.0,
    blendMode: "multiply",
    opacity: 0.2,
  },
  label: {
    fontFamily: "serif",
    color: "#4a3728",
    haloColor: "#f0e4d0",
    tierSizes: [22, 16, 13, 11, 9, 8],
  },
}

/** tier 字符串 → 数字 (1-6) */
const TIER_NUM: Record<string, number> = {
  world: 1, continent: 1,
  kingdom: 2,
  region: 3,
  city: 3,
  site: 4,
  building: 5,
}

export function tierToNum(tier?: string): number {
  if (!tier) return 3
  return TIER_NUM[tier] ?? 3
}

/**
 * tier → MapLibre minzoom
 * 动态根据小说的最大 tier 深度调整映射
 */
export function tierToMinZoom(tierNum: number, maxTier: number = 6): number {
  if (tierNum <= 1) return 0
  // 均匀分布到 zoom 0-9 范围
  return Math.round(((tierNum - 1) / Math.max(maxTier - 1, 1)) * 8)
}

export function getDefaultStyle(): MapStyle {
  return PARCHMENT_STYLE
}

/**
 * 将后端 icon/tier/type 映射到 terrain key
 * 优先用 icon（英文，已分类），其次 tier，最后中文 type 子串匹配
 */
const _ICON_TO_TERRAIN: Record<string, string> = {
  capital: "city", city: "city", town: "town", village: "town",
  camp: "town", mountain: "mountain", forest: "forest",
  water: "water", desert: "desert", island: "island",
  temple: "temple", palace: "palace", cave: "cave",
  tower: "org", gate: "city", portal: "org",
  ruins: "ruins", sacred: "sacred",
}

const _TIER_TO_TERRAIN: Record<string, string> = {
  world: "realm", continent: "realm", kingdom: "kingdom",
  region: "valley", city: "city", site: "town", building: "town",
}

// 中文 type 子串 → terrain（兜底）
const _CN_TYPE_TERRAIN: [string, string][] = [
  ["海", "ocean"], ["洋", "ocean"], ["湖", "water"], ["河", "water"],
  ["江", "water"], ["溪", "water"], ["泉", "water"], ["沼", "water"], ["池", "water"],
  ["山", "mountain"], ["岭", "mountain"], ["峰", "mountain"], ["崖", "mountain"],
  ["丘", "hill"], ["坡", "hill"],
  ["林", "forest"], ["森", "forest"], ["竹", "forest"],
  ["沙", "desert"], ["漠", "desert"], ["荒", "desert"],
  ["谷", "valley"], ["峡", "valley"], ["涧", "valley"],
  ["原", "plain"], ["野", "plain"], ["地", "plain"],
  ["岛", "island"],
  ["洞", "cave"], ["窟", "cave"], ["穴", "cave"],
  ["殿", "palace"], ["宫", "palace"], ["府", "palace"],
  ["寺", "temple"], ["庙", "temple"], ["观", "temple"], ["祠", "temple"],
  ["塔", "org"], ["阁", "org"], ["楼", "org"],
  ["门", "org"], ["派", "org"], ["宗", "org"], ["教", "org"], ["盟", "org"],
  ["城", "city"], ["都", "city"], ["镇", "town"], ["村", "town"],
  ["国", "kingdom"], ["界", "realm"], ["域", "realm"], ["洲", "realm"],
  ["大陆", "realm"],
]

export function resolveTerrainType(
  icon?: string, tier?: string, chineseType?: string
): string {
  // 1. icon（最精确）
  if (icon && icon !== "generic") {
    const t = _ICON_TO_TERRAIN[icon]
    if (t) return t
  }
  // 2. 中文 type 子串（比 tier 更具体）
  if (chineseType) {
    for (const [kw, terrain] of _CN_TYPE_TERRAIN) {
      if (chineseType.includes(kw)) return terrain
    }
  }
  // 3. tier（层级兜底）
  if (tier) {
    const t = _TIER_TO_TERRAIN[tier]
    if (t) return t
  }
  return "plain"  // 最终兜底
}
