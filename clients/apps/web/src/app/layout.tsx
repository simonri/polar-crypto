import '../styles/globals.css'

import { getExperimentNames } from '@/experiments'
import { ExperimentProvider } from '@/experiments/ExperimentProvider'
import { getExperiments } from '@/experiments/server'
import { UserContextProvider } from '@/providers/auth'
import { CONFIG } from '@/utils/config'
import { getAuthenticatedUser, getUserOrganizations } from '@/utils/user'
import { schemas } from '@polar-sh/client'
import { PHASE_PRODUCTION_BUILD } from 'next/constants'
import { Viewport } from 'next/types'
import {
  NavigationHistoryProvider,
  PolarNuqsProvider,
  PolarQueryClientProvider,
} from './providers'

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
}

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  let authenticatedUser: schemas['UserRead'] | undefined = undefined
  let userOrganizations: schemas['OrganizationWithRole'][] = []

  try {
    authenticatedUser = await getAuthenticatedUser()
    userOrganizations = await getUserOrganizations()
  } catch (e) {
    if (process.env.NEXT_PHASE !== PHASE_PRODUCTION_BUILD) {
      throw e
    }
  }

  const experimentVariants = await getExperiments(getExperimentNames())

  return (
    <html lang="en" suppressHydrationWarning className="antialiased">
      <head>
        {CONFIG.ENVIRONMENT === 'development' ? (
          <>
            <link
              href="/favicon-dev.png"
              rel="icon"
              media="(prefers-color-scheme: dark)"
            />
            <link
              href="/favicon-dev-dark.png"
              rel="icon"
              media="(prefers-color-scheme: light)"
            />
          </>
        ) : (
          <>
            <link
              href="/favicon.png"
              rel="icon"
              media="(prefers-color-scheme: dark)"
            />
            <link
              href="/favicon-dark.png"
              rel="icon"
              media="(prefers-color-scheme: light)"
            />
          </>
        )}
      </head>
      <body style={{ textRendering: 'optimizeLegibility' }}>
        {/* Runs synchronously before React hydrates to prevent flash of wrong theme.
            Must live in a server component — scripts in client components trigger a
            React 19 warning and are not re-executed on client navigations anyway. */}
        <script
          suppressHydrationWarning
          dangerouslySetInnerHTML={{
            __html: `try{var d=document.documentElement,s=localStorage.getItem('theme'),t=s==='dark'||s==='light'?s:window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';d.classList.remove('light','dark');d.classList.add(t);d.style.colorScheme=t}catch(e){}`,
          }}
        />
        <ExperimentProvider experiments={experimentVariants}>
          <UserContextProvider
            user={authenticatedUser}
            userOrganizations={userOrganizations}
          >
            <PolarQueryClientProvider>
              <PolarNuqsProvider>
                <NavigationHistoryProvider>
                  {children}
                </NavigationHistoryProvider>
              </PolarNuqsProvider>
            </PolarQueryClientProvider>
          </UserContextProvider>
        </ExperimentProvider>
      </body>
    </html>
  )
}
