import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { ActionCard, type AgentActionCard } from '@bttco/ui'
import { agentCardHands, getBanditMember, RYOZAN_BANDIT_ORCHESTRA } from '../../lib/agent-card-hands'
import { gala, type AgentAccess, type ApexAgentEvolutionResponse, type BenchPreviewRequest, type BenchPreviewResponse, type OrchestraRouting, type User } from '../../lib/api'
import { compileTemplate } from './template'
import { sourceAgentMarks } from './source-icons'
import type { DesktopPortalData } from '../../lib/desktop-portal-api'
import type { DesktopDeckAction } from './ActionDeck'

// AgentEvolutionStudio owns candidate editing, not the frozen founder board.
// This projection extends the approved board and reuses its read contracts.
// Draft preparation is local only, never a substitute for an accepted agent job.
interface Props {
  actions: DesktopDeckAction[]
  user: User
  /** True when the API can serve Ark agent evolution (Bench, trials, profiles). */
  evolution?: boolean
  workflow?: string | null
  benchView?: string | null
  data: DesktopPortalData
  receipts: Record<string, unknown>[]
  onOpen: (workflow: string) => void
  onNavigate: (tab: string) => void
  onOpenConversation: (id: string) => void
  onOpenBooking: (id: string) => void
  onPreview: (preview: BenchPreviewResponse) => void
  renderConversations?: (agent: string) => ReactNode
}
const roster = [
  ['gala', 'Gala', 'Gala · The Heavenly Sovereign · Maestro (👑 Ultra)', 'var(--gold)'],
  ['spyder', 'Sōkō', 'Sōkō · The Timely Rain · Shop Boss (🔥 Super)', '#D9BF95'],
  ['bella', 'Ensei', 'Ensei · The Prodigy · Front Desk (⚡ Nano)', 'var(--accent-coral)'],
  ['mercury', 'Taisō', 'Taisō · The Magic Traveller · Courier (⚡ Nano)', '#B795E8'],
  ['booky', 'Saishin', 'Saishin · Little Whirlwind · Booking (🔥 Super)', '#DD664E'],
  ['xar', 'Shishin', 'Shishin · Nine-Tattooed Dragon · Ledger (🔥 Super)', 'var(--alt-sky)'],
  ['herald', 'Riki', 'Riki · The Black Whirlwind · Herald Social (🔥 Super)', '#8FB89A'],
  ['docbot', 'Andōzen', 'Andōzen · The Divine Physician · Aftercare (⚡ Nano)', '#7FC3B1'],
  ['smoku', 'Rochishin', 'Rochishin · The Tattooed Monk · Guild & Style (👁️ Super-VL)', 'var(--gold)'],
] as const
const panel: CSSProperties = { border: '1px solid var(--line)', borderRadius: 16, background: 'var(--well)', padding: '13px 14px', minWidth: 0 }
const column: CSSProperties = { display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }
function Block({ title, children }: { title: string; children: ReactNode }) {
  return <section style={panel}><h3>{title}</h3>{children}</section>
}
function Mark({ id }: { id: string }) {
  if (id === 'herald') return <span aria-hidden="true" style={{display:'inline-block',width:24,height:24,background:'currentColor',mask:'url(/assets/herald-horn.png) center / contain no-repeat',WebkitMask:'url(/assets/herald-horn.png) center / contain no-repeat'}} />
  const paths = sourceAgentMarks[id] ?? '<path d="M12 5v14M5 12h14"/>'
  return <>{compileTemplate(`<svg aria-hidden="true" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`).render({})}</>
}

export default function FounderHubWorkspace(props: Props) {
  // The route guard is repeated at the component boundary so a future caller
  // cannot accidentally mount the founder reader for an artist or collector.
  return props.user.role === 'founder' ? <FounderBoard key={props.user.id} {...props} /> : <p role="alert">The founder board is available only to founder accounts.</p>
}

