import { useMemo, useState, type ReactNode } from 'react'
import type { Booking } from '../../lib/api'
import { formatCustomerPrice } from '../../lib/format'
import { compileTemplate, type Values } from './template'
import source from './booky-workspace.html?raw'
import Lightbox from '../Lightbox'
import { useLightbox } from '../../hooks/useLightbox'
import '../../styles/lightbox.css'

export interface BookyWorkspaceProps {
  bookings: Booking[]
  total: number
  collector: boolean
  selectedId: string | null
  onSelect: (id: string) => void
  onOpenThread: (id: string) => void
}

function display(value: string | null | undefined): string {
  return value?.trim() || 'Not provided'
}
/** API enums such as tattoo_session read as words: "Tattoo session". */
function enumLabel(value: string | null | undefined): string {
  const text = display(value).replaceAll('_', ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}
function money(value: number | null): string {
  return value === null || !Number.isFinite(value) ? 'Not provided' : new Intl.NumberFormat('en-US', { style:'currency', currency:'USD', maximumFractionDigits:2 }).format(value)
}
function safeReference(value: string): string | null {
  try {
    const url = new URL(value, window.location.origin)
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null
  } catch { return null }
}
function ReferenceImage({ url }: { url: string }) {
  const [failed, setFailed] = useState(false)
  const viewer = useLightbox(1)
  const safe = safeReference(url)
  if (!safe) return <span>Reference URL unavailable.</span>
  return <>
    <button type="button" aria-label="Preview booking reference" onClick={()=>viewer.openAt(0)} style={{background:'transparent',color:'inherit',border:0,cursor:'zoom-in',flex:'1 1 0',minWidth:150,borderRadius:10,display:'flex',alignItems:'center',justifyContent:'center',padding:4,boxSizing:'border-box'}}>
      {failed ? null : <img src={safe} alt="Booking reference" referrerPolicy="no-referrer" onError={()=>setFailed(true)} style={{maxWidth:'100%',maxHeight:200,objectFit:'contain'}}/>}
    </button>
    {viewer.open && <Lightbox images={[{src:safe,alt:'Booking reference',referrerPolicy:'no-referrer'}]} currentIndex={0} onClose={viewer.close} onNext={viewer.next} onPrev={viewer.prev}/>}
  </>
}

/** Extends the existing safe DC renderer with the approved Huddl grid, detail
 * and pass. Unlike the previous generic cards, markup remains source-derived;
 * only display data and local/read-only callbacks replace prototype behavior. */
export default function BookyWorkspace({ bookings, total, collector, selectedId, onSelect, onOpenThread }: BookyWorkspaceProps) {
  const compiled = useMemo(()=>compileTemplate(source),[])
  const [ticketId, setTicketId] = useState<string | null>(null)
  const selected = bookings.find(booking=>booking.id===selectedId) ?? bookings[0] ?? null
  const showTicket = !!selected && ticketId === selected.id
  if (!selected) return <section data-booky-workspace="empty" aria-label="Bookings" style={{minHeight:320,display:'flex',flexDirection:'column',gap:12,padding:'14px 18px',color:'var(--ink-ghost)'}}><span style={{fontFamily:"'Poppins',sans-serif",fontSize:17,fontWeight:800,textTransform:'uppercase',color:'var(--ink)'}}>Bookings</span><p>No bookings loaded.</p><span>{bookings.length} loaded of {total}</span></section>
  const name = selected.resolved_name ?? selected.client_name ?? selected.guest_name ?? 'Name not provided'
  const scheduled = selected.scheduled_date
  const rawDate = scheduled ?? selected.preferred_date
  // Date-only fields are calendar dates, not UTC instants. Noon avoids a local
  // offset shifting a booking onto the previous day when formatting the pass.
  const date = rawDate && /^\d{4}-\d{2}-\d{2}/.test(rawDate) ? new Date(`${rawDate.slice(0,10)}T12:00:00`) : null
  const validDate = date && !Number.isNaN(date.getTime()) ? date : null
  const start = selected.scheduled_time
  let end = 'Not provided'
  if (start && /^\d{2}:\d{2}/.test(start) && selected.duration_minutes !== null && selected.duration_minutes > 0) {
    const [hour,minute] = start.split(':').map(Number)
    const minutes = hour*60+minute+selected.duration_minutes
    end = `${String(Math.floor(minutes/60)%24).padStart(2,'0')}:${String(minutes%60).padStart(2,'0')}${minutes>=1440?' (+1 day)':''}`
  }
  const references: ReactNode = selected.reference_urls?.length ? selected.reference_urls.map(url=><ReferenceImage key={url} url={url}/>) : <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:12.5,color:'var(--ink-ghost)'}}>No reference images attached.</span>
  const hasThread = !!selected.conversation_id
  const amount = (value: number | null) => value == null || !Number.isFinite(value) ? 'Not provided' : collector ? formatCustomerPrice(value) : money(value)
  const values: Values = {
    selectedId:selected.id,
    agPanelLine:'var(--ag-panel-line)',agPanelMute:'var(--ag-panel-mute)',stuHeadWash:'#0a0c0a',weldBL:'0',weldRad:'0 18px 18px 0',
    gridSplitQ:'minmax(0,1fr) minmax(0,1.5fr)',gridRowsQ:'minmax(0,1fr)',gridOverQ:'hidden',
    booksTabs:[{id:'bookings',label:'Bookings',onStr:'true',op:1,tglow:'none',pick:()=>setTicketId(null)}],
    isCollector:false,isStaff:false,annoOpen:false,hudShowInq:!showTicket,hudShowTicket:showTicket,hudIsAppt:true,hudIsInq:hasThread,
    hudOpenTicket:()=>setTicketId(selected.id),hudBackCard:()=>setTicketId(null),
    inqKind:enumLabel(selected.booking_kind),inqRef:selected.reference_code ?? selected.id,inqStatusTag:enumLabel(selected.status),
    inqTitleMain:name,inqTitleSub:'',inqTime:start ? start.slice(0,5) : selected.preferred_time ? `${selected.preferred_time} (preferred)` : 'Not scheduled',
    inqDay:validDate?String(validDate.getDate()):'TBD',inqMon:validDate?`${validDate.toLocaleDateString('en-US',{month:'short'})}${scheduled?'':' · preferred'}`:'No date',
    inqDesc:display(selected.description),inqType:enumLabel(selected.booking_kind),inqPlacement:display(selected.placement),inqSize:display(selected.size_estimate),inqBudget:display(selected.budget),
    referenceImages:references,
    inquiryFlex:selected.reference_urls?.length ? '1' : '0 1 auto',
    referenceFlex:selected.reference_urls?.length ? '1 0 auto' : '0 0 auto',
    referenceRowFlex:selected.reference_urls?.length ? '1' : '0 0 auto',
    referenceMinHeight:selected.reference_urls?.length ? '200px' : '0px',
    inqActions:hasThread?[{label:'Open Courier thread',act:()=>onOpenThread(selected.conversation_id!),line:'1px solid var(--ag-panel-line)',bg:'transparent',fg:'var(--ink)',glow:'none'}]:[],
    hudInqAny:true,loadedLabel:`${bookings.length} loaded of ${total}`,additionalLabel:'',hudAppt:[],
    hudInq:bookings.map(booking=>({id:booking.id,selected:String(booking.id===selected.id),client:booking.resolved_name ?? booking.client_name ?? booking.guest_name ?? 'Name not provided',piece:booking.reference_code ?? booking.id,when:booking.scheduled_date?.slice(0,10) ?? 'Not scheduled',status:display(booking.status),bar:'var(--ag-accent)',stFg:'var(--ink-mute)',rowBg:booking.id===selected.id?'var(--surface)':'transparent',pick:()=>{setTicketId(null);onSelect(booking.id)}})),
    sessionReference:selected.reference_urls?.[0]?<ReferenceImage key={selected.reference_urls[0]} url={selected.reference_urls[0]}/>:<span style={{fontSize:12.5,color:'var(--ink-ghost)'}}>No reference attached.</span>,tkPass:name,tkStyle:display(selected.style_preference),tkStart:start?.slice(0,5) ?? 'Not scheduled',tkEnd:end,
    tkArtist:display(selected.artist_name),tkChair:'?',tkShort:display(selected.style_preference),tkAmt:amount(selected.estimated_total),tkAmtSize:selected.estimated_total == null ? '24px':'56px',
    tkDep:`${amount(selected.deposit_amount)} · ${selected.deposit_paid?'paid':'not paid'}`,tkProgress:'0 145',
    tkDateCode:scheduled?.slice(0,10) ?? 'Not scheduled',tkHrs:selected.duration_minutes===null?'?':String(Math.round(selected.duration_minutes/60*100)/100),
    tkSerial:selected.reference_code ?? selected.id,tkStatus:display(selected.status),locationLabel:'Location not provided',
  }
  return <section data-booky-workspace="ready" aria-label="Bookings workspace" style={{display:'flex',flexDirection:'column',minHeight:600,height:'100%',minWidth:0}}>
    <style>{compiled.css}{`[data-booky-workspace] [data-booking-id]{border:0;text-align:left;width:100%;min-width:0}[data-booky-workspace] [data-booking-id] span{overflow-wrap:anywhere}[data-booky-workspace] button:focus-visible{outline:2px solid var(--ag-accent);outline-offset:-2px}`}</style>
    {compiled.render(values)}
  </section>
}
