'use client'

import { usePostHog } from '@/hooks/posthog'
import KeyboardArrowRight from '@mui/icons-material/KeyboardArrowRight'
import { Button } from '@polar-sh/orbit'
import { ComponentProps, FormEvent, useCallback, useState } from 'react'
import { twMerge } from 'tailwind-merge'
import { Modal } from '../Modal'
import { useModal } from '../Modal/useModal'
import { AuthModal } from './AuthModal'

interface GetStartedButtonProps extends ComponentProps<typeof Button> {
  text?: string
}

const GetStartedButton = ({
  text: _text,
  wrapperClassNames,
  size = 'lg',
  ...props
}: GetStartedButtonProps) => {
  const posthog = usePostHog()
  const { isShown: isModalShown, hide: hideModal, show: showModal } = useModal()
  const [view, setView] = useState<'signup' | 'login'>('signup')
  const text = _text || 'Get Started'

  const onClick = useCallback(() => {
    posthog.capture('global:user:signup:click')
    setView('signup')
    showModal()
  }, [posthog, showModal])

  const onSubmit = useCallback(
    (e: FormEvent) => {
      e.preventDefault()
      e.stopPropagation()
      onClick()
    },
    [onClick],
  )

  const modalTitles = {
    signup: 'Get started',
    login: 'Sign in',
  } as const
  const modalTitle = modalTitles[view]

  const modalContents = {
    signup: <AuthModal returnTo="/onboarding/personal" signup />,
    login: <AuthModal returnTo="/dashboard" />,
  }
  const modalContent = modalContents[view]

  return (
    <>
      <Button
        wrapperClassNames={twMerge(
          'flex flex-row items-center gap-x-2 ',
          wrapperClassNames,
        )}
        size={size}
        onClick={onClick}
        onSubmit={onSubmit}
        {...props}
      >
        <div>{text}</div>
        <KeyboardArrowRight
          className={size === 'lg' ? 'text-lg' : 'text-md'}
          fontSize="inherit"
        />
      </Button>

      <Modal
        title={modalTitle}
        isShown={isModalShown}
        hide={hideModal}
        modalContent={modalContent}
        className="lg:w-full lg:max-w-[480px]"
      />
    </>
  )
}

export default GetStartedButton
