import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { ChatBubble } from '@ryozan/ui'
import { compileTemplate, type Values } from './template'
import { RailMotion } from './rail-motion.js'
import { start as startField } from './relic-field.js'
import shell from './shell.html?raw'
import sourceCSS from './source.css?raw'
import { sourceIcons, sourceAgentMarks } from './source-icons'
import { ActionDeck, type DesktopDeckAction } from './ActionDeck'

export type PortalRole = 'founder' | 'admin' | 'artist' | 'assistant' | 'collector'
export interface NativeDesktopPortalProps {
  role: PortalRole
  agentId: keyof typeof agents
  actions: DesktopDeckAction[]
  activeTab: string
  onSelectTab: (tab: string) => void
  onCloseWorkspace?: () => void
  user: { name: string; avatar?: string }
  unreadCount: number
  messages?: { id: string; from: 'me' | 'agent'; text: string; cards?: ReactNode }[]
  conversationKey?: string
  working?: boolean
  onSend?: (text: string) => void
  onUpload?: (files: File[]) => void
  children?: ReactNode
  status?: string
  reducedMotion?: boolean
  /** Tabs the page cannot fill yet (for example Club before any post exists). */
  hiddenTabs?: string[]
}
// Source-derived agents: the parent maps display identity to authorized APIs.
const agents = {
  gala: { name:'Gala', accent:'var(--gold)', rgb:'232,223,207' },
  soko: { name:'Sōkō · Timely Rain', accent:'#D9BF95', rgb:'217,191,149' },
  shishin: { name:'Shishin · Nine-Tattooed Dragon', accent:'#D94E34', rgb:'217,78,52' },
  ensei: { name:'Ensei · The Prodigy', accent:'var(--accent-coral)', rgb:'211,138,104' },
  saishin: { name:'Saishin · Little Whirlwind', accent:'#DD664E', rgb:'221,102,78' },
  taiso: { name:'Taisō · Magic Traveller', accent:'#B795E8', rgb:'183,149,232' },
  rochishin: { name:'Rochishin · Tattooed Monk', accent:'var(--gold)', rgb:'166,125,67' },
  andozen: { name:'Andōzen · Divine Physician', accent:'#7FC3B1', rgb:'127,195,177' },
  riki: { name:'Riki · Black Whirlwind', accent:'#8FB89A', rgb:'143,184,154' },
  bella: { name:'Ensei · The Prodigy', accent:'var(--accent-coral)', rgb:'211,138,104' },
  booky: { name:'Saishin · Little Whirlwind', accent:'#DD664E', rgb:'221,102,78' },
  xar: { name:'Shishin · Nine-Tattooed Dragon', accent:'var(--alt-sky)', rgb:'99,186,223' },
  mercury: { name:'Taisō · Magic Traveller', accent:'#B795E8', rgb:'183,149,232' },
  herald: { name:'Riki · Black Whirlwind', accent:'#8FB89A', rgb:'143,184,154' },
  spyder: { name:'Sōkō · Timely Rain', accent:'#D9BF95', rgb:'217,191,149' },
  docbot: { name:'Andōzen · Divine Physician', accent:'#7FC3B1', rgb:'127,195,177' },
  smoku: { name:'Rochishin · Tattooed Monk', accent:'var(--gold)', rgb:'166,125,67' },
}
const labels: Record<string,string> = { home:'Home',books:'Booking',studio:'Profile',hub:'Account',courier:'Courier',arkstation:'Club',pit:'Hub',guild:'Guild',shop:'Shop',you:'You' }
const navLabels: Record<string,string> = {home:'Home',books:'Booking',studio:'Profile',courier:'Inbox',arkstation:'Club',pit:'Hub',shop:'Shop',you:'You'}

/** Longest prompt a person can type. The API accepts more (CHAT_MESSAGE_MAX_LENGTH in gala_chat.py)
 * because Courier appends a bounded conversation excerpt to the typed prompt. */
export const CHAT_INPUT_MAX = 4000
type ChatMessage = NonNullable<NativeDesktopPortalProps['messages']>[number]
/** A turn may contain several agent updates, but remains one response bubble. */
export function chatExchanges(messages: ChatMessage[]) {
  const exchanges: { id: string; prompt?: ChatMessage; replies: ChatMessage[] }[] = []
  for (const message of messages) {
    if (message.from === 'me') exchanges.push({ id: message.id, prompt: message, replies: [] })
    else {
      if (!exchanges.length) exchanges.push({ id: message.id, replies: [] })
      exchanges[exchanges.length - 1].replies.push(message)
    }
  }
  return exchanges
}

