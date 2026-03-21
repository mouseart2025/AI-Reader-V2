/** Spatial relation type → Chinese label */
export const SPATIAL_TYPE_LABELS: Record<string, string> = {
  direction: "方位",
  distance: "距离",
  contains: "包含",
  adjacent: "相邻",
  separated_by: "分隔",
  in_between: "居中",
  travel_path: "路径",
  cluster: "聚集",
  terrain: "地形",
  relative_scale: "相对规模",
  on_coast: "沿岸",
}

/** Spatial relation value → Chinese label */
export const SPATIAL_VALUE_LABELS: Record<string, string> = {
  south_of: "南方",
  north_of: "北方",
  east_of: "东方",
  west_of: "西方",
  northeast_of: "东北方",
  northwest_of: "西北方",
  southeast_of: "东南方",
  southwest_of: "西南方",
  nearby: "附近",
  on_coast: "沿岸",
  above: "上方",
  below: "下方",
}

export function translateSpatialType(type: string): string {
  return SPATIAL_TYPE_LABELS[type] ?? type
}

export function translateSpatialValue(value: string): string {
  return SPATIAL_VALUE_LABELS[value] ?? value
}
