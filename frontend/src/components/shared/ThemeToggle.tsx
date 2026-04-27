import { Moon, Sun, Monitor } from "lucide-react"
import { useThemeStore } from "@/stores/themeStore"
import { Button } from "@/components/ui/button"
import { useI18n } from "@/i18n"

const CYCLE = ["light", "dark", "system"] as const

export function ThemeToggle() {
  const { t } = useI18n()
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
        theme === "light"
          ? t("shared.themeToggle.lightMode")
          : theme === "dark"
            ? t("shared.themeToggle.darkMode")
            : t("shared.themeToggle.systemMode")
      }
    >
      {theme === "light" && <Sun className="h-4 w-4" />}
      {theme === "dark" && <Moon className="h-4 w-4" />}
      {theme === "system" && <Monitor className="h-4 w-4" />}
    </Button>
  )
}
