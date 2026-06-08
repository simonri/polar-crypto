import { MetadataRoute } from 'next'

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: '*',
      allow: '/',
      disallow: ['/dashboard/', '/auth/', '/verify-email/'],
    },
    sitemap: 'https://polar.sh/sitemap.xml',
  }
}