function FounderBoard({ user, evolution = false, workflow, benchView, data, receipts, onOpen, onNavigate, onOpenConversation, onOpenBooking, actions, onPreview, renderConversations }: Props) {
  const [selected, setSelected] = useState<string>('gala')
  const [cast, setCast] = useState<{agent: string; id: string} | null>(null)
  const [estimateHours, setEstimateHours] = useState('')
  const [estimateRate, setEstimateRate] = useState('')
  const assignedCards = [...(agentCardHands[selected] ?? []),...actions.filter(action=>['models','access','train','conversations'].includes(action.id) && action.shell).map(action=>({id:action.id,name:action.title,description:action.description,shell:action.shell!}))]
  const [cardDrafts,setCardDrafts] = useState<Record<string,Partial<AgentActionCard>>>({})
  const originalCard = cast?.agent === selected ? assignedCards.find(card => card.id === cast.id) : undefined
  const cardKey = `${selected}:${cast?.id ?? ''}`
  const castCard = originalCard ? {...originalCard,...cardDrafts[cardKey]} : undefined
  const [statsOpen, setStatsOpen] = useState(false)
  const [floorLane,setFloorLane] = useState<'inbox'|'bookings'>('inbox')
  const [revision, setRevision] = useState(0)
  const [read, setRead] = useState<{ agent: string; data?: ApexAgentEvolutionResponse; error?: string; at?: string } | null>(null)
  const agent = roster.find(item => item[0] === selected) ?? roster[1]
  const bandit = getBanditMember ? getBanditMember(selected) : (RYOZAN_BANDIT_ORCHESTRA?.find(item => item.id === selected || item.legacyId === selected) ?? { id: 'soko', legacyId: 'spyder', name: 'Sōkō', moniker: 'The Timely Rain', fullTitle: 'Sōkō (Song Jiang) · The Timely Rain', star: 'Tenkai · Chief Star', archetype: 'Legendary Creature — Bandit Conductor', domain: 'Ryozan Conductor', role: 'Conductor', nemotronModel: 'nvidia/nemotron-3-super-120b-a12b', manaPip: '🔥 Super' as const, accent: '#D9BF95', rgb: '217,191,149', npcGreeting: 'The Hall of Loyalty is open.' })
  const dealtHand = (agentCardHands[selected] ?? []).slice(0, 3)
  // A card is sealed only by a real tool receipt from the latest agent reply.
  const receiptFor = (card: AgentActionCard) => receipts.find(item => item.tool === card.mcpTool && item.status === 'succeeded')
  const supported = evolution && (selected === 'bella' || selected === 'gala' || selected === 'ensei')
  const current = read?.agent === selected ? read : null
  useEffect(() => {
    let active = true
    if (!evolution || (selected !== 'bella' && selected !== 'gala')) return
    void gala.evolution.get(selected).then(data => {
      if (!data.ok) throw new Error('The agent service did not confirm this read.')
      if (data.profile && data.profile.agentId !== selected) throw new Error('The returned profile belongs to another agent.')
      if (active) setRead({ agent: selected, data, at: new Date().toLocaleTimeString() })
    }).catch(error => { if (active) setRead({ agent: selected, error: error instanceof Error ? error.message : 'Agent state could not be read.' }) })
    return () => { active = false }
  }, [selected, revision, evolution])
  const profile = current?.data?.profile
  const trials = current?.data?.trials
  // Without Ark evolution the panel shows the agent's own profile, not a missing-contract notice.
  const standalone = `${bandit.fullTitle} runs ${bandit.nemotronModel} (${bandit.manaPip}). ${bandit.domain}.`
  const notice = !evolution ? standalone : !supported ? `${bandit.fullTitle} has no stored evolution profile yet.` : !current ? 'Reading agent state…' : current.error ?? (!profile ? 'No profile was returned for this agent.' : '')
  const active = actions.find(action => action.id === workflow)
  const unread = data.inbox.data?.items.filter(item=>item.unread_count>0||item.is_unread)
  const pending = data.bookings.data?.items.filter(item=>item.status==='pending')
  const floorThread = unread?.[0]??data.inbox.data?.items[0]
  const floorBooking = pending?.[0]??data.bookings.data?.items[0]
  const floorResource = floorLane==='inbox'?data.inbox:data.bookings
  const floorRecord = floorLane==='inbox'?floorThread:floorBooking
  const floorName = floorLane==='inbox'?(floorThread?.name??floorThread?.booking_context?.client_name??'Conversation'):(floorBooking?.resolved_name??floorBooking?.client_name??floorBooking?.guest_name??'Booking')
  const floorContext = floorLane==='inbox'?(floorThread?.last_message?.content??floorThread?.booking_context?.description??'Open the thread to read its messages.'):(floorBooking?.description??'Open the booking to review its details.')
  const details = <><p>Profile v{profile?.version} · revision {profile?.revision} · {profile?.status}</p><p>Provider synchronization: {profile?.providerBinding.syncStatus}</p><details><summary>Read directives ({profile?.directives.length ?? 0})</summary><ul>{profile?.directives.map((item,index)=><li key={index}>{item}</li>)}</ul></details></>
  const evidence = <>{notice ? <p role="status">{notice}</p> : trials?.length ? trials.map(trial=><details key={trial.trialId}><summary>{trial.kind === 'crucible' ? 'Candidate review' : trial.kind === 'gauntlet' ? 'Profile checks' : trial.kind.replaceAll('_',' ')} · {trial.state.replaceAll('_',' ')}</summary><p>{trial.outcomeSummary || 'No outcome summary returned.'}</p><small>Trial {trial.trialId}</small></details>) : <p>No trial evidence returned. This does not establish a passed evaluation.</p>}</>
  return <section data-founder-board data-training={workflow === 'train' ? 'true' : undefined} aria-label="Founder board" style={{display:'grid',gridTemplateColumns:'minmax(220px, 1fr) minmax(0, 2fr)',alignItems:'start',gap:14,padding:'14px 18px 16px',minWidth:0,...(workflow === 'train' ? {height:'100%',minHeight:0,boxSizing:'border-box',overflow:'hidden'} : {})}}>
    <style>{`[data-founder-board][data-training] > div{height:100%;min-height:0;overflow:hidden}[data-founder-board][data-training] > div:first-of-type > section{height:100%;min-height:0;box-sizing:border-box;display:flex;flex-direction:column}[data-founder-board][data-training] > div:first-of-type > section > div{min-height:0;overflow-y:auto}[data-bench] input,[data-bench] textarea{accent-color:var(--gold);color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:8px;font:inherit;box-sizing:border-box;width:100%}[data-bench] textarea{resize:none;padding:10px;min-height:0;flex:1}[data-bench] [role=tablist]{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:4}[data-bench] [role=tab]{text-align:center;padding:8px 4px}[data-bench] [role=tab][aria-selected=true]{border-color:var(--gold-decor);background:var(--surface)}[data-bench] label{font-size:12px;display:flex;flex-direction:column;gap:6px;min-height:0}[data-bench] footer{display:flex;gap:8px;align-items:center;flex-wrap:wrap;border-top:1px solid var(--line);padding-top:8px}`}</style>
    <style>{`[data-founder-board] h3{font-family:'JetBrains Mono',monospace!important;font-size:10px!important;font-weight:700!important;letter-spacing:.18em!important;color:var(--ink-ghost)!important;margin-bottom:12px!important}[data-founder-board] button{min-height:38px;color:var(--ink);background:transparent;border:1px solid var(--line);border-radius:10px;padding:8px 11px;cursor:pointer;text-align:left}[data-founder-board] p,[data-founder-board] li{font-size:12px;line-height:1.55;overflow-wrap:anywhere}[data-founder-board] ul{padding-left:18px}[data-founder-board] small{display:block;margin-top:5px}[data-founder-board] button[aria-pressed=true]{border-color:var(--gold-decor);background:var(--surface)}`}</style>
    <style>{`[data-founder-board]:not(:has([data-orchestra-roster])){grid-template-rows:minmax(0,1fr)!important}[data-founder-board] > [data-founder-workspace]{min-height:0!important;overflow:auto!important}`}</style>
    {workflow === 'roster' && <div style={column}>
      <Block title="Orchestra Roster">
        <div data-orchestra-roster style={{display:'grid',gap:4,flexShrink:1}}>{roster.map(([id,name,role,color]) => <div key={id} style={{minWidth:0}}>
          <div style={{position:'relative'}}>
          <button type="button" aria-pressed={id===selected} aria-expanded={id===selected && statsOpen} aria-controls={id===selected && statsOpen ? `agent-stats-${id}` : undefined} aria-label={`Inspect ${name}`} onClick={() => { if (id !== selected) { setRead(null); setSelected(id); setStatsOpen(true) } else { setStatsOpen(value=>!value) } }} style={{display:'flex',gap:10,alignItems:'center',width:'100%',paddingRight:48}}><span style={{color,flexShrink:0}}><Mark id={id}/></span><span><strong>{name}</strong><small>{role}</small></span></button>
          <button type="button" aria-label={`Refresh ${name} state`} title="Refresh stored agent state (discards unsaved access edits)" disabled={id===selected && supported && !current} onClick={() => { setRead(null); setSelected(id); setRevision(value=>value+1); if (id !== 'gala' && id !== 'bella') onOpen('access') }} style={{position:'absolute',right:3,top:'50%',transform:'translateY(-50%)',width:44,height:44,display:'grid',placeItems:'center',padding:0,border:0,opacity:id===selected && supported && !current ? .45 : 1}}>
            <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 7a7 7 0 0 1 11.8-1L20 9M4 15l2.1 3A7 7 0 0 0 17.9 17"/></svg>
          </button>
          </div>
          {id===selected && statsOpen && <section id={`agent-stats-${id}`} aria-label={`${name} stats`} style={{padding:'8px 10px',border:'1px solid var(--line)',borderTop:0,borderRadius:'0 0 10px 10px',background:'var(--surface)'}}>
            {notice ? <p role="status">{notice}</p> : <>
              <small>Stored v{profile?.version} · r{profile?.revision} · {profile?.status}</small>
              <dl style={{display:'grid',gridTemplateColumns:'repeat(2,minmax(0,1fr) auto)',gap:'4px 7px',margin:'8px 0'}}>{Object.entries(profile?.traits??{}).map(([key,value])=><div key={key} style={{display:'contents'}}><dt style={{overflowWrap:'anywhere',fontSize:9}}>{key==='oneQuestionAtATime'?'One at a time':key.replace(/([A-Z])/g,' $1')}</dt><dd style={{margin:0,fontSize:11}}>{typeof value==='boolean'?(value?'On':'Off'):value}</dd></div>)}</dl>
              <small>{trials?.length??0} returned trials</small><small>Provider sync: {profile?.providerBinding.syncStatus??'Unknown'}</small>
            </>}
          </section>}
        </div>)}</div>
      </Block>
    </div>}
    <div style={column} data-founder-workspace={workflow || 'overview'}>
      {workflow && workflow !== 'roster' && <nav aria-label="Selected agent" style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:8}}><strong>{agent[1]}</strong><button onClick={()=>onOpen('roster')}>Manage Orchestra</button></nav>}
      {!workflow && <section aria-label="Hub overview" data-hub-overview style={{display:'grid',gap:16}}>
        <style>{`[data-hub-overview] h4{margin:0 0 8px}[data-hub-overview] button small{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}`}</style>
        <header><h2>Shop overview</h2><p>Your shop’s current workload, separate from agent tools.</p><nav aria-label="Hub management" style={{display:'flex',gap:8}}><button onClick={()=>onOpen('roster')}>Manage Orchestra</button><button onClick={()=>onOpen('craft')}>Card Workshop</button></nav></header>
        <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(150px,1fr))',gap:12}}>
          <Block title="Unread conversations"><strong style={{fontSize:28}}>{unread?.length ?? 'Unavailable'}</strong><p>{data.inbox.data ? `Of ${data.inbox.data.items.length} loaded threads` : data.inbox.status==='loading'?'Loading inbox…':data.inbox.error || 'Inbox not available.'}</p></Block>
          <Block title="Pending bookings"><strong style={{fontSize:28}}>{pending?.length ?? 'Unavailable'}</strong><p>{data.bookings.data ? `Of ${data.bookings.data.items.length} loaded bookings` : data.bookings.status==='loading'?'Loading bookings…':data.bookings.error || 'Bookings not available.'}</p></Block>
          <Block title="Booking coverage"><strong style={{fontSize:28}}>{data.bookings.data ? `${data.bookings.data.items.length} / ${data.bookings.data.total}` : 'Unavailable'}</strong><p>Bookings shown of all bookings on record.</p></Block>
        </div>
        <Block title="Needs attention">
          <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(220px,1fr))',gap:16}}>
          {!!unread?.length && <section><h4>Unread conversations</h4>{unread.slice(0,3).map(item=><button key={item.id} onClick={()=>onOpenConversation(item.id)} style={{display:'block',width:'100%',marginTop:6}}>Open {item.name ?? item.booking_context?.client_name ?? 'conversation'}<small>{item.last_message?.content?.slice(0,120) || 'Read the thread to review the request.'}</small></button>)}</section>}
          {!!pending?.length && <section><h4>Pending booking requests</h4>{pending.slice(0,3).map(item=><button key={item.id} onClick={()=>onOpenBooking(item.id)} style={{display:'block',width:'100%',marginTop:6}}>Review {item.resolved_name ?? item.client_name ?? item.guest_name ?? 'booking'}<small>{item.description?.slice(0,120) || 'Review scheduling and request details.'}</small></button>)}</section>}
          {unread?.length===0 && pending?.length===0 && <p>No unread threads or pending bookings in the loaded snapshot.</p>}
          {(!unread || !pending) && <p role="status">Some shop data is unavailable or still loading. This is not an all-clear.</p>}
          </div>
        </Block>

      </section>}
      {workflow === 'roster' && <section aria-label={`${agent[1]} management`} style={panel}>
        <h3>{bandit.fullTitle} · Agent management</h3>
        <div data-npc-dialogue-banner={bandit.id} style={{border:'2px solid var(--ukiyoe-sumi-ink, #121212)',borderRadius:12,background:'linear-gradient(135deg, rgba(18,18,18,0.96) 0%, rgba(29,53,87,0.28) 100%)',boxShadow:'inset 3px 0 0 var(--sidelight, #cba15b)',padding:'12px 14px',marginBottom:12,display:'grid',gap:8}}>
          <header style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:8,flexWrap:'wrap'}}>
            <div>
              <strong style={{fontSize:14,color:'var(--rp-bone, #dad1b9)'}}>{bandit.fullTitle}</strong>
              <small style={{color:'var(--ukiyoe-kin-gold, #cba15b)'}}>{bandit.star} · {bandit.archetype}</small>
            </div>
            <span data-npc-mana-pip={bandit.manaPip} style={{fontFamily:'JetBrains Mono, monospace',fontSize:10,fontWeight:700,padding:'3px 8px',borderRadius:99,border:'1px solid var(--ukiyoe-kin-gold, #cba15b)',background:'rgba(194,75,42,0.2)',color:'var(--rp-bone, #dad1b9)'}}>{bandit.manaPip} · {bandit.nemotronModel}</span>
          </header>
          <p data-npc-greeting style={{fontStyle:'italic',color:'var(--rp-bone, #dad1b9)',margin:0}}>“{bandit.npcGreeting}”</p>
          <div data-npc-quest-queue style={{display:'flex',gap:12,flexWrap:'wrap',fontFamily:'JetBrains Mono, monospace',fontSize:10,color:'var(--ink-mute)'}}>
            <span>Quest Queue — Unread DMs: {unread ? unread.length : '—'}</span>
            <span>Pending Deposits/Bookings: {pending ? pending.length : '—'}</span>
            <span>Social Drops Ready: {data.club?.data ? data.club.data.items.length : '—'}</span>
          </div>
          <div data-npc-dealt-hand style={{display:'grid',gridTemplateColumns:'repeat(auto-fit, minmax(170px, 1fr))',gap:8,marginTop:4}}>
            {dealtHand.map(card => {
              const receipt = receiptFor(card)
              return <div key={card.id} data-dealt-card={card.id} style={{border:'1.5px solid var(--ukiyoe-sumi-ink, #121212)',borderRadius:8,padding:'8px 10px',background:'var(--ukiyoe-washi-plate, #ccc4ad)',color:'var(--ukiyoe-sumi-ink, #121212)',position:'relative'}}>
                <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:4}}>
                  <strong style={{fontSize:11,color:'#121212'}}>{card.id === 'memento' ? 'Seal Memory Shard' : card.name}</strong>
                  <span style={{fontFamily:'JetBrains Mono, monospace',fontSize:9,fontWeight:700,padding:'1px 5px',borderRadius:99,background:'#121212',color:'#dad1b9'}}>{card.manaPip ?? bandit.manaPip}</span>
                </div>
                <small style={{fontSize:9.5,color:'#2a2620',marginTop:3}}>{card.oracleText ?? card.description}</small>
                {receipt && <span data-hanko-seal="executed" style={{display:'inline-flex',alignItems:'center',gap:4,marginTop:6,padding:'2px 6px',border:'2px solid #D94E34',borderRadius:99,color:'#D94E34',fontWeight:900,fontSize:10,fontFamily:'JetBrains Mono, monospace',transform:'rotate(-5deg)'}}>済 · {String(receipt.correlation_id ?? receipt.tool)}</span>}
              </div>
            })}
          </div>
        </div>
        <p>{notice || `Profile revision ${profile?.revision}; provider sync ${profile?.providerBinding.syncStatus ?? 'Unknown'}`}</p>
        <button onClick={()=>onOpen('craft')}>Open Card Workshop</button>
      </section>}
      {workflow === 'craft' && <section data-card-workshop={selected} aria-label={`${agent[1]} card workshop`} style={panel}>
        <style>{`[data-card-workshop] input,[data-card-workshop] textarea{background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px;font:inherit}[data-card-workshop] label{display:block}`}</style>
        <h3>{agent[1]} · Card Workshop</h3>
        <p>Select a card to edit its presentation and preview its assigned shell with connected data and controls. Presentation drafts remain here while this Hub is open; they are not published to agent UIs.</p>
        <div aria-label={`${agent[1]} assigned cards`} style={{display:'flex',gap:8,flexWrap:'wrap'}}>
          {assignedCards.map(card=><button key={card.id} aria-pressed={castCard?.id===card.id} onClick={()=>setCast(castCard?.id===card.id ? null : {agent:selected,id:card.id})}>{card.name}</button>)}
        </div>
        {castCard && <div data-agent-action-shell={castCard.shell} style={{marginTop:12}}>
          <button onClick={()=>setCast(null)} aria-label="Close agent card">Close card</button>
          <fieldset style={{display:'grid',gap:8,border:0,padding:'12px 0'}}><legend>Presentation draft</legend>
            <label>Card title<input aria-label="Card title" value={String(castCard.name)} onChange={event=>setCardDrafts(value=>({...value,[cardKey]:{...value[cardKey],name:event.target.value}}))} style={{display:'block',width:'100%',boxSizing:'border-box'}} /></label>
            <label>Description<textarea aria-label="Card description" value={String(castCard.description)} onChange={event=>setCardDrafts(value=>({...value,[cardKey]:{...value[cardKey],description:event.target.value}}))} style={{display:'block',width:'100%',boxSizing:'border-box'}} /></label>
            <button disabled={!cardDrafts[cardKey]} onClick={()=>setCardDrafts(value=>{const next={...value};delete next[cardKey];return next})}>Reset presentation</button><small>Assigned shell: {castCard.shell}. Preview controls retain their normal save and action behavior.</small>
          </fieldset>
          <ActionCard card={castCard} agentName={agent[1]} accent={agent[3]}>
            <div style={{display:'grid',gap:8}}>
              {castCard.id==='models' ? <ModelRoutingEditor key={selected} agentId={selected}/> : castCard.id==='access' ? <AccessEditor key={selected} agentId={selected} accountId={user.id} onPreview={onPreview}/> : castCard.id==='train' ? <Bench key={selected} agentId={selected} name={agent[1]} data={current?.data} notice={notice} onPreview={onPreview}/> : castCard.id==='conversations' ? renderConversations?.(selected) : selected==='booky' && castCard.id!=='memento' ? <>
                {castCard.id==='estimate' && <fieldset style={{display:'grid',gridTemplateColumns:'repeat(2,minmax(0,1fr))',gap:10,border:0,padding:0}}>
                  <legend>Estimate calculator</legend>
                  <label>Hours<input aria-label="Estimated hours" type="number" min="0.25" step="0.25" value={estimateHours} onChange={event=>setEstimateHours(event.target.value)} style={{width:'100%'}} /></label>
                  <label>Hourly rate (USD)<input aria-label="Hourly rate in USD" type="number" min="0" step="1" value={estimateRate} onChange={event=>setEstimateRate(event.target.value)} style={{width:'100%'}} /></label>
                  <output aria-live="polite" style={{gridColumn:'1 / -1'}}>{estimateHours && estimateRate && Number(estimateHours)>0 && Number(estimateRate)>=0 && Number.isFinite(Number(estimateHours)*Number(estimateRate)) ? new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(Number(estimateHours)*Number(estimateRate)) : 'Enter hours and rate to calculate.'}</output>
                  <small style={{gridColumn:'1 / -1'}}>Local calculation only. Rate entered by you. Not saved, sent, or a confirmed quote.</small>
                </fieldset>}
                <p>Review a booking before preparing {castCard.id==='estimate'?'an estimate':castCard.id==='hold-slot'?'a slot hold':'a schedule change'}. Nothing is confirmed from this card.</p>
                {data.bookings.status==='error' ? <p role="alert">Bookings could not be loaded. Open Booking to retry.</p> : <p>{data.bookings.data?.items.length ?? 0} loaded bookings · {pending?.length ?? 0} pending in this snapshot</p>}
                {floorBooking && <button onClick={()=>onOpenBooking(floorBooking.id)}>Review next booking</button>}
                <button onClick={()=>onNavigate('books')}>Open booking workspace</button>
              </> : castCard.id==='memento' || castCard.id==='write-ark' ? <>
                <p>Review what {agent[1]} is taught in the Bench. This card does not write a memory or change a live profile.</p>
                <button onClick={()=>onOpen('train')}>Open teaching tools</button>
              </> : castCard.id==='report-up' ? <><p>{unread ? `${unread.length} unread messages` : 'Inbox unavailable'} · {pending ? `${pending.length} pending bookings` : 'Bookings unavailable'}</p>{evidence}<button onClick={()=>onOpen('runs')}>Review recorded evidence</button></> : <>
                <p>Start with the current records, then use the connected workspace to take action.</p>
                <button onClick={()=>onOpen('brief')}>Review current work</button>
                <button onClick={()=>onNavigate(selected==='herald'?'club':selected==='xar'?'studio':'courier')}>Open {selected==='herald'?'Club':selected==='xar'?'Commerce':'Inbox'}</button>
              </>}
            </div>
          </ActionCard>
        </div>}
        {!assignedCards.length && <p>No action shell is assigned to this agent yet.</p>}
        <details style={{marginTop:12}}><summary>Stored agent details</summary>
        {notice ? <p role="status">{notice}</p> : <>
          <p>Profile v{profile?.version} · revision {profile?.revision} · {profile?.status}</p>
          <p>Provider sync: {profile?.providerBinding.syncStatus ?? 'Unknown'}</p>
          <dl>{Object.entries(profile?.traits ?? {}).map(([key,value])=><div key={key}><dt>{key.replace(/([A-Z])/g,' $1')}</dt><dd>{typeof value==='boolean'?(value?'On':'Off'):value}</dd></div>)}</dl>
          <p>{trials?.length ?? 0} returned trials</p>
        </>}
        </details>
      </section>}
      <ActionCardWorkspace action={active} agent={agent}>
      {workflow === 'conversations' && renderConversations?.(selected)}
      {workflow === 'models' && <ModelRoutingEditor key={`${user.id}:${selected}`} agentId={selected} />}
      {workflow === 'access' && <AccessEditor key={`${user.id}:${selected}:${revision}`} agentId={selected} accountId={user.id} onPreview={onPreview} />}
      {workflow === 'brief' && <section data-maestro-floor aria-label="The floor" style={{...panel,display:'flex',flexDirection:'column',gap:12,borderColor:'var(--gold-decor)'}}>
        <style>{`[data-maestro-floor] h3{margin:0!important}[data-maestro-floor] small{font-size:11px;line-height:1.4;margin-top:3px;color:var(--ink-mute)}[data-maestro-floor] p{margin:0}[data-maestro-floor] button:focus-visible{outline:2px solid var(--gold);outline-offset:2px}[data-maestro-floor] summary{cursor:pointer;font-size:11px;color:var(--ink-mute)}[data-maestro-floor] [data-floor-context]{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;overflow:hidden}[data-maestro-floor] [data-floor-tools] button{font-size:12px;min-height:34px;padding:6px 9px}`}</style>
        <header style={{display:'flex',alignItems:'center',gap:10}}><h3>The floor</h3><span aria-hidden="true" style={{height:1,flex:1,background:'var(--line)'}}/><small>Ryozan snapshot</small></header>
        <div aria-label="Choose work to review" style={{display:'grid',gridTemplateColumns:'repeat(2,minmax(0,1fr))',gap:8}}>
          <button type="button" aria-pressed={floorLane==='inbox'} onClick={()=>setFloorLane('inbox')} style={{padding:'10px 12px'}}><strong>Read the inbox</strong><small>{unread?`${unread.length} unread in ${data.inbox.data?.items.length} loaded threads`:data.inbox.status==='loading'?'Loading threads…':'Inbox unavailable'}</small></button>
          <button type="button" aria-pressed={floorLane==='bookings'} onClick={()=>setFloorLane('bookings')} style={{padding:'10px 12px'}}><strong>Review bookings</strong><small>{pending?`${pending.length} pending in ${data.bookings.data?.items.length} loaded bookings`:data.bookings.status==='loading'?'Loading bookings…':'Bookings unavailable'}</small></button>
        </div>
        <section aria-label="Your call" style={{display:'flex',flexDirection:'column',gap:10,padding:'12px 13px',border:'1px solid var(--gold-decor)',borderRadius:12,background:'var(--surface)'}}>
          <header style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:8}}><span style={{fontFamily:'JetBrains Mono,monospace',fontSize:10,fontWeight:700,letterSpacing:'.16em',color:'var(--gold)'}}>YOUR CALL</span><small>{floorLane==='inbox'?'Courier':'Bookings'} · {floorRecord?(floorLane==='inbox'?(floorThread?.unread_count||floorThread?.is_unread?'Unread':'Loaded thread'):floorBooking?.status):floorResource.status}</small></header>
          {floorRecord?<div style={{borderLeft:'2px solid var(--gold-decor)',paddingLeft:11,minWidth:0}}><strong style={{display:'block',fontFamily:'Poppins,sans-serif',fontSize:17,fontWeight:800,overflowWrap:'anywhere'}}>{floorName}</strong><p data-floor-context style={{color:'var(--ink-mute)',marginTop:5}}>{floorContext}</p></div>:<p role="status">{floorResource.status==='loading'?'Loading your shop snapshot…':floorResource.error??`No ${floorLane==='inbox'?'threads':'bookings'} in this snapshot.`}</p>}
          <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12,borderTop:'1px solid var(--line)',paddingTop:8}}><div><small>Next step</small><p>{floorLane==='inbox'?'Read the thread and handle the reply.':'Check the request, artist and scheduling details.'}</p></div><div><small>Where it happens</small><p>{floorLane==='inbox'?'Courier keeps the conversation and reply history.':'Bookings owns the request and appointment.'}</p></div></div>
          <button type="button" onClick={()=>{if(floorLane==='inbox'&&floorThread)onOpenConversation(floorThread.id);else if(floorLane==='bookings'&&floorBooking)onOpenBooking(floorBooking.id);else onNavigate(floorLane==='inbox'?'courier':'books')}} style={{width:'100%',textAlign:'center',borderColor:'var(--gold-decor)',color:'var(--gold)',fontWeight:700}}>{floorRecord?(floorLane==='inbox'?'Open this conversation':'Review this booking'):(floorLane==='inbox'?'Open Courier':'Open Bookings')} <span aria-hidden="true">↗</span></button>
        </section>
        <section aria-label={`${agent[1]} controls`} style={{display:'flex',flexDirection:'column',gap:9}}>
          <header style={{display:'flex',alignItems:'center',gap:8}}><span style={{color:agent[3],display:'flex'}}><Mark id={selected}/></span><div><strong style={{fontSize:13}}>{agent[1]}</strong><small>{agent[2]} · selected agent</small></div><span aria-hidden="true" style={{height:1,flex:1,background:'var(--line)'}}/></header>
          <div data-floor-tools style={{display:'grid',gridTemplateColumns:'repeat(3,minmax(0,1fr))',gap:6}}>{evolution&&<button type="button" onClick={()=>onOpen('train')}>Open Bench</button>}<button type="button" onClick={()=>onOpen('models')}>Models & routing</button><button type="button" onClick={()=>onOpen('access')}>Scopes & tools</button></div>
        </section>
        <details style={{borderTop:'1px solid var(--line)',paddingTop:8}}><summary>Snapshot & agent connections</summary><small>Selecting an agent changes its controls, not these records.</small>{notice?<p role="status">{notice}</p>:details}{evolution&&<button type="button" onClick={()=>onOpen('runs')}>Review recorded trials</button>}</details>
      </section>}
      {workflow === 'runs' && <>
        <Block title={`Recorded trials · ${agent[1]}`}>{evidence}</Block>
        <Block title="Report up"><details><summary>Latest Gala tool receipts in this session ({receipts.length})</summary>{receipts.length ? <pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{JSON.stringify(receipts,null,2)}</pre> : <p>No tool receipts available from the latest Gala reply. This is not a session total or global run count.</p>}</details></Block>
      </>}
      {workflow === 'train' && <Bench key={`${user.id}:${selected}:${profile?.revision ?? 'loading'}`} view={benchView} agentId={selected} name={agent[1]} data={current?.data} notice={notice} onPreview={onPreview} />}
      {workflow && !active && <p role="alert">This Hub workflow is not recognized. Close it to return to the overview.</p>}
      </ActionCardWorkspace>
    </div>
  </section>
}

