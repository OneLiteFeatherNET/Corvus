/**
 * Path the app is served under. nginx injects `<base href="/prefix/">` from CORVUS_UI_BASE_PATH; without that tag
 * (dev server, plain static hosting) the app lives at the root.
 */
export function readBasePath(doc: Pick<Document, 'querySelector'> | null = typeof document === 'undefined' ? null : document): string {
  const href = doc?.querySelector('base')?.getAttribute('href')
  if (!href) return '/'
  let path: string
  try {
    path = new URL(href, 'http://localhost').pathname
  }
  catch {
    return '/'
  }
  return path.endsWith('/') ? path : `${path}/`
}