/** Native React portal into a shadow tree. No DC scripts, eval, mock state or
 * global styles run here. The page owns all reads and business mutations. */
export function NativeDesktopPortal(props: NativeDesktopPortalProps) {
  const [root, setRoot] = useState<ShadowRoot | null>(null)
  const attach = useCallback((node: HTMLDivElement | null) => { if(node) setRoot(node.shadowRoot ?? node.attachShadow({mode:'open'})) }, [])
  const [input, setInput] = useState('')
  const [plus, setPlus] = useState(false)
  const [files, setFiles] = useState<File[]>([])
  // Clear the composer when the agent or conversation changes (reset during render, not in an effect).
  const composerKey = `${props.agentId}|${props.conversationKey}`
  const [composerFor, setComposerFor] = useState(composerKey)
  if (composerFor !== composerKey) { setComposerFor(composerKey); setInput(''); setFiles([]) }
  const [width, setWidth] = useState(340)
  const [rail, setRail] = useState<Values>({})
  const [workspaceAction, setWorkspaceAction] = useState<{tab:string;id:string} | null>(null)
  const activeAction = workspaceAction?.tab === props.activeTab ? props.actions.find(action => action.id === workspaceAction.id) : undefined
  const [hover, setHover] = useState(false)
  const [dividerY, setDividerY] = useState<number | null>(null)
  const [resizing, setResizing] = useState(false)
  const [viewport, setViewport] = useState(window.innerWidth)
  const [viewportHeight, setViewportHeight] = useState(window.innerHeight)
  const portrait = viewport >= 768 && viewport <= 1366 && viewportHeight > viewport
  const zoom = !portrait && viewport >= 768 && viewport <= 1366 ? viewport / 1366 : 1
  const compositionHeight = '100%'
  const motion = useRef<RailMotion | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const [canvas, setCanvas] = useState<HTMLCanvasElement | null>(null)
  const pageRef = useRef<HTMLDivElement>(null)
  const dividerRef = useRef<HTMLDivElement>(null)
  const resizeCleanup = useRef<(() => void) | null>(null)
  const template = useMemo(() => compileTemplate(shell), [])
  useEffect(() => {
    // Observe proximity without expanding the drag target over nearby controls.
    let timer: ReturnType<typeof setTimeout> | undefined
    let latestY: number | null = null
    let armed = false
    const cancel = () => {
      clearTimeout(timer)
      timer = undefined
      latestY = null
      armed = false
      if (!resizing) setDividerY(null)
    }
    const move = (event: MouseEvent) => {
      if (resizing || !dividerRef.current) return
      const rect = dividerRef.current.getBoundingClientRect()
      const near = event.clientX >= rect.left - 50 && event.clientX <= rect.right + 50 && event.clientY >= rect.top && event.clientY <= rect.bottom
      if (!near) { cancel(); return }
      latestY = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))
      if (armed) setDividerY(latestY)
      else if (timer === undefined) timer = setTimeout(() => {
        timer = undefined
        armed = true
        setDividerY(latestY)
      }, 500)
    }
    const leave = cancel
    window.addEventListener('mousemove', move)
    document.documentElement.addEventListener('mouseleave', leave)
    window.addEventListener('blur', leave)
    return () => { clearTimeout(timer); window.removeEventListener('mousemove', move); document.documentElement.removeEventListener('mouseleave', leave); window.removeEventListener('blur', leave) }
  }, [resizing, props.activeTab])
  const navIcons = useMemo(() => Object.fromEntries(Object.entries(sourceIcons).map(([id,paths])=>[id,compileTemplate(`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`).render({})])), [])
  useEffect(() => {
    const resize = () => { setViewport(window.innerWidth); setViewportHeight(window.innerHeight) }
    window.addEventListener('resize', resize)
    return () => { window.removeEventListener('resize', resize); resizeCleanup.current?.() }
  }, [])
  useEffect(() => {
    // Chromium does not consistently load @font-face declared in shadow CSS.
    const face = new FontFace('KC Tallboy', 'url(/desktop-portal/fonts/KCTallboy.otf)')
    let active = true
    void face.load().then(loaded => { if(active) document.fonts.add(loaded) }).catch(() => undefined)
    return () => { active=false; document.fonts.delete(face) }
  }, [])
  useEffect(() => {
    if (!root) return
    const controller = new RailMotion(root, update => setRail(previous => ({ ...previous, ...update })), !!props.reducedMotion)
    motion.current = controller
    const frame = requestAnimationFrame(() => controller.railIntro())
    return () => { cancelAnimationFrame(frame); controller.dispose(); motion.current = null }
  }, [root, props.reducedMotion])
  useEffect(() => {
    // Template edits can replace the canvas without replacing the shadow root.
    // Start and stop the field with its real DOM owner, including live preview.
    if (canvas && pageRef.current) return startField(canvas, pageRef.current, props.reducedMotion)
  }, [canvas, props.reducedMotion])
  const select = (tab: string) => { setWorkspaceAction(null);setInput('');setPlus(false);props.onSelectTab(tab) }
  const collector = props.role === 'collector'
  const staffTabs = ['home','books','studio','courier','arkstation','pit']
  const tabs = (collector ? ['home','arkstation','shop','courier','you'] : staffTabs.filter(tab => tab !== 'pit' || props.role === 'founder'))
    .filter(tab => tab === props.activeTab || !props.hiddenTabs?.includes(tab))
  const iconKeys: Record<string,string> = {home:'house',books:'books',studio:'studio',courier:'courier',arkstation:collector?'toolkit':'guild',pit:'pit',shop:'bag',you:'person'}
  const glyph = useMemo(() => props.agentId === 'herald'
    ? <span data-agent-emblem="herald" aria-hidden="true" style={{display:'inline-block',width:'1em',height:'1em',background:'currentColor',mask:'url(/assets/herald-horn.png) center / contain no-repeat',WebkitMask:'url(/assets/herald-horn.png) center / contain no-repeat'}} />
    : compileTemplate(`<svg data-agent-emblem="${props.agentId}" aria-hidden="true" width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="display:inline-block;vertical-align:middle">${sourceAgentMarks[props.agentId]}</svg>`).render({}), [props.agentId])
  const agent = { ...agents[props.agentId], glyph }
  const rgba = (opacity: number) => `rgba(${agent.rgb},${opacity})`
  const send = props.onSend && !props.working && input.trim() ? () => { props.onSend?.(input.trim()); setInput('') } : undefined
  const exchanges = chatExchanges(props.messages ?? [])
  const exchange = exchanges.at(-1)
  const resizeStart = (event: React.MouseEvent) => {
    if (event.button !== 0 || dividerY === null) return
    event.preventDefault()
    resizeCleanup.current?.()
    setResizing(true)
    const x = event.clientX
    const move = (e: MouseEvent) => {
      setWidth(Math.max(300, Math.min(420, width + (e.clientX-x)/zoom)))
      const rect = dividerRef.current?.getBoundingClientRect()
      if (rect) setDividerY(Math.max(0, Math.min(1, (e.clientY-rect.top)/rect.height)))
    }
    const up = () => { window.removeEventListener('mousemove',move); window.removeEventListener('mouseup',up); setResizing(false); setDividerY(null) }
    resizeCleanup.current = up
    window.addEventListener('mousemove',move); window.addEventListener('mouseup',up)
  }
  const values: Values = {
    nativeThread: <section data-chat-exchange aria-label={`${agent.name} conversation`}>
      <div data-chat-pair key={exchange?.id ?? 'empty'}>
        {exchange?.prompt && <ChatBubble from="user" role="region" aria-label="Your prompt" tabIndex={0}><strong>You</strong><span>{exchange.prompt.text}</span>{exchange.prompt.cards}</ChatBubble>}
        <ChatBubble from="agent" accent={agent.accent} role="region" aria-label={`${agent.name} response`} tabIndex={0}>
          <strong>{agent.name}</strong>
          {exchange?.replies.map(reply => <span key={reply.id}>{reply.text}{reply.cards}</span>)}
          {!exchange && <span>Ask a question to start. Each exchange stays together here.</span>}
          {exchange && !exchange.replies.length && <span>{props.working ? 'Working on your response…' : 'No response received for this prompt.'}</span>}
          {props.working && !!exchange?.replies.length && <span role="status">Updating response…</span>}
        </ChatBubble>
      </div>
    </section>,
    pageRef,fieldRef:setCanvas,dividerRef,compositionHeight,
    // Keep annotated dimensions in visible pixels at the tablet scale.
    mainHeaderHeight: `${100 / zoom}px`,
    mainHeaderContentOffset: `translateY(${10 / zoom}px)`,
    nativeDock: <footer data-desktop-dock-region style={{position:'relative',flex:'0 0 auto',height:`calc(120px + env(safe-area-inset-bottom,0px) / ${zoom})`,paddingBottom:`calc(env(safe-area-inset-bottom,0px) / ${zoom})`,boxSizing:'border-box'}}>
      <div style={{position:'relative',height:'100%'}}><ActionDeck key={props.activeTab} actions={props.actions} glyph={agent.glyph} working={!!props.working} onActivate={action=>{if(action.component || !action.run)setWorkspaceAction({tab:props.activeTab,id:action.id});else{setWorkspaceAction(null);action.run()}}} /></div>
      {props.status && <span role="status" style={{position:'absolute',bottom:`calc(env(safe-area-inset-bottom,0px) / ${zoom})`,right:18,color:'var(--ink-ghost)',fontSize:12}}>{props.status}</span>}
    </footer>,
    nativeBody: activeAction ? <section data-action-workspace={activeAction.id} style={{height:'100%',minHeight:0,overflow:'auto',padding:20}}><h2>{activeAction.title}</h2>{activeAction.component ?? <p role="status">{activeAction.description}</p>}</section> : props.children,
    yardOpenNum:0, yardShelvedNum:1, mobileNum:0, isMobile:false,
    railW:viewport < 1180 ? '86px':'108px',railDisplay:'flex',railGap:viewport < 1180?'10px':'14px',bannerW:viewport < 1180?'78px':'87px',bannerH:`calc(${compositionHeight} - 52px)`,
    tigerTop:`${(hover?10:15)+20/zoom}px`,tigerT:rail.tigerT ?? 'translateX(-50%)',bannerT:rail.bannerT ?? 'translate(0px,-15px)',bannerClass:rail.railSway && !rail.railHidden?'bt-rail-sway':'',tigerFx:'drop-shadow(0 2px 6px rgba(0,0,0,.55))',
    tigerTip:rail.railHidden?'Unfurl the banner':'Furl the banner',railToggle:()=>motion.current?.railToggle(),tigerEnter:()=>setHover(true),tigerLeave:()=>setHover(false),
    title:props.activeTab==='home'?`${new Date().getHours()<12?'Morning':new Date().getHours()<17?'Afternoon':'Evening'}, ${props.user.name.split(' ')[0]}`:labels[props.activeTab] ?? 'Home',titleSize:viewport<1180?'clamp(22px,3.6vw,36px)':'clamp(26px,3.4vw,50px)',padMain:'18px',hdrBtn:'38px',unread:props.unreadCount,
    avatarRing:'var(--gold-decor)',avatarInitials:props.user.avatar?'':props.user.name.split(/\s+/).filter(Boolean).slice(0,2).map(part=>part[0]!.toUpperCase()).join(''),avatarBg:props.user.avatar?`url("${props.user.avatar.replaceAll('"','%22')}")`:'none',toggleSettings:()=>select(collector?'you':'hub'),goCourier:()=>select('courier'),
    tabs:tabs.map(id => ({id,label:navLabels[id],pick:()=>select(id),fg:id===props.activeTab?'var(--accent-cream)':'rgba(228,217,192,.6)',glow:id===props.activeTab?'drop-shadow(0 0 7px rgba(232,223,207,.65)) drop-shadow(0 0 16px rgba(232,223,207,.3))':'none',tglow:id===props.activeTab?'0 0 10px rgba(228,217,192,.75), 0 0 22px rgba(232,223,207,.45)':'none',icon:navIcons[iconKeys[id]]})),
    vpFlow:'row',vpGap:'0px',vpPadTop:'18px',stuChatDock:true,weldRad:'0 18px 18px 0',
    agAccent:agent.accent,agSweep:rgba(.95),agBloom:`radial-gradient(128% 34% at 50% -2%,${rgba(.10)},${rgba(.035)} 46%,rgba(0,0,0,0) 74%)`,agPanelMute:rgba(.25),agPanelLine:rgba(.34),agHeadGlow:`inset 0 1px 0 ${rgba(.18)}`,agHeadWash:'#0a0c0a',
    stuCurName:agent.name,stuCurGlyph:agent.glyph,stuCurAccent:agent.accent,stuHeadGlow:'#0a0c0a',stuHeadWash:'#0a0c0a',stuChatW:`min(${width}px, 45%)`,
    stuRailCursor:resizing||dividerY!==null?'col-resize':'default',stuRailOn:dividerY!==null||resizing?1:0,
    stuRailBg:`linear-gradient(180deg, transparent 0%, ${rgba(.28)} 14%, ${rgba(.28)} 86%, transparent 100%)`,
    stuRailGlow:`drop-shadow(0 0 ${resizing?12:7}px ${rgba(.8)})`,stuRailTrans:dividerY!==null||resizing?'opacity .16s ease':'opacity .85s ease .4s',stuResizeStart:resizeStart,
    stuComposerPh:`Message ${agent.name}`,stuInput:input,stuInputChange:(e:React.ChangeEvent<HTMLTextAreaElement>)=>setInput(e.target.value.slice(0,CHAT_INPUT_MAX)),stuCount:input.length>=CHAT_INPUT_MAX*0.9?`${input.length} / ${CHAT_INPUT_MAX}`:'',stuInputKey:(e:React.KeyboardEvent<HTMLTextAreaElement>)=>{if(e.key==='Enter' && !e.shiftKey && !e.nativeEvent.isComposing){e.preventDefault();send?.()}},stuSend:send,stuSendBg:agent.accent,stuSendGlow:`0 0 16px ${rgba(.35)}`,stuPlusOpen:plus&&!!props.onUpload,stuPlusOpenStr:String(plus),stuPlusToggle:()=>props.onUpload&&setPlus(!plus),stuPlusFg:'var(--ink)',stuPlusGlow:'none',
    stuUploadPick:props.onUpload?()=>fileInput.current?.click():undefined,stuInboxHas:files.length>0,stuInboxN:files.length,stuInboxView:files.map((file,index)=>({id:index,name:file.name,size:`${Math.ceil(file.size/1024)} KB`,glyph:'↑',remove:()=>setFiles(current=>current.filter((_,i)=>i!==index))})),stuInboxSend:props.onUpload?()=>{props.onUpload?.(files);setFiles([]);setPlus(false)}:undefined,
    // The source chip strip represents spawned artifacts, never the base page.
    stuHasChips:false,stuChips:[],hasActiveAction:!!activeAction || !!props.onCloseWorkspace,podFxClose:()=>{if(activeAction)setWorkspaceAction(null);else props.onCloseWorkspace?.()},
  }
  // #root alone owns viewport units. Scaling belongs to the inner composition,
  // never the page boundary; safe-area padding is inside the painted host.
  return <div ref={attach} data-native-desktop-portal="true" style={{position:'relative',height:'100%',minHeight:0,width:'100%',overflow:'hidden'}}>{root && createPortal(<>
    <style>{sourceCSS}{template.css}{`:host{display:block;--gala-clear:18px;--ag-accent:${agent.accent};--ag-panel-line:${rgba(.34)};--ag-panel-mute:${rgba(.25)}}button:focus-visible,[role=button]:focus-visible{outline:2px solid var(--gold);outline-offset:3px}button:disabled{opacity:.4;cursor:not-allowed}${props.onUpload?'':'button[aria-label="Attach or pick talents"]{display:none}'}`}</style>
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Poppins:wght@600;700;800;900&family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap"/>
    <input ref={fileInput} type="file" multiple hidden onChange={e=>setFiles(Array.from(e.target.files ?? []))}/>
    <style>{`:host{zoom:1;box-sizing:border-box;background:var(--ground);padding:env(safe-area-inset-top,0px) env(safe-area-inset-right,0px) 0 env(safe-area-inset-left,0px)}[data-yard]{--gala-clear:18px}.bt-banner-nav,.bt-wordmark{translate:0 ${20/zoom}px}`}</style>
    <div data-desktop-composition data-tablet-portrait={portrait || undefined} style={{position:'relative',display:'flex',flexDirection:'column',minHeight:0,height:'100%',width:'100%',zoom}}>
      {template.render(values)}
    </div>
  </>,root)}</div>
}
export default NativeDesktopPortal