// ActionCard already owns shell rendering and accepts real workflow children.
// Keep action selection separate from agent identity and API ownership.
function ActionCardWorkspace({action, agent, children}: {action?: DesktopDeckAction; agent: typeof roster[number]; children: ReactNode}) {
  if (!action?.shell) return <>{children}</>
  return <div data-workflow-card={action.id} data-card-agent={agent[0]} style={{minWidth:0,minHeight:0,overflowY:'auto'}}>
    <style>{`[data-workflow-card] article section{box-shadow:none}[data-workflow-card] article > div > section{border:0!important;background:transparent!important;padding:0!important}`}</style>
    <ActionCard card={{id:action.id,name:`${agent[1]} · ${action.title}`,description:action.description,shell:action.shell}} agentName={agent[1]} accent={agent[3]}>{children}</ActionCard>
  </div>
}

// This brand-specific editor uses Ark's existing Orchestra record. No local
// storage, account preference or second assignment registry is introduced.
function ModelRoutingEditor({agentId}:{agentId:string}) {
  const [provider,setProvider]=useState('')
  const [search,setSearch]=useState('')
  const [catalogPage,setCatalogPage]=useState(0)
  const [routing,setRouting]=useState<OrchestraRouting|null>(null)
  const legacyActorId = ({ soko: 'spyder', shishin: 'xar', ensei: 'bella', saishin: 'booky', taiso: 'mercury', rochishin: 'smoku', andozen: 'doc', riki: 'herald', docbot: 'doc' } as Record<string, string>)[agentId] ?? agentId
  const actor=agentId==='gala'?'agent:gala':`agent:bttco-${legacyActorId}`
  const [draft,setDraft]=useState<{defaultModel:string|null;fallbacks:string[];expectedRevision:number}|null>(null)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const [reload,setReload]=useState(0)
  const [notice,setNotice]=useState('')
  const [readOnly,setReadOnly]=useState(false)
  const flight=useRef(false)
  const alive=useRef(true)
  useEffect(()=>{let active=true;alive.current=true;const load=async()=>{try{const result=await gala.getModelRouting();if(!result.ok||!['orchestra:bttco','orchestra:ryozan'].includes(result.routing.orchestraId)||!['bttco_chat','ryozan_chat'].includes(result.routing.scope))throw new Error('Orchestra identity mismatch. No assignments loaded.');if(active){setRouting(result.routing);setReadOnly(result.readOnly===true);setError('')}}catch(reason){if(active)setError(reason instanceof Error?reason.message:'Ark configuration unavailable.')}};void load();const timer=setInterval(()=>void load(),10000);return()=>{active=false;alive.current=false;clearInterval(timer)}},[reload])
  const member=routing?.members.find(item=>item.actorRef===actor || item.actorRef===`agent:ryozan-${agentId}`)
  const sections=routing?.inventory?.sections??[]
  const section=sections.find(item=>item.id===provider)??sections[0]
  const matches=(section?.models??[]).filter(model=>`${model.label} ${model.modelMaker??''} ${model.provider} ${model.id}`.toLowerCase().includes(search.toLowerCase()))
  const safePage=Math.min(catalogPage,Math.max(0,Math.ceil(matches.length/2)-1))
  const current=member?.assignment
  const selectedModel=draft?draft.defaultModel:current?.defaultModel
  const fallbacks=draft?.fallbacks??current?.fallbacks??[]
  const stale=!!draft && draft.expectedRevision!==(current?.revision??0)
  const changed=!!draft && (draft.defaultModel!==(current?.defaultModel??null) || JSON.stringify(draft.fallbacks)!==JSON.stringify(current?.fallbacks??[]))
  const modelLabel=(id:string)=>routing?.models.find(model=>model.id===id)?.label??id
  const edit=()=>{setDraft({defaultModel:current?.defaultModel??null,fallbacks:current?.fallbacks??[],expectedRevision:current?.revision??0});setNotice('')}
  const save=async()=>{if(!draft||flight.current)return;flight.current=true;setBusy(true);setNotice('');try{const result=await gala.saveModelRouting({actorRef:actor,...draft});if(!result.ok||result.actorRef!==actor||result.assignment.revision!==draft.expectedRevision+1)throw new Error('Save receipt mismatch. Reload before retrying.');if(alive.current){setRouting(value=>value&&({...value,members:value.members.map(item=>item.actorRef===actor?{...item,assignment:result.assignment}:item)}));setDraft(null);setNotice('Saved to the shared ArkStation Orchestra. Used on the next desktop chat or Bench request.')}}catch(reason){if(alive.current){setError(reason instanceof Error?reason.message:'Save failed. Reload before retrying.');setDraft(null)}}finally{flight.current=false;if(alive.current)setBusy(false)}}
  return <section data-model-desk aria-label="Model desk" style={{...panel,display:'flex',flexDirection:'column',gap:10}}>
    <style>{`[data-model-desk] h3{margin:0!important}[data-model-desk] small{margin-top:0;font-size:11px;line-height:1.4;color:var(--ink-mute)}[data-model-desk] button{min-height:32px;padding:5px 9px;font-size:12px}[data-model-desk] button:disabled{opacity:.45;cursor:not-allowed}[data-model-desk] input,[data-model-desk] select{width:100%;min-width:0;box-sizing:border-box;background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px;font:inherit;font-size:12px}[data-model-desk] summary{cursor:pointer;font-size:11px;color:var(--ink-mute)}[data-model-desk] fieldset{min-width:0}[data-model-desk] p{margin:6px 0}`}</style>
    <header style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:8,borderBottom:'1px solid var(--line)',paddingBottom:10}}><div><h3>Model desk</h3><small>{readOnly?'NVIDIA Nemotron on Nebius Token Factory · set by agent profile':'Shared with ArkStation'}</small></div><button disabled={busy} onClick={()=>{setDraft(null);setReload(value=>value+1)}}>{readOnly?'Refresh':'Refresh from Ark'}</button></header>
    {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
    {!routing&&!error&&<p role="status">Loading shared assignments…</p>}
    {routing&&<>
      <div style={{display:'flex',alignItems:'center',gap:8}}><strong aria-label="Selected orchestra agent" style={{flex:1}}>{member?.displayName ?? `${agentId} · Not registered`}</strong>{!draft&&!readOnly&&<button disabled={busy||!!error||!member} onClick={edit} style={{flexShrink:0}}>Edit assignment</button>}</div>
      <fieldset disabled={busy||!!error||stale} style={{border:'1px solid var(--line)',borderRadius:10,padding:'9px 10px',margin:0,display:'grid',gap:8,background:'var(--surface)'}}>
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:8}}><small>{draft?'LOCAL DRAFT':'SAVED ROUTE'} · r{draft?.expectedRevision??current?.revision??0}</small>{draft&&<button type="button" disabled={!draft.defaultModel&&!draft.fallbacks.length} onClick={()=>setDraft(value=>value&&({...value,defaultModel:null,fallbacks:[]}))}>Use account default</button>}</div>
        <div style={{fontSize:12,overflowWrap:'anywhere'}}><small>Primary</small><strong>{selectedModel?modelLabel(selectedModel):'Account default'}</strong></div>
        <div aria-label="Ordered fallback models" style={{display:'flex',flexWrap:'wrap',gap:5}}>{fallbacks.length?fallbacks.map((id,index)=><span key={id} style={{display:'inline-flex',alignItems:'center',gap:5,minWidth:0,maxWidth:'100%',border:'1px solid var(--line)',borderRadius:7,padding:'3px 6px'}}><small style={{overflowWrap:'anywhere'}}>{index+1}. {modelLabel(id)}</small>{draft&&<button type="button" aria-label={`Remove fallback ${index+1}`} onClick={()=>setDraft(value=>value&&({...value,fallbacks:value.fallbacks.filter(item=>item!==id)}))} style={{padding:'0 4px',minHeight:24,border:0}}>×</button>}</span>):<small>No explicit fallbacks</small>}</div>
      </fieldset>
      {stale&&<p role="alert">This assignment changed elsewhere. Refresh before saving.</p>}
      {!readOnly&&<section aria-label="Model catalog" style={{minWidth:0,display:'grid',gap:8}}>
        <div aria-label="Model providers" style={{display:'flex',flexWrap:'wrap',gap:4}}>{sections.map(item=><button type="button" key={item.id} aria-pressed={section?.id===item.id} onClick={()=>{setProvider(item.id);setCatalogPage(0)}} style={{display:'inline-flex',alignItems:'center',gap:6,minHeight:32,padding:'4px 8px',fontSize:12,borderColor:section?.id===item.id?'var(--gold)':'var(--line)',color:section?.id===item.id?'var(--gold)':'var(--ink)'}}>{item.label} <span style={{fontFamily:'JetBrains Mono, monospace',fontSize:10,color:'var(--gold)',borderLeft:'1px solid var(--line)',paddingLeft:6}}>{item.models.length}</span></button>)}</div>
        {!section?<p role="status">Runtime inventory is not available. Refresh from Ark to load provider sections.</p>:<>
          <input aria-label="Search model catalog" value={search} onChange={event=>{setSearch(event.target.value);setCatalogPage(0)}} placeholder={`Search ${section.label} models`}/>
          <small>{matches.length} matching models · {section.authState}</small>
          <div style={{display:'grid',gap:6}}>{matches.slice(safePage*2,safePage*2+2).map(model=>{const route=model.assignmentModelId??model.id;const usable=model.assignable&&routing.models.some(item=>item.id===route);return <article key={model.id} style={{...panel,padding:'8px 10px',borderRadius:10,borderColor:selectedModel===route?'var(--gold)':'var(--line)'}}>
            <div style={{display:'flex',flexWrap:'wrap',alignItems:'center',justifyContent:'space-between',gap:6}}><div style={{flex:'1 1 140px',minWidth:0}}><strong style={{display:'block',overflowWrap:'anywhere',fontSize:12.5}}>{model.label}</strong><small>{model.modelMaker??model.provider} · {model.source}</small></div>
            {draft&&<div style={{display:'flex',gap:4}}><button type="button" disabled={busy||!!error||stale||!usable||draft.defaultModel===route} onClick={()=>setDraft(value=>value&&({...value,defaultModel:route,fallbacks:value.fallbacks.filter(id=>id!==route)}))}>{draft.defaultModel===route?'Primary':'Set primary'}</button><button type="button" disabled={!draft.defaultModel||busy||!!error||stale||!usable||draft.fallbacks.length>=4||draft.defaultModel===route||draft.fallbacks.includes(route)} onClick={()=>setDraft(value=>value&&({...value,fallbacks:[...value.fallbacks,route]}))}>Add fallback</button></div>}</div>
            <details style={{marginTop:5}}><summary>{usable?'Routing details':'Unavailable for assignment'}</summary><p>{model.assignmentReason}</p><small style={{overflowWrap:'anywhere'}}>{route}</small></details>
          </article>})}</div>
          {!matches.length&&<p>No models match this search.</p>}
          <nav aria-label="Model catalog pages" style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:8}}><button disabled={safePage===0} onClick={()=>setCatalogPage(safePage-1)}>Previous models</button><small>{safePage+1} / {Math.max(1,Math.ceil(matches.length/2))}</small><button disabled={(safePage+1)*2>=matches.length} onClick={()=>setCatalogPage(safePage+1)}>Next models</button></nav>
          {section.warnings.length>0&&<details><summary>Runtime notes</summary>{section.warnings.map(note=><p key={note}>{note}</p>)}</details>}
        </>}
        {!!routing.inventory?.notes.length&&<details><summary>How models are grouped</summary>{routing.inventory.notes.map(note=><p key={note}>{note}</p>)}</details>}
      </section>}
    </>}
    {draft&&<footer style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:8,borderTop:'1px solid var(--line)',paddingTop:10}}><button type="button" disabled={busy} onClick={()=>setDraft(null)}>Discard draft</button><button type="button" disabled={busy||!!error||stale||!changed} onClick={()=>void save()} style={{borderColor:'var(--gold-decor)',color:'var(--gold)'}}>{busy?'Saving…':'Save shared assignment'}</button></footer>}
    {readOnly?<details><summary>How routing works</summary><p>Each agent calls its primary model first and falls back in order when a model is unavailable. Change an agent's model in its profile.</p></details>:<details><summary>Applies to desktop chat and Bench</summary><p>Catalog entries are routing choices, not verified provider access or tested tool support. Mobile, phone agents and unrelated ArkStation agents are unchanged. Edits stay local until saved to the shared assignment.</p></details>}
  </section>
}

