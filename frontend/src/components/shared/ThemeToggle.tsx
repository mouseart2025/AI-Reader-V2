import { Moon, Sun, Monitor } from "lucide-react"
import { useThemeStore } from "@/stores/themeStore"
import { Button } from "@/components/ui/button"

const CYCLE = ["light", "dark", "system"] as const

export function ThemeToggle() {
  const { theme, setTheme } = useThemeStore()

  const next = () => {
    const idx = CYCLE.indexOf(theme)
    setTheme(CYCLE[(idx + 1) % CYCLE.length])
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      className="h-8 w-8"
      onClick={next}
      title={
        theme === "light" ? "浅色模式" : theme === "dark" ? "深色模式" : "跟随系统"
      }
    >
      {theme === "light" && <Sun className="h-4 w-4" />}
      {theme === "dark" && <Moon className="h-4 w-4" />}
      {theme === "system" && <Monitor className="h-4 w-4" />}
    </Button>
  )
}
