/* global process */
import createMDX from '@next/mdx'
import { themeConfig } from './shiki.config.mjs'

// Vercel preview: compute basePath and API URL from PR number + Tailscale hostname
let previewBasePath = ''
if (
  process.env.VERCEL_GIT_PULL_REQUEST_ID &&
  process.env.POLAR_PREVIEW_BACKEND_HOST
) {
  const prNum = parseInt(process.env.VERCEL_GIT_PULL_REQUEST_ID)
  previewBasePath = `/pr-${prNum}`
  const baseUrl = `https://${process.env.POLAR_PREVIEW_BACKEND_HOST}${previewBasePath}`
  process.env.NEXT_PUBLIC_API_URL = baseUrl
  process.env.NEXT_PUBLIC_FRONTEND_BASE_URL = baseUrl
}

const POLAR_AUTH_COOKIE_KEY =
  process.env.POLAR_AUTH_COOKIE_KEY || 'polar_session'

const defaultFrontendHostname = process.env.NEXT_PUBLIC_FRONTEND_BASE_URL
  ? new URL(process.env.NEXT_PUBLIC_FRONTEND_BASE_URL).hostname
  : 'polar.sh'

// CSP disabled — no restrictions in this build

/** @type {import('next').NextConfig} */
const nextConfig = {
  allowedDevOrigins: ['127.0.0.1'],
  reactStrictMode: true,
  transpilePackages: ['shiki', '@polar-sh/checkout', '@polar-sh/orbit'],
  pageExtensions: ['js', 'jsx', 'md', 'mdx', 'ts', 'tsx'],

  ...(previewBasePath && {
    basePath: previewBasePath,
    env: {
      POLAR_API_URL: `https://${process.env.POLAR_PREVIEW_BACKEND_HOST}:8443${previewBasePath}`,
    },
  }),

  typescript: { ignoreBuildErrors: true },
  eslint: { ignoreDuringBuilds: true },

  outputFileTracingIncludes: {
    '/onboarding/validate-description': [
      './src/app/(main)/onboarding/validate-description/acceptable-use-policy.mdx',
    ],
  },

  webpack: (config, { dev }) => {
    if (config.cache && !dev) {
      config.cache = Object.freeze({
        type: 'memory',
      })
    }

    return config
  },

  experimental: {
    webpackMemoryOptimizations: true,
  },

  images: {
    remotePatterns: [
      ...(process.env.S3_PUBLIC_IMAGES_BUCKET_HOSTNAME
        ? [
            {
              protocol: process.env.S3_PUBLIC_IMAGES_BUCKET_PROTOCOL || 'https',
              hostname: process.env.S3_PUBLIC_IMAGES_BUCKET_HOSTNAME,
              port: process.env.S3_PUBLIC_IMAGES_BUCKET_PORT || '',
              pathname: process.env.S3_PUBLIC_IMAGES_BUCKET_PATHNAME || '**',
            },
          ]
        : []),
      {
        protocol: 'https',
        hostname: 'avatars.githubusercontent.com',
        port: '',
        pathname: '**',
      },
      {
        protocol: 'https',
        hostname: '7vk6rcnylug0u6hg.public.blob.vercel-storage.com',
        port: '',
        pathname: '**',
      },
    ],
  },

  async rewrites() {
    const apiUrl = process.env.POLAR_API_URL || process.env.NEXT_PUBLIC_API_URL
    return [
      ...(apiUrl
        ? [
            {
              source: '/v1/:path*',
              destination: `${apiUrl}/v1/:path*`,
            },
            {
              source: '/backoffice/:path*',
              destination: `${apiUrl}/backoffice/:path*`,
            },
            {
              source: '/healthz',
              destination: `${apiUrl}/healthz`,
            },
            {
              source: '/openapi.json',
              destination: `${apiUrl}/openapi.json`,
            },
          ]
        : []),
    ]
  },

  async redirects() {
    return [
      // dashboard.polar.sh redirections
      {
        source: '/',
        destination: '/auth',
        has: [
          {
            type: 'host',
            value: 'dashboard.polar.sh',
          },
        ],
        permanent: false,
      },
      {
        source: '/:path*',
        destination: 'https://polar.sh/:path*',
        has: [
          {
            type: 'host',
            value: 'dashboard.polar.sh',
          },
        ],
        permanent: false,
      },
      {
        source: '/careers',
        destination: 'https://polar.sh/company',
        permanent: false,
      },
      {
        source: '/legal/terms',
        destination: 'https://polar.sh/legal/master-services-terms',
        permanent: false,
      },
      {
        source: '/legal/privacy',
        destination: 'https://polar.sh/legal/privacy-policy',
        permanent: false,
      },
      {
        source: '/llms.txt',
        destination: 'https://polar.sh/docs/llms.txt',
        permanent: true,
        has: [
          {
            type: 'host',
            value: 'polar.sh',
          },
        ],
      },
      {
        source: '/llms-full.txt',
        destination: 'https://polar.sh/docs/llms-full.txt',
        permanent: true,
        has: [
          {
            type: 'host',
            value: 'polar.sh',
          },
        ],
      },

      // Logged-in user redirections
      {
        source: '/',
        destination: '/start',
        has: [
          {
            type: 'cookie',
            key: POLAR_AUTH_COOKIE_KEY,
          },
          {
            type: 'host',
            value: defaultFrontendHostname,
          },
        ],
        permanent: false,
      },

      // Redirect /dashboard to correct domain if on a different domain name
      // Skip in preview builds — preview env uses a single domain via Caddy proxy
      ...(!previewBasePath
        ? [
            {
              source: '/dashboard/:path*',
              destination: `https://${defaultFrontendHostname}/dashboard/:path*`,
              missing: [
                {
                  type: 'host',
                  value: defaultFrontendHostname,
                },
                {
                  type: 'header',
                  key: 'x-forwarded-host',
                  value: defaultFrontendHostname,
                },
              ],
              permanent: false,
            },
          ]
        : []),

      {
        source: '/maintainer',
        destination: '/dashboard',
        permanent: true,
      },
      {
        source: '/maintainer/:path(.*)',
        destination: '/dashboard/:path(.*)',
        permanent: true,
      },
      {
        source: '/finance',
        destination: '/finance/income',
        permanent: false,
      },
      {
        source: '/dashboard/:organization/overview',
        destination: '/dashboard/:organization',
        permanent: true,
      },
      {
        source: '/dashboard/:organization/benefits',
        destination: '/dashboard/:organization/products/benefits',
        permanent: true,
      },
      {
        source: '/dashboard/:organization/products/overview',
        destination: '/dashboard/:organization/products',
        permanent: true,
      },
      {
        source: '/dashboard/:organization/issues',
        destination: '/dashboard/:organization/issues/overview',
        permanent: false,
      },
      {
        source: '/dashboard/:organization/promote/issues',
        destination: '/dashboard/:organization/issues/badge',
        permanent: false,
      },
      {
        source: '/dashboard/:organization/issues/promote',
        destination: '/dashboard/:organization/issues/badge',
        permanent: false,
      },
      {
        source: '/dashboard/:organization/finance',
        destination: '/dashboard/:organization/finance/income',
        permanent: false,
      },
      {
        source: '/dashboard/:organization/usage-billing/events',
        destination: '/dashboard/:organization/analytics/events',
        permanent: true,
      },
      {
        source: '/dashboard/:organization/usage-billing/spans',
        destination: '/dashboard/:organization/analytics/costs',
        permanent: true,
      },

      // Account Settings Redirects
      {
        source: '/settings',
        destination: '/dashboard/account/preferences',
        permanent: true,
      },

      // Access tokens redirect
      {
        source: '/settings/tokens',
        destination: '/account/developer',
        permanent: false,
      },

      // Old blog redirects
      {
        source: '/polarsource/posts',
        destination: '/blog',
        permanent: false,
      },
      {
        source: '/polarsource/posts/:path(.*)',
        destination: '/blog/:path*',
        permanent: false,
      },

      // Fallback blog redirect
      {
        source: '/:path*',
        destination: 'https://polar.sh/polarsource',
        has: [
          {
            type: 'host',
            value: 'blog.polar.sh',
          },
        ],
        permanent: false,
      },

      // CLI Install Script
      {
        source: '/install.sh',
        destination:
          'https://raw.githubusercontent.com/polarsource/cli/main/install.sh',
        permanent: false,
      },

      {
        source: '/signup',
        destination: '/auth',
        permanent: false,
      },
    ]
  },
  async headers() {
    return []
  },
}

const createConfig = async () => {
  const withMDX = createMDX({
    options: {
      remarkPlugins: ['remark-frontmatter', 'remark-gfm'],
      rehypePlugins: [
        'rehype-slug',
        [
          '@shikijs/rehype',
          {
            themes: themeConfig,
          },
        ],
      ],
    },
  })

  return withMDX(nextConfig)
}

export default createConfig