function Bench({name,data,notice,agentId,onPreview,view}:{name:string;data?:ApexAgentEvolutionResponse;notice:string;agentId:string;onPreview:Props['onPreview'];view?:string|null}) {
  const tab = view==='teach'?'Teach':view==='test'?'Test':view==='review'?'Review':'Tune'
  const [page,setPage] = useState(0)
  const profile = data?.profile
  const [traits,setTraits] = useState(profile?.traits)
  const [directives,setDirectives] = useState(profile?.directives.join('\n') ?? '')
  const [lesson,setLesson] = useState('')
  const [reviewPage,setReviewPage] = useState(0)
  const [testPage,setTestPage] = useState(0)
  const traitEntries = Object.entries(traits ?? {})
  const trials = data?.trials ?? []
  const trial = trials[Math.min(testPage,Math.max(0,trials.length-1))]
  const changed = profile ? traitEntries.filter(([key,value])=>profile.traits[key as keyof typeof profile.traits]!==value).map(([key,value])=>`${key}: ${String(profile.traits[key as keyof typeof profile.traits])} → ${String(value)}`) : []
  if (profile && directives!==profile.directives.join('\n')) changed.push('Directives edited')
  if (lesson.trim()) changed.push('Teaching note prepared')
  const textArea:CSSProperties = {whiteSpace:'pre-wrap',overflowWrap:'anywhere',overflowY:'auto',minHeight:0,flex:1,fontSize:12,margin:0}
  return <section data-bench aria-label={`${name} training workbench`} style={{...panel,display:'flex',flexDirection:'column',height:'100%',minHeight:0,boxSizing:'border-box',gap:10,overflow:'hidden'}}>
    <header><h3>The bench · {name}</h3><small>{profile ? `v${profile.version} · revision ${profile.revision} · ${profile.status}` : 'Profile unavailable'}</small></header>
    <h4 id="bench-view-title">{tab==='Tune'?'Tune the response':tab==='Teach'?'Teach with an example':tab==='Test'?'Recorded tests':'Review your draft'}</h4>
    <div id="bench-panel" role="region" aria-labelledby="bench-view-title" style={{display:'flex',flexDirection:'column',gap:10,flex:1,minHeight:0,overflow:'hidden'}}>
      {notice ? <p role="status" style={textArea}>{notice}</p> : tab==='Tune' ? <>
        {page<Math.ceil(traitEntries.length/2) ? traitEntries.slice(page*2,page*2+2).map(([key,value])=><label key={key} style={{display:'grid',gridTemplateColumns:'1fr auto',gap:4,flexShrink:0}}>{key} <output>{String(value)}</output><input style={{gridColumn:'1 / -1',height:22}} aria-label={key} type={typeof value==='boolean'?'checkbox':'range'} min={0} max={key==='questionBudget'?5:100} checked={typeof value==='boolean'?value:undefined} value={typeof value==='number'?value:undefined} onChange={event=>setTraits(current=>current&&({...current,[key]:typeof value==='boolean'?event.target.checked:Number(event.target.value)}))}/></label>) : <label style={{flex:1}}>Directives · one per line<textarea aria-label="Draft directives" value={directives} onChange={event=>setDirectives(event.target.value)}/></label>}
        <footer style={{marginTop:'auto',flexShrink:0}}><button disabled={page===0} onClick={()=>setPage(value=>value-1)}>Previous</button><small>{page+1} / {Math.ceil(traitEntries.length/2)+1}</small><button disabled={page>=Math.ceil(traitEntries.length/2)} onClick={()=>setPage(value=>value+1)}>Next</button></footer>
      </> : tab==='Teach' ? <><label style={{flex:1}}>Teaching note<textarea aria-label="Teaching note" placeholder="Describe the desired behavior and give an example." value={lesson} onChange={event=>setLesson(event.target.value)}/></label><p>Draft only. No agent message or attachment is sent.</p></> : tab==='Test' ? <>
        <strong>Recorded tests</strong>{trial ? <><small>{trial.kind.replaceAll('_',' ')} · {trial.state.replaceAll('_',' ')}</small><p style={textArea}>{trial.outcomeSummary || 'No outcome summary returned.'}</p><footer><button disabled={testPage===0} onClick={()=>setTestPage(value=>value-1)}>Previous</button><small>{testPage+1} / {trials.length}</small><button disabled={testPage>=trials.length-1} onClick={()=>setTestPage(value=>value+1)}>Next</button></footer></> : <p>No recorded tests returned.</p>}<small>New AI tests require verified sandbox isolation. These records do not evaluate your unsaved edits.</small>
      </> : <><strong>{changed.length} local changes</strong><p style={textArea}>{changed.slice(reviewPage*3,reviewPage*3+3).join('\n\n') || 'No changes on this page.'}</p><footer><button disabled={reviewPage===0} onClick={()=>setReviewPage(value=>value-1)}>Previous</button><small>{reviewPage+1} / {Math.max(1,Math.ceil(changed.length/3))}</small><button disabled={(reviewPage+1)*3>=changed.length} onClick={()=>setReviewPage(value=>value+1)}>Next</button></footer></>}
    </div>
    <footer><button disabled={!profile || !changed.length} onClick={()=>{setTraits(profile?.traits);setDirectives(profile?.directives.join('\n')??'');setLesson('');setReviewPage(0)}}>Reset draft</button><button disabled title="Sandbox candidate storage has not been verified">Save candidate</button></footer>
    {profile && !notice && <PreviewControl agentId={agentId} draft={{profile_revision:profile.revision,traits,directives:directives.split('\n').map(item=>item.trim()).filter(Boolean),lesson}} onPreview={onPreview} />}
    <small role="status">Local draft only. Closing, refreshing or switching agents discards edits. Shared Apex writes and live promotion are disabled.</small>
  </section>
}

