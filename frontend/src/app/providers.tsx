import type { ReactNode } from "react"
import { I18nProvider } from "@/i18n"
import { DataProviderProvider } from "@/providers/context"

export function Providers({ children }: { children: ReactNode }) {
  return (
    <I18nProvider>
      <DataProviderProvider>
        {children}
      </DataProviderProvider>
    </I18nProvider>
  )
}
