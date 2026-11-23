/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** OAuth client id for "Continuar con Google"; unset = the button is not shown. */
  readonly VITE_GOOGLE_CLIENT_ID?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
