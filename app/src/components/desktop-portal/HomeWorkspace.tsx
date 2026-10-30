import { useMemo, useState } from 'react'
import type { Booking, Conversation, User } from '../../lib/api'
import { formatCustomerPrice } from '../../lib/format'
import { compileTemplate, type Values } from './template'
import source from './home-workspace.html?raw'

export interface HomeWorkspaceProps {
  user: User
  bookings: Booking[]
  inbox: Conversation[]
  onSelectTab: (tab: string) => void
  onOpenThread?: (id: string) => void
  onOpenBooking?: (id: string) => void
}

const text = (value: string | null | undefined) => value?.trim() || 'Not provided'
const dateKey = (date: Date) => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`
const money = (amount: number | null | undefined) => amount == null || !Number.isFinite(amount) ? 'Not provided' : new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(amount)
function bookingDate(booking: Booking | undefined): Date | null {
  const value = booking?.scheduled_date?.slice(0,10)
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null
  const date = new Date(`${value}T12:00:00`)
  return Number.isNaN(date.getTime()) ? null : date
}

/** Layout repairs for the source Home, applied without editing the DC template.
 *
 * 1. The staff inbox + side quests pane is a grid with auto rows inside an
 *    `overflow:hidden` box, so every conversation made the rows ~2,000px tall and
 *    the list was clipped with no way to scroll past the first few. Pin the row to
 *    the pane's height and let the inbox list scroll inside itself.
 * 2. Tablet portrait (the approved reflow: workspace above, chat below) gives the
 *    workspace a short fixed band. The template's `height:100%` rows resolve against
 *    a parent sized only by `min-height`, collapse to ~48px and clip the calendar
 *    and inbox. In portrait the Home flows at its natural height and the workspace
 *    band scrolls as one surface; the inbox pane gets a fixed, scrollable height. */
// Hooks are the template's own `data-home-conversation` rows (inline style text is
// re-serialised by the browser, so it cannot be matched reliably).
const INBOX_LIST = '[data-home-workspace="staff"] div:has(> [data-home-conversation])'
const INBOX_PANE = '[data-home-workspace="staff"] div:has(> div > div > [data-home-conversation])'
const HOME = '[data-tablet-portrait] [data-home-workspace="staff"] > [data-screen-label="Home"]'
const HOME_LAYOUT_CSS = `
${INBOX_PANE}{grid-template-rows:minmax(0,1fr)}
${INBOX_PANE} > div{min-height:0}
${INBOX_LIST}{overflow-y:auto;overscroll-behavior:contain}
[data-tablet-portrait] [data-home-workspace="staff"]{height:auto!important;min-height:0!important;overflow:visible!important}
${HOME}{flex:0 0 auto!important;overflow:visible!important}
${HOME} > div{height:auto!important;flex:0 0 auto!important;overflow:visible!important}
${HOME} > div > div{height:auto!important;overflow:visible!important}
${HOME} > div > div > div{height:auto!important;flex:0 0 auto!important;overflow:visible!important}
[data-tablet-portrait] ${INBOX_PANE}{flex:0 0 auto!important;height:24rem!important;top:0!important}
`

/** The former generic user card cannot represent the approved role-specific Home.
 * Reuse the safe DC renderer and source Home calendar/session/inbox hierarchy;
 * only local selection and existing navigation replace prototype callbacks. */
export default function HomeWorkspace({user,bookings,inbox,onSelectTab,onOpenThread,onOpenBooking}: HomeWorkspaceProps) {
  const compiled = useMemo(()=>compileTemplate(source),[])
  const [selectedDay,setSelectedDay] = useState(()=>dateKey(new Date()))
  const [calendarMonth,setCalendarMonth] = useState(()=>new Date(new Date().getFullYear(),new Date().getMonth(),1,12))
  const [view,setView] = useState<'month'|'week'|'day'>('month')
  const [sessionIndex,setSessionIndex] = useState(0)
  const collector = user.role === 'collector'
  const price = (amount: number | null) => collector && amount !== null && Number.isFinite(amount) ? formatCustomerPrice(amount) : money(amount)
  const dayBookings = bookings.filter(booking=>booking.scheduled_date?.slice(0,10)===selectedDay)
  const session = collector ? bookings.filter(booking=>!!bookingDate(booking)).sort((a,b)=>(a.scheduled_date ?? '').localeCompare(b.scheduled_date ?? '')).find(booking=>(booking.scheduled_date ?? '').slice(0,10)>=dateKey(new Date())) ?? bookings[0] : dayBookings[sessionIndex % Math.max(1,dayBookings.length)]
  const sessionDate = bookingDate(session)
  const deposit = session ? `${price(session.deposit_amount)} · ${session.deposit_paid?'paid':'not paid'}` : 'Not provided'
  const estimate = session ? `${price(session.estimated_total)} estimated total` : 'Not provided'
  const openBooking = () => session && onOpenBooking ? onOpenBooking(session.id) : onSelectTab('books')
  const openSessionThread = () => session?.conversation_id && onOpenThread ? onOpenThread(session.conversation_id) : onSelectTab('courier')
  const selectDay = (day: Date) => {setSelectedDay(dateKey(day));setSessionIndex(0)}
  const cell = (day: Date) => {
    const key = dateKey(day)
    return {id:key,selected:String(key===selectedDay),num:day.getDate(),pick:()=>selectDay(day),bg:key===selectedDay?'var(--accent-cream)':'transparent',line:key===selectedDay?'var(--accent-cream)':'var(--line)',fg:key===selectedDay?'var(--ground)':'var(--ink)',op:day.getMonth()===calendarMonth.getMonth()?1:.4,glow:'none',dots:bookings.some(booking=>booking.scheduled_date?.slice(0,10)===key)?[{color:'var(--accent-coral)'}]:[]}
  }
  const first = new Date(calendarMonth.getFullYear(),calendarMonth.getMonth(),1,12)
  first.setDate(first.getDate()-first.getDay())
  const cells = Array.from({length:42},(_,index)=>cell(new Date(first.getFullYear(),first.getMonth(),first.getDate()+index,12)))
  const weekStart = new Date(`${selectedDay}T12:00:00`)
  weekStart.setDate(weekStart.getDate()-weekStart.getDay())
  const rows = inbox.map(conversation=>({id:conversation.id,who:conversation.name ?? conversation.last_message?.sender_name ?? 'Conversation',txt:conversation.last_message?.content ?? 'No messages loaded',what:conversation.last_message?.content ?? 'No messages loaded',t:conversation.last_message?.created_at?.slice(0,10) ?? '',when:conversation.last_message?.created_at?.slice(0,10) ?? '',ch:conversation.channel_context?.channel ?? 'Portal',dot:conversation.is_unread?'var(--gold)':'transparent',fw:conversation.is_unread?700:600,pick:()=>onOpenThread ? onOpenThread(conversation.id) : onSelectTab('courier')}))
  const values: Values = {
    isCollector:collector,isStaff:!collector,agPanelLine:'var(--ag-panel-line)',weldRad:'0 18px 18px 0',stuEmpty:true,stuCtx:[],podColOver:'auto',podCal:true,podPaneFlex:'1',podPaneMinH:'250px',
    loadedBookingsLabel:`${bookings.length} ${bookings.length===1?"booking":"bookings"}`,loadedInboxCount:inbox.length,loadedInboxLabel:`${inbox.length} ${inbox.length===1?"conversation":"conversations"}`,emptyInboxLabel:inbox.length?'':'No conversations loaded.',
    sessionMonth:sessionDate?.toLocaleDateString('en-US',{month:'short'}) ?? 'No date',sessionDay:sessionDate?.getDate() ?? 'TBD',sessionWeekday:sessionDate?.toLocaleDateString('en-US',{weekday:'long'}) ?? '',sessionTime:session?.scheduled_time?.slice(0,5) ?? 'Not scheduled',sessionStatus:session?text(session.status):'No booking loaded',sessionStyle:session?text(session.style_preference):'Sessions',sessionDescription:session?text(session.description):'No booking loaded',sessionArtist:text(session?.artist_name),sessionWhen:sessionDate?`${sessionDate.toLocaleDateString('en-US')} · ${session?.scheduled_time?.slice(0,5) ?? 'Time not provided'}`:'Not scheduled',sessionPlacement:session?`${text(session.placement)} · ${session.reference_code ?? session.id}`:'Not provided',sessionDeposit:deposit,sessionEstimate:estimate,
    cphOpenPass:openBooking,cphOpenCourier:()=>onSelectTab('courier'),openSessionThread,cphInbox:rows,cphUnread:`${inbox.reduce((sum,item)=>sum+item.unread_count,0)} unread in loaded set`,
    monthLabel:calendarMonth.toLocaleDateString('en-US',{month:'short',year:'numeric'}),previousMonth:()=>setCalendarMonth(value=>new Date(value.getFullYear(),value.getMonth()-1,1,12)),nextMonth:()=>setCalendarMonth(value=>new Date(value.getFullYear(),value.getMonth()+1,1,12)),
    calSlideX:`${['month','week','day'].indexOf(view)*54}px`,calViews:(['month','week','day'] as const).map(id=>({id,label:id,fg:view===id?'var(--ground)':'var(--ink-mute)',hfg:'var(--gold)',pick:()=>setView(id)})),calMonth:view==='month',calWeek:view==='week',calDay:view==='day',calCells:cells,calWeekCells:Array.from({length:7},(_,index)=>cell(new Date(weekStart.getFullYear(),weekStart.getMonth(),weekStart.getDate()+index,12))),dowLabels:['S','M','T','W','T','F','S'].map((ch,id)=>({id,ch})),selDayMeta:`${selectedDay} · ${dayBookings.length} loaded sessions`,
    agenda:dayBookings.map(booking=>({id:booking.id,dot:'var(--accent-coral)',time:booking.scheduled_time?.slice(0,5) ?? 'Time not provided',title:booking.resolved_name ?? booking.client_name ?? booking.guest_name ?? 'Name not provided',meta:`${booking.status} · ${booking.description}`})),
    nfAny:!!session,nfNone:!session,nfMulti:dayBookings.length>1,nfCycle:()=>setSessionIndex(value=>value+1),nfCount:dayBookings.length,nfTitleMain:session?.resolved_name ?? session?.client_name ?? session?.guest_name ?? 'Name not provided',nfTitleSub:session?`${text(session.style_preference)} · ${session.status}`:'',nfWhen:session?`${session.scheduled_date?.slice(0,10) ?? 'Not scheduled'} · ${session.scheduled_time?.slice(0,5) ?? 'Time not provided'}`:'Not scheduled',nfPlace:text(session?.placement),nfPaid:deposit,nfDue:estimate,nfOpenPass:openBooking,nextMsg:openSessionThread,nfMsgLabel:'Open Courier',nfDay:selectedDay,nbxRows:rows,sqReset:'Unavailable',schedQuests:[],
  }
  return <section data-home-workspace={collector?'collector':'staff'} aria-label="Home workspace" style={{display:'flex',flexDirection:'column',height:'100%',minHeight:640,flexShrink:0,minWidth:0,overflowY:'auto'}}><style>{compiled.css}{`[data-home-workspace] button:focus-visible{outline:2px solid var(--ag-accent);outline-offset:-2px}[data-home-workspace] span{overflow-wrap:anywhere}`}{HOME_LAYOUT_CSS}</style>{compiled.render(values)}</section>
}
