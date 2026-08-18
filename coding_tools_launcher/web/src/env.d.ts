/// <reference types="vite/client" />

import type { DesktopBridge } from './types'

declare global {
  interface Window {
    pywebview?: {
      api: DesktopBridge
    }
  }
}

export {}
