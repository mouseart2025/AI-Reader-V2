import { translate, type TranslationKey } from "@/i18n"

/** Spatial relation type -> locale key. */
export const SPATIAL_TYPE_LABELS: Record<string, TranslationKey> = {
  direction: "visualization.spatial.type.direction",
  distance: "visualization.spatial.type.distance",
  contains: "visualization.spatial.type.contains",
  adjacent: "visualization.spatial.type.adjacent",
  separated_by: "visualization.spatial.type.separatedBy",
  in_between: "visualization.spatial.type.inBetween",
  travel_path: "visualization.spatial.type.travelPath",
  cluster: "visualization.spatial.type.cluster",
  terrain: "visualization.spatial.type.terrain",
  relative_scale: "visualization.spatial.type.relativeScale",
  on_coast: "visualization.spatial.type.onCoast",
}

/** Spatial relation value -> locale key. */
export const SPATIAL_VALUE_LABELS: Record<string, TranslationKey> = {
  south_of: "visualization.spatial.value.southOf",
  north_of: "visualization.spatial.value.northOf",
  east_of: "visualization.spatial.value.eastOf",
  west_of: "visualization.spatial.value.westOf",
  northeast_of: "visualization.spatial.value.northeastOf",
  northwest_of: "visualization.spatial.value.northwestOf",
  southeast_of: "visualization.spatial.value.southeastOf",
  southwest_of: "visualization.spatial.value.southwestOf",
  nearby: "visualization.spatial.value.nearby",
  on_coast: "visualization.spatial.value.onCoast",
  above: "visualization.spatial.value.above",
  below: "visualization.spatial.value.below",
}

export function translateSpatialType(type: string): string {
  const key = SPATIAL_TYPE_LABELS[type]
  return key ? translate(key) : type
}

export function translateSpatialValue(value: string): string {
  const key = SPATIAL_VALUE_LABELS[value]
  return key ? translate(key) : value
}
