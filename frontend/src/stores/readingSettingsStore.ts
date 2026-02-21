import { create } from "zustand"
import { persist } from "zustand/middleware"

export type FontSize = "small" | "medium" | "large" | "xlarge"
export type LineHeight = "compact" | "normal" | "loose"

interface ReadingSettingsState {
  fontSize: FontSize
  lineHeight: LineHeight
  setFontSize: (size: FontSize) => void
  setLineHeight: (height: LineHeight) => void
}

export const FONT_SIZE_MAP: Record<FontSize, string> = {
  small: "text-sm",
  medium: "text-base",
  large: "text-lg",
  xlarge: "text-xl",
}

export const LINE_HEIGHT_MAP: Record<LineHeight, string> = {
  compact: "leading-[1.6]",
  normal: "leading-[2.0]",
  loose: "leading-[2.6]",
}

export const useReadingSettingsStore = create<ReadingSettingsState>()(
  persist(
    (set) => ({
      fontSize: "medium",
      lineHeight: "normal",
      setFontSize: (fontSize) => set({ fontSize }),
      setLineHeight: (lineHeight) => set({ lineHeight }),
    }),
    { name: "ai-reader-reading-settings" },
  ),
)
