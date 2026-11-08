import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Badge, Chip, ChipGroup, MetaList, PageTitle, Panel, SectionLabel, StatusText, token } from '@ryozan/ui'
import type { BadgeTone } from '@ryozan/ui'
import { mods, type MatrixModView, type ModEntry, type ModsInventory, type ModTier } from '../../lib/api'

/*
 * Mods: the shop's Mods inventory and the Ark Matrix Mod.
 *
 * The inventory is a stamp of Ark's inventory Mold: the API sends the instance
 * config (title, eyebrow, tabs with code prefixes) and its feed; this page only
 * renders it. The Matrix view reads Ark's Matrix Mod through the API and never
 * starts a run itself.
 */

const tierTone: Record<ModTier, BadgeTone> = {
  unknown: 'neutral',
  planned: 'neutral',
  scaffolded: 'pending',
  implemented: 'info',
  validated: 'info',
  integrated: 'ok',
  'runtime-proven': 'ok',
}

const stack = { display: 'grid', gap: token.space.s4, minWidth: 0 } as const
const row = { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: token.space.s2 } as const
const muted = { color: token.color.textMuted, margin: 0 } as const

function connection(entry: ModEntry) {
  if (entry.connected === null) return <StatusText tone="neutral" dot={false}>No setting to check</StatusText>
  return entry.connected
    ? <StatusText tone="ok">Connected</StatusText>
    : <StatusText tone="pending">Not connected</StatusText>
}

function InventoryView({ inventory }: { inventory: ModsInventory }) {
  const [tab, setTab] = useState(inventory.mold.tabs[0]?.id ?? '')
  const entries = inventory.feed.entries.filter((entry) => entry.tab === tab)
  return (
    <div style={stack}>
      <ChipGroup label={inventory.mold.eyebrow}>
        {inventory.mold.tabs.map((item) => (
          <Chip key={item.id} selected={item.id === tab} onClick={() => setTab(item.id)}>
            {item.label} {inventory.feed.entries.filter((entry) => entry.tab === item.id).length}
          </Chip>
        ))}
      </ChipGroup>
      {entries.length === 0 && <p style={muted}>No Mods in this tab yet.</p>}
      {entries.map((entry) => (
        <Panel key={entry.code} bordered>
          <div style={{ ...stack, gap: token.space.s2, padding: token.space.s4 }}>
            <div style={row}>
              <SectionLabel tone="gold">{entry.code}</SectionLabel>
              <strong>{entry.name}</strong>
              <Badge tone={tierTone[entry.tier]}>{entry.tier}</Badge>
              {connection(entry)}
            </div>
            <p style={muted}>{entry.alt}</p>
            <MetaList rows={[{ label: 'Source', value: entry.source }]} />
            {entry.gaps.map(([text, where]) => (
              <p key={text} style={muted}>Gap: {text} ({where})</p>
            ))}
          </div>
        </Panel>
      ))}
      {inventory.feed.warnings.map((warning) => <p key={warning} style={muted}>{warning}</p>)}
    </div>
  )
}

const serverLabel = { own: 'Own server', shared: 'Shared server', none: 'No server' } as const

function MatrixView({ view }: { view: MatrixModView }) {
  if (!view.connected)
    return (
      <Panel bordered>
        <div style={{ ...stack, padding: token.space.s4 }}>
          <StatusText tone="pending">Not connected to Ark</StatusText>
          <p style={muted}>{view.reason}</p>
        </div>
      </Panel>
    )
  return (
    <div style={stack}>
      <SectionLabel tone="gold">Eden Matrices</SectionLabel>
      {view.matrices.map((matrix) => (
        <Panel key={matrix.slug} bordered>
          <div style={{ ...stack, gap: token.space.s2, padding: token.space.s4 }}>
            <div style={row}>
              <strong>{matrix.label}</strong>
              <Badge tone={matrix.engine.server === 'none' ? 'neutral' : 'info'}>{serverLabel[matrix.engine.server]}</Badge>
              <Badge>{matrix.scopes.read}</Badge>
              {matrix.scopes.write ? <Badge tone="gold">{matrix.scopes.write}</Badge> : <Badge>read only</Badge>}
            </div>
            <MetaList
              rows={[
                { label: 'Matrix', value: matrix.matrixRef },
                { label: 'Engine', value: matrix.engine.name },
                { label: 'Starts', value: matrix.engine.starts },
                { label: 'Server', value: matrix.engine.serverDetail },
              ]}
            />
            <div style={row}>
              {matrix.tools.map((tool) => (
                <Badge key={tool.capabilityId} tone={tool.risk === 'write' ? 'gold' : 'neutral'} title={tool.description}>
                  {tool.name}
                </Badge>
              ))}
            </div>
            {matrix.writeRefusal && <p style={muted}>{matrix.writeRefusal}</p>}
          </div>
        </Panel>
      ))}
      <SectionLabel tone="gold">Recent scenario runs</SectionLabel>
      {view.runs === null && <p style={muted}>{view.runsNote}</p>}
      {view.runs?.length === 0 && <p style={muted}>No runs yet.</p>}
      {view.runs?.map((run) => (
        <Panel key={run.runId} bordered>
          <div style={{ ...row, padding: token.space.s3 }}>
            <Badge tone={run.state === 'passed' ? 'ok' : run.state === 'failed' || run.state === 'error' ? 'danger' : 'pending'}>
              {run.state}
            </Badge>
            <strong>{run.scenario?.title ?? run.runId}</strong>
            {run.change && <span style={muted}>{run.change}</span>}
          </div>
        </Panel>
      ))}
    </div>
  )
}

export default function ModsPage() {
  const [params, setParams] = useSearchParams()
  const view = params.get('view') === 'matrix' ? 'matrix' : 'inventory'
  const [inventory, setInventory] = useState<ModsInventory | null>(null)
  const [matrix, setMatrix] = useState<MatrixModView | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    const load = view === 'matrix' ? mods.matrix().then((body) => live && setMatrix(body)) : mods.inventory().then((body) => live && setInventory(body))
    load.catch((cause: unknown) => live && setError(cause instanceof Error ? cause.message : 'Could not load Mods.'))
    return () => { live = false }
  }, [view])

  const meta = useMemo(() => {
    if (view === 'matrix') return matrix ? `${matrix.matrices.length} MATRICES // ARK EDEN` : 'ARK EDEN'
    if (!inventory) return 'MODS'
    const connected = inventory.feed.entries.filter((entry) => entry.connected).length
    return `${inventory.feed.entries.length} MODS // ${connected} CONNECTED`
  }, [view, inventory, matrix])

  return (
    <div style={{ ...stack, padding: token.space.s4 }}>
      <PageTitle meta={meta}>Mods</PageTitle>
      <ChipGroup>
        <Chip selected={view === 'inventory'} onClick={() => setParams({})}>Inventory</Chip>
        <Chip selected={view === 'matrix'} onClick={() => setParams({ view: 'matrix' })}>Matrix Mod</Chip>
      </ChipGroup>
      {error && <StatusText tone="danger">{error}</StatusText>}
      {view === 'inventory' && inventory && <InventoryView inventory={inventory} />}
      {view === 'matrix' && matrix && <MatrixView view={matrix} />}
    </div>
  )
}