// Shared by the existing Bench and access editor: one preview lifecycle and
// destination, separate from saved configuration and real agent execution.
function PreviewControl({agentId,draft,onPreview}:{agentId:string;draft:Omit<BenchPreviewRequest,'scenario'>;onPreview:Props['onPreview']}) {
  const scenarios:Record<string,string> = {
    gala:'Shop planning rehearsal: I have several unanswered inquiries. What should I prioritize today? No current records are supplied.',
    bella:'Booking inquiry rehearsal: I want a traditional eagle on my forearm. What information do you need before we can book?',
    spyder:'Operations rehearsal: Find overdue booking follow-ups and explain the next steps. No current records are supplied.',
    mercury:'Inbox rehearsal: A collector asks whether their deposit confirmed the appointment. No payment or booking record is supplied.',
    booky:'Scheduling rehearsal: A collector requests next Saturday afternoon. Availability has not been checked.',
    xar:'Artist business rehearsal: What can I do to improve my booking page? No current analytics are supplied.',
    herald:'Club rehearsal: A member asks which rewards they can redeem. Their balance has not been checked.',
    docbot:'Aftercare rehearsal: A collector asks how to care for a fresh tattoo. Explain general care, limits and when to contact a qualified clinician. No health record is supplied.',
    smoku:'Learning rehearsal: An artist wants to study traditional eagle designs. Explain how to find source-backed examples without inventing authorship or references.',
  }
  const [scenario,setScenario] = useState(scenarios[agentId] ?? '')
  const [example,setExample] = useState('typical')
  const [pending,setPending] = useState(false)
  const [feedback,setFeedback] = useState('')
  const generation = useRef(0)
  const inFlight = useRef(false)
  const signature = JSON.stringify(draft)
  useEffect(()=>{generation.current+=1;setFeedback('');return()=>{generation.current+=1}},[signature,scenario,agentId])
  const preview = async () => {
    if(inFlight.current || scenario.trim().length<10)return
    const version=generation.current
    inFlight.current=true;setPending(true);setFeedback('Generating a text-only rehearsal through Ark…')
    try {
      const result=await gala.previewAgent(agentId,{...draft,scenario:scenario.trim()})
      if(generation.current!==version)return
      if(result.agent_id!==agentId || result.mode!=='illustrative_preview')throw new Error('Preview identity mismatch. Nothing was applied.')
      onPreview(result);setFeedback('Comparison added to Gala’s chat. Draft not applied.')
    } catch(error) {if(generation.current===version)setFeedback(error instanceof Error?error.message:'Preview failed. Nothing was applied.')}
    finally {inFlight.current=false;setPending(false)}
  }
  return <section aria-label="Response preview" style={{flexShrink:0,minWidth:0}}>
    <label style={{display:'block',fontSize:12}}>Try a situation <select aria-label="Preview example" value={example} onChange={event=>{const next=event.target.value;setExample(next);if(next!=='custom')setScenario(`${scenarios[agentId] ?? 'Explain your role and what information you need.'}${next==='missing'?' The information needed is missing. Ask one useful clarifying question and explain what must be checked.':next==='approval'?' The user asks you to act immediately. Explain the required checks and approval before any real action.':''}`)}}><option value="typical">Typical request</option><option value="missing">Missing information</option><option value="approval">Before taking action</option><option value="custom">My own example</option></select></label>
    <label style={{display:'block',fontSize:12}}>Example request <input aria-label="Preview scenario" maxLength={3000} value={scenario} onChange={event=>{setExample('custom');setScenario(event.target.value)}} style={{display:'block',width:'100%',boxSizing:'border-box',padding:8,color:'var(--ink)',background:'var(--surface)',border:'1px solid var(--line)',borderRadius:8}}/></label>
    <button type="button" disabled={pending || scenario.trim().length<10} onClick={()=>void preview()} style={{marginTop:6}}>{pending?'Comparing…':'Compare in Gala chat'}</button>
    <small role="status">{feedback.includes('Ark Model Gateway routing failed') ? 'Ark could not generate this comparison. Check Models & routing. Nothing was applied.' : feedback || 'Uses saved settings and registered tools. Example only: no customer records loaded, no tools run.'}</small>
  </section>
}

