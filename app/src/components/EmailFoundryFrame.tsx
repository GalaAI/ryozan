import { useEffect, useState } from 'react'
import { getToken } from '../lib/api'
import './DependencyFrames.css'

export type EmailFoundrySurface = 'mobile' | 'desktop'

const FOUNDRY_BASE = '/dependencies/@bttco/email-foundry/1.0.0/'
const FOUNDRY_DOCUMENTS: Record<EmailFoundrySurface, string> = {
  mobile: 'Email%20Foundry%20Mobile.dc.html',
  desktop: 'Email%20Foundry.dc.html',
}

const MOBILE_ROOT_STYLE = "min-height: 100vh; max-width: 430px; margin: 0 auto; background: #0b0c0b; color: #e3dbcb; font-family: 'Space Grotesk', sans-serif; padding: 22px 18px 90px; box-sizing: border-box;"
const MOBILE_THREAD_STYLE = "min-height: 100%; width: 100%; margin: 0; background: #0b0c0b; color: #e3dbcb; font-family: 'Space Grotesk', sans-serif; padding: 10px 0 24px; box-sizing: border-box;"
const MOBILE_BRAND_MARK = '<img src="/assets/brand/ryozan-mark.svg" alt="Ryozan" style="width: 40px; height: 40px; object-fit: contain; flex: none;">'

function applySurfaceLayout(source: string, surface: EmailFoundrySurface): string {
  if (surface !== 'mobile') return source
  return source
    .replace(MOBILE_ROOT_STYLE, MOBILE_THREAD_STYLE)
    .replace(MOBILE_BRAND_MARK, '')
}

function mountFoundryHost(source: string, adapterSource: string, authToken: string | null, surface: EmailFoundrySurface): string {
  const classicAdapterSource = adapterSource
    .replace('export function installFoundryAdapter', 'function installFoundryAdapter')
    .replaceAll('</script>', '<\\/script>')
  const adapterBoot = `<script>
${classicAdapterSource}
window.addEventListener('bttco-foundry-hydrated', () => {
  document.documentElement.dataset.foundryAdapter = 'hydrated';
}, { once: true });
const adapter = installFoundryAdapter({
  baseUrl: '/api/v1/email-templates/foundry',
  authToken: ${JSON.stringify(authToken)},
});
adapter.refresh().finally(() => {
  const runtime = document.createElement('script');
  runtime.src = './support.js';
  document.head.appendChild(runtime);
});
</script>`

  return applySurfaceLayout(source, surface)
    .replace(/<head>/i, `<head><base href="${FOUNDRY_BASE}">`)
    .replace('<script src="./support.js"></script>', adapterBoot)
}

/**
 * Exact host boundary for the locked @bttco/email-foundry dependency.
 * The deck remains immutable; this wrapper only supplies its package-relative
 * base URL and official adapter before the design-component runtime boots.
 */
export default function EmailFoundryFrame({ surface = 'mobile' }: { surface?: EmailFoundrySurface }) {
  const [documentSource, setDocumentSource] = useState<string | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    const controller = new AbortController()

    Promise.all([
      fetch(`${FOUNDRY_BASE}${FOUNDRY_DOCUMENTS[surface]}`, { signal: controller.signal }),
      fetch(`${FOUNDRY_BASE}adapter.js`, { signal: controller.signal }),
    ])
      .then(async ([documentResponse, adapterResponse]) => {
        if (!documentResponse.ok) throw new Error(`Email Foundry dependency returned ${documentResponse.status}`)
        if (!adapterResponse.ok) throw new Error(`Email Foundry adapter returned ${adapterResponse.status}`)
        return [await documentResponse.text(), await adapterResponse.text()] as const
      })
      .then(([source, adapterSource]) => setDocumentSource(mountFoundryHost(source, adapterSource, getToken(), surface)))
      .catch((caught) => {
        if ((caught as Error).name !== 'AbortError') setError(true)
      })

    return () => controller.abort()
  }, [surface])

  return (
    <section className="ryozan-email-foundry-frame" aria-label="Email Foundry">
      {error ? (
        <div className="ryozan-email-foundry-frame__status" role="alert">
          Email Foundry could not be loaded. Close and reopen this panel.
        </div>
      ) : documentSource ? (
        <iframe
          className="ryozan-email-foundry-frame__iframe"
          srcDoc={documentSource}
          title={`Email Foundry ${surface}`}
          sandbox="allow-forms allow-modals allow-popups allow-same-origin allow-scripts"
        />
      ) : (
        <div className="ryozan-email-foundry-frame__status" role="status">Loading Email Foundry</div>
      )}
    </section>
  )
}
