import { schemas } from '@polar-sh/client'
import { nanoid } from 'nanoid'
import { RequestCookiesAdapter } from 'next/dist/server/web/spec-extension/adapters/request-cookies'
import type { NextRequest } from 'next/server'
import { NextResponse } from 'next/server'
import { COOKIE_MAX_AGE, DISTINCT_ID_COOKIE } from './experiments/constants'
import { createServerSideAPI } from './utils/client'
import { CONFIG } from './utils/config'

const POLAR_AUTH_COOKIE_KEY =
  process.env.POLAR_AUTH_COOKIE_KEY || 'polar_session'

const AUTHENTICATED_ROUTES = [
  new RegExp('^/start(/.*)?$'),
  new RegExp('^/onboarding(/.*)?$'),
  new RegExp('^/dashboard(/.*)?$'),
  new RegExp('^/finance(/.*)?$'),
  new RegExp('^/settings(/.*)?$'),
  new RegExp('^/oauth2(/.*)?$'),
  new RegExp('^/to(/.*)?$'),
]

const getOrCreateDistinctId = (
  request: NextRequest,
): { id: string; isNew: boolean } => {
  const existing = request.cookies.get(DISTINCT_ID_COOKIE)?.value
  if (existing) {
    return { id: existing, isNew: false }
  }
  return { id: `anon_${nanoid()}`, isNew: true }
}

const isForwardedRoute = (request: NextRequest): boolean => {
  if (request.nextUrl.pathname.startsWith('/docs/')) {
    return true
  }

  if (request.nextUrl.pathname.startsWith('/mintlify-assets/')) {
    return true
  }

  if (request.nextUrl.pathname.startsWith('/_mintlify/')) {
    return true
  }

  return false
}

const requiresAuthentication = (request: NextRequest): boolean => {
  if (isForwardedRoute(request)) {
    return false
  }

  return AUTHENTICATED_ROUTES.some((route) =>
    route.test(request.nextUrl.pathname),
  )
}

const getLoginResponse = (request: NextRequest): NextResponse => {
  const redirectURL = request.nextUrl.clone()
  redirectURL.pathname = '/auth'
  redirectURL.search = ''
  const returnTo = `${request.nextUrl.pathname}${request.nextUrl.search}`
  redirectURL.searchParams.set('return_to', returnTo)
  return NextResponse.redirect(redirectURL)
}

export async function proxy(request: NextRequest) {
  // Do not run middleware for forwarded routes
  // @pieterbeulque added this because the `config.matcher` behavior below
  // doesn't appear to be working consistently with Vercel rewrites
  if (isForwardedRoute(request)) {
    return NextResponse.next()
  }

  // Redirect old customer query string URLs to path-based URLs
  const customersMatch = request.nextUrl.pathname.match(
    /^\/dashboard\/([^/]+)\/customers$/,
  )
  if (customersMatch && request.nextUrl.searchParams.has('customerId')) {
    const customerId = request.nextUrl.searchParams.get('customerId')
    const redirectURL = request.nextUrl.clone()
    redirectURL.pathname = `/dashboard/${customersMatch[1]}/customers/${customerId}`
    redirectURL.searchParams.delete('customerId')
    return NextResponse.redirect(redirectURL)
  }

  // Redirect old checkout link query string URLs to path-based URLs
  const checkoutLinksMatch = request.nextUrl.pathname.match(
    /^\/dashboard\/([^/]+)\/products\/checkout-links$/,
  )
  if (
    checkoutLinksMatch &&
    request.nextUrl.searchParams.has('checkoutLinkId')
  ) {
    const checkoutLinkId = request.nextUrl.searchParams.get('checkoutLinkId')
    const redirectURL = request.nextUrl.clone()
    redirectURL.pathname = `/dashboard/${checkoutLinksMatch[1]}/products/checkout-links/${checkoutLinkId}`
    redirectURL.searchParams.delete('checkoutLinkId')
    return NextResponse.redirect(redirectURL)
  }

  // Redirect deprecated path-based URLs to new structure
  // Events: /dashboard/{org}/usage-billing/events/* -> /dashboard/{org}/analytics/events/*
  const eventsPathMatch = request.nextUrl.pathname.match(
    /^\/dashboard\/([^/]+)\/usage-billing\/events(\/.*)?$/,
  )
  if (eventsPathMatch) {
    const redirectURL = request.nextUrl.clone()
    redirectURL.pathname = `/dashboard/${eventsPathMatch[1]}/analytics/events${eventsPathMatch[2] || ''}`
    return NextResponse.redirect(redirectURL, { status: 308 })
  }

  let user: schemas['UserRead'] | undefined = undefined

  if (request.cookies.has(POLAR_AUTH_COOKIE_KEY)) {
    const api = await createServerSideAPI(
      request.headers,
      RequestCookiesAdapter.seal(request.cookies),
    )
    const { data, response } = await api.GET('/v1/users/me', {
      cache: 'no-cache',
    })

    // A 429 means resolving the session was rate-limited upstream (the session
    // cookie is being counted in the API's `pending_auth` bucket). We can't
    // determine the user, so we proceed as anonymous rather than turning a
    // transient rate-limit into a hard 500 for the user. It's still logged so
    // we keep visibility on how often this happens.
    if (response.status === 429) {
      console.error(
        `Rate limited while fetching authenticated user: status=429, headers=${JSON.stringify(Object.fromEntries(response.headers.entries()))}`,
      )
    } else if (!response.ok && response.status !== 401) {
      console.error(
        `Error response: status=${response.status}, headers=${JSON.stringify(Object.fromEntries(response.headers.entries()))}`,
      )
      throw new Error(
        'Unexpected response status while fetching authenticated user',
      )
    }

    user = data
  }

  if (requiresAuthentication(request) && !user) {
    return getLoginResponse(request)
  }

  const { id: distinctId, isNew: isNewDistinctId } =
    getOrCreateDistinctId(request)

  const headers: Record<string, string> = {
    'x-polar-distinct-id': distinctId,
  }
  if (user) {
    headers['x-polar-user'] = Buffer.from(JSON.stringify(user)).toString(
      'base64',
    )
  }

  const response = NextResponse.next({ headers })

  if (isNewDistinctId) {
    response.cookies.set(DISTINCT_ID_COOKIE, distinctId, {
      maxAge: COOKIE_MAX_AGE,
      httpOnly: false,
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'lax',
    })
  }

  return response
}

export const config = {
  matcher: [
    /*
     * Match all request paths except for the ones starting with:
     * - api (API routes)
     * - fonts (static font files)
     * - monitoring (Sentry)
     * - docs, _mintlify, mintlify-assets (Mintlify)
     * - assets (static asset files)
     * - _next (Next.js internals: static files, image optimization, data)
     * - favicon.ico, sitemap.xml, robots.txt (metadata files)
     */
    '/((?!api|fonts|docs|_mintlify|mintlify-assets|assets|_next|favicon.ico|sitemap.xml|robots.txt).*)',
  ],
}