function AccessEditor({ agentId, accountId, onPreview }: { agentId: string; accountId: string; onPreview:Props['onPreview'] }) {
  const [access, setAccess] = useState<AgentAccess | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [saving, setSaving] = useState(false)
  const [reload, setReload] = useState(0)
  const [uncertain, setUncertain] = useState(false)
  const mounted = useRef(true)
  const inFlight = useRef(false)
  useEffect(() => {
    mounted.current = true
    let active = true
    void gala.getAccess(agentId).then(result => {
      if (result.agent_id !== agentId || result.account_id !== accountId || result.environment !== (result.editable ? 'local_dev' : 'standard')) throw new Error('Access owner mismatch. No settings loaded.')
      if (active) setAccess(result)
    }).catch(reason => { if (active) setError(reason instanceof Error ? reason.message : 'Could not load agent access.') })
    return () => { active = false; mounted.current = false }
  }, [agentId, accountId, reload])
  const refresh = () => { setAccess(null); setError(''); setNotice(''); setUncertain(false); setReload(value=>value+1) }
  const changeScope = (scope: string, enabled: boolean) => {
    setNotice('Unsaved changes')
    setAccess(value=>value && ({...value,scopes:enabled ? [...value.scopes,scope] : value.scopes.filter(item=>item!==scope),tools:enabled ? value.tools : value.tools.filter(id=>value.catalog.find(item=>item.id===id)?.scope!==scope)}))
  }
  const save = async () => {
    if (!access?.editable || inFlight.current || uncertain) return
    inFlight.current = true; setSaving(true); setError(''); setNotice('')
    try {
      const result = await gala.saveAccess(agentId, {revision:access.revision,tools:access.tools,scopes:access.scopes})
      if (result.agent_id !== agentId || result.account_id !== accountId || result.revision !== access.revision+1) throw new Error('Save receipt mismatch. Reload to verify the stored policy.')
      if (mounted.current) { setAccess(result); setNotice(`Saved revision ${result.revision}. Applies to your next desktop chat messages.`) }
    } catch (reason) {
      if (mounted.current) { setUncertain(true); setError(`${reason instanceof Error ? reason.message : 'Save failed.'} Reload stored access before trying again; the request may have saved.`) }
    } finally { inFlight.current=false; if (mounted.current) setSaving(false) }
  }
  const editable = !!access?.editable
  return <Block title="Scopes & tools">
    <p>{access && !editable ? 'The tools your selected agent can use on your account.' : 'Tool access for your selected agent on the desktop.'}</p>
    <details><summary>Access boundaries</summary><p>{access && !editable ? 'Each agent uses the registered tools its role allows. Editing these choices is available when the API runs with LOCAL_DEV_MODE enabled.' : 'These controls enforce tool access, not personality instructions. Mobile, other accounts and production permissions are unchanged. Enable a scope, then select its tools. Removing a scope removes its tools. Only registered tools can be assigned.'}</p></details>
    {error && <p role="alert">{error}</p>}
    {(!access && !error) && <p role="status">Loading registered access…</p>}
    {(editable || error) && <button type="button" disabled={saving} onClick={refresh}>Reload stored access</button>}
    {access && <>
      {editable && <small>Revision {access.revision} · {access.updated_at ? `Saved ${new Date(access.updated_at).toLocaleString()}` : 'Default agent configuration'}</small>}
      {editable && !saving && !uncertain && <PreviewControl agentId={agentId} draft={{access:{revision:access.revision,tools:access.tools,scopes:access.scopes}}} onPreview={onPreview} />}
      <fieldset disabled={!editable || saving || uncertain} style={{border:0,padding:0,margin:'16px 0'}}>
        {[...new Set(access.catalog.map(tool=>tool.scope))].sort().map(scope=><details key={scope} style={{borderBottom:'1px solid var(--line)'}}>
          <summary style={{cursor:'pointer',minHeight:44,padding:'10px 0',boxSizing:'border-box',fontSize:12}}><strong>{scope}</strong><span style={{float:'right',color:'var(--ink-ghost)'}}>{access.scopes.includes(scope) ? `${access.catalog.filter(tool=>tool.scope===scope && access.tools.includes(tool.id)).length}/${access.catalog.filter(tool=>tool.scope===scope).length} tools` : 'Off'}</span></summary>
          <div style={{padding:'0 10px 10px'}}>
          <label style={{display:'flex',gap:10,alignItems:'center',minHeight:44}}><input type="checkbox" aria-label={scope} checked={access.scopes.includes(scope)} onChange={event=>changeScope(scope,event.target.checked)} /><strong>Enable scope</strong></label>
          {access.catalog.filter(tool=>tool.scope===scope).map(tool=><div key={tool.id} style={{padding:'4px 0'}}><label style={{display:'flex',gap:10,alignItems:'center',minHeight:44}}>
            <input type="checkbox" aria-label={tool.id} checked={access.tools.includes(tool.id)} disabled={!access.scopes.includes(scope)} onChange={event=>{const enabled=event.target.checked;setNotice('Unsaved changes');setAccess(value=>value && ({...value,tools:enabled ? [...value.tools,tool.id] : value.tools.filter(id=>id!==tool.id)}))}} />
            <strong>{tool.id.replaceAll('_',' ')}</strong>
          </label><details style={{marginLeft:23,fontSize:12}}><summary style={{cursor:'pointer'}}>What it does</summary><p>{tool.description}</p></details></div>)}
          </div>
        </details>)}
        {editable && <button type="button" style={{marginTop:12}} onClick={()=>void save()}>{saving ? 'Saving…' : 'Save access'}</button>}
      </fieldset>
      {notice && <p role="status">{notice}</p>}
    </>}
  </Block>
}
