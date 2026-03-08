import type { ReactNode } from "react"
import { DataProviderProvider } from "@/providers/context"

export function Providers({ children }: { children: ReactNode }) {
  return (
    <DataProviderProvider>
      {children}
    </DataProviderProvider>
  )
}
