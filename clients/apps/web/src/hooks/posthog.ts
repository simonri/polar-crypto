'use client'

import type { schemas } from '@polar-sh/client'

export type JsonType =
  | string
  | number
  | boolean
  | null
  | undefined
  | Record<string, unknown>
  | unknown[]

type Surface = 'website' | 'docs' | 'dashboard' | 'storefront' | 'global'
type Category =
  | 'benefits'
  | 'checkout'
  | 'subscriptions'
  | 'user'
  | 'organizations'
  | 'onboarding'
  | 'issues'
type Noun = string
type Verb =
  | 'click'
  | 'submit'
  | 'create'
  | 'view'
  | 'add'
  | 'invite'
  | 'update'
  | 'delete'
  | 'remove'
  | 'start'
  | 'end'
  | 'cancel'
  | 'fail'
  | 'generate'
  | 'send'
  | 'archive'
  | 'done'
  | 'open'
  | 'close'
  | 'complete'

export type EventName = `${Surface}:${Category}:${Noun}:${Verb}`

export interface PolarHog {
  setPersistence: (
    persistence: 'localStorage' | 'sessionStorage' | 'cookie' | 'memory',
  ) => void
  capture: (event: EventName, properties?: Record<string, JsonType>) => void
  identify: (user: schemas['UserRead']) => void
  logout: () => void
}

const noOp: PolarHog = {
  setPersistence: () => {},
  capture: () => {},
  identify: () => {},
  logout: () => {},
}

export const usePostHog = (): PolarHog => noOp
