'use client'

import { NavigationHistoryProvider } from '@/providers/navigationHistory'
import { getQueryClient } from '@/utils/api/query'
import { QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@/providers/theme'
import { usePathname, useSearchParams } from 'next/navigation'
import { NuqsAdapter } from 'nuqs/adapters/next/app'
import { PropsWithChildren, Suspense } from 'react'

export { NavigationHistoryProvider }

const FORCED_DARK_PREFIXES = [
  '/features',
  '/customers',
  '/blog',
  '/resources',
  '/company',
  '/startup-program',
  '/downloads',
  '/legal',
  '/midday/portal',
]

const isForcedDarkPath = (pathname: string): boolean =>
  pathname === '/' ||
  FORCED_DARK_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  )

function ThemeProviderInner({
  children,
  forceTheme,
}: {
  children: React.ReactNode
  forceTheme?: 'light' | 'dark'
}) {
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const theme = searchParams.get('theme')

  const forcedTheme = isForcedDarkPath(pathname) ? 'dark' : forceTheme

  return (
    <ThemeProvider
      defaultTheme="system"
      enableSystem
      attribute="class"
      forcedTheme={theme ?? forcedTheme}
    >
      {children}
    </ThemeProvider>
  )
}

export function PolarThemeProvider({
  children,
  forceTheme,
}: {
  children: React.ReactNode
  forceTheme?: 'light' | 'dark'
}) {
  return (
    <Suspense fallback={<>{children}</>}>
      <ThemeProviderInner forceTheme={forceTheme}>
        {children}
      </ThemeProviderInner>
    </Suspense>
  )
}

export function PolarQueryClientProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const queryClient = getQueryClient()

  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

export function PolarNuqsProvider({ children }: PropsWithChildren) {
  return <NuqsAdapter>{children}</NuqsAdapter>
}
