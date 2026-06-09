'use client'

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from 'react'

type ResolvedTheme = 'light' | 'dark'

interface ThemeContextValue {
  theme: string
  resolvedTheme: ResolvedTheme
  setTheme: (t: string) => void
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: 'system',
  resolvedTheme: 'light',
  setTheme: () => {},
})

function readDocTheme(): ResolvedTheme {
  if (typeof document === 'undefined') return 'light'
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light'
}

function applyTheme(t: string) {
  const isDark =
    t === 'dark' ||
    (t === 'system' &&
      window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', isDark)
  document.documentElement.classList.toggle('light', !isDark)
  document.documentElement.style.colorScheme = isDark ? 'dark' : 'light'
}

export function ThemeProvider({
  children,
  forcedTheme,
  defaultTheme = 'system',
}: {
  children: React.ReactNode
  forcedTheme?: string
  defaultTheme?: string
  attribute?: string
  enableSystem?: boolean
}) {
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>('light')
  const [storedTheme, setStoredTheme] = useState(defaultTheme)

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setResolvedTheme(readDocTheme())
    const observer = new MutationObserver(() =>
      setResolvedTheme(readDocTheme()),
    )
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class'],
    })
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (forcedTheme) {
      applyTheme(forcedTheme)
    }
  }, [forcedTheme])

  const setTheme = useCallback((t: string) => {
    setStoredTheme(t)
    try {
      localStorage.setItem('theme', t)
    } catch {
      // localStorage may not be available in all environments
    }
    applyTheme(t)
  }, [])

  const active = forcedTheme ?? storedTheme
  const resolved = forcedTheme
    ? forcedTheme === 'dark'
      ? 'dark'
      : 'light'
    : resolvedTheme

  return (
    <ThemeContext.Provider
      value={{ theme: active, resolvedTheme: resolved, setTheme }}
    >
      {children}
    </ThemeContext.Provider>
  )
}

export const useTheme = () => useContext(ThemeContext)
