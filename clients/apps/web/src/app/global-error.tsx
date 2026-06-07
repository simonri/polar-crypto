'use client'

import InternalServerError from '@/components/Shared/InternalServerError'

export default function GlobalError({ error }: { error: Error }) {
  return (
    <html className="antialiased">
      <body>
        <InternalServerError
          digest={'digest' in error ? (error.digest as string) : undefined}
        />
      </body>
    </html>
  )
}
