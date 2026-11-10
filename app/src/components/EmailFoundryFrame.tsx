import { useEffect, useState } from 'react'
import { Button, StatusText } from '@ryozan/ui'
import { getToken } from '../lib/api'
import { loadStudioSettingsResult } from '../hooks/useStudioSettings'
import { shopPackScopeState } from '../lib/studio-settings'
import './DependencyFrames.css'

export type EmailFoundrySurface = 'mobile' | 'desktop'

/**
 * Email Foundry is a package of the install's shop brand pack (SHOP_PACK_SCOPE), served same-origin at
 * /dependencies/@<scope>/email-foundry/<version>/ by the API (api/app/services/shop_pack.py). The pack is
 * shop content and never lives in this repository.
 */
const FOUNDRY_PACKAGE = 'email-foundry'
const FOUNDRY_VERSION = '1.0.0'

function foundryBase(scope: string): string {
  return `/dependencies/@${scope}/${FOUNDRY_PACKAGE}/${FOUNDRY_VERSION}/`
}

/** The pack does not carry Email Foundry, or this install has no pack. */
class FoundryMissing extends Error {
  name = 'FoundryMissing'
}

/** The studio settings read failed, so whether this install has a pack is unknown. */
class FoundrySettingsUnavailable extends Error {
  name = 'FoundrySettingsUnavailable'
}

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

function mountFoundryHost(base: string, source: string, adapterSource: string, authToken: string | null, surface: EmailFoundrySurface): string {
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
    .replace(/<head>/i, `<head><base href="${base}">`)
    .replace('<script src="./support.js"></script>', adapterBoot)
}

/**
 * Exact host boundary for the shop brand pack's locked email-foundry package.
 * The deck remains immutable; this wrapper only supplies its package-relative
 * base URL and official adapter before the design-component runtime boots.
 */
export default function EmailFoundryFrame({ surface = 'mobile' }: { surface?: EmailFoundrySurface }) {
  const [documentSource, setDocumentSource] = useState<string | null>(null)
  const [error, setError] = useState(false)
  const [missing, setMissing] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()

    loadStudioSettingsResult()
      .then(async (result) => {
        const pack = shopPackScopeState(result)
        if (pack.state === 'error') throw new FoundrySettingsUnavailable()
        if (pack.state === 'none') throw new FoundryMissing()
        const base = foundryBase(pack.scope)
        const [documentResponse, adapterResponse] = await Promise.all([
          fetch(`${base}${FOUNDRY_DOCUMENTS[surface]}`, { signal: controller.signal }),
          fetch(`${base}adapter.js`, { signal: controller.signal }),
        ])
        if (documentResponse.status === 404 || adapterResponse.status === 404) throw new FoundryMissing()
        if (!documentResponse.ok) throw new Error(`Email Foundry dependency returned ${documentResponse.status}`)
        if (!adapterResponse.ok) throw new Error(`Email Foundry adapter returned ${adapterResponse.status}`)
        return [base, await documentResponse.text(), await adapterResponse.text()] as const
      })
      .then(([base, source, adapterSource]) => setDocumentSource(mountFoundryHost(base, source, adapterSource, getToken(), surface)))
      .catch((caught) => {
        if (controller.signal.aborted || (caught as Error).name === 'AbortError') return
        if (caught instanceof FoundryMissing) setMissing(true)
        else setError(true)
      })

    return () => controller.abort()
  }, [surface, attempt])

  const retry = () => {
    setError(false)
    setMissing(false)
    setDocumentSource(null)
    setAttempt((value) => value + 1)
  }

  return (
    <section className="ryozan-email-foundry-frame" aria-label="Email Foundry">
      {missing ? (
        <StatusText role="status" tone="neutral" dot={false}>
          Email Foundry is not set up for this studio.
        </StatusText>
      ) : error ? (
        <>
          <StatusText role="alert" tone="danger">
            Email Foundry could not be loaded.
          </StatusText>
          <Button variant="outline" size="sm" onClick={retry}>Try again</Button>
        </>
      ) : documentSource ? (
        <iframe
          className="ryozan-email-foundry-frame__iframe"
          srcDoc={documentSource}
          title={`Email Foundry ${surface}`}
          sandbox="allow-forms allow-modals allow-popups allow-same-origin allow-scripts"
        />
      ) : (
        <StatusText role="status" tone="neutral" dot={false}>Loading Email Foundry</StatusText>
      )}
    </section>
  )
}
