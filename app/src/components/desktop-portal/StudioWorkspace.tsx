import { useMemo, useState, type CSSProperties, type KeyboardEvent, type MouseEvent } from 'react'
import type { ArtistDetail } from '../../lib/api'
import { compileTemplate, type Values } from './template'
import source from './studio-workspace.html?raw'

export interface StudioWorkspaceProps {
  artist: ArtistDetail
  working: boolean
  onUpload: (files: File[]) => void
  onEditProfile?: () => void
  onUploadAvatar?: (file: File) => void
  onRemoveImage?: (url: string) => void
}
const control: CSSProperties = {display:'flex',alignItems:'center',justifyContent:'center',minHeight:28,padding:'0 14px',borderRadius:99,border:'1px solid var(--ag-panel-line)',background:'transparent',color:'var(--ink-mute)',cursor:'pointer',fontFamily:"'JetBrains Mono',monospace",fontSize:10.5,fontWeight:700,letterSpacing:'.12em',textTransform:'uppercase',textDecoration:'none'}
function safeUrl(value: string | null | undefined, mail = false): string | null {
  if (!value) return null
  try { const parsed = new URL(value,window.location.origin); return (mail?['https:','http:','mailto:']:['https:','http:']).includes(parsed.protocol)?parsed.href:null } catch { return null }
}
function ArtistImage({url,alt,avatar=false}:{url:string | null;alt:string;avatar?:boolean}) {
  const [failed,setFailed]=useState(false)
  const safe=safeUrl(url)
  const style: CSSProperties=avatar?{width:110,height:110,objectFit:'cover',borderRadius:'50%',border:'2px solid var(--gold-decor)'}:{width:'100%',height:'100%',objectFit:'cover',display:'block',borderRadius:8}
  return safe && !failed ? <img src={safe} alt={alt} style={style} onError={()=>setFailed(true)}/> : <span role="img" aria-label={alt} style={{...style,display:'flex',alignItems:'center',justifyContent:'center',background:'var(--surface)',color:'var(--ink-ghost)',fontSize:11,textAlign:'center'}}>Image unavailable</span>
}

/** The generic artist panel has a different layout contract from approved Hustl.
 * Extend the existing safe template renderer with its profile and gallery plates;
 * API writes remain parent-owned callbacks, never prototype publish or reorder. */
export default function StudioWorkspace({artist,working,onUpload,onEditProfile,onUploadAvatar,onRemoveImage}:StudioWorkspaceProps) {
  const compiled=useMemo(()=>compileTemplate(source),[])
  const [tab,setTab]=useState('page')
  const [flipped,setFlipped]=useState<Set<string>>(()=>new Set())
  const images=artist.portfolio_images ?? []
  const flip=(url:string)=>setFlipped(previous=>{const next=new Set(previous);if(next.has(url))next.delete(url);else next.add(url);return next})
  const pickFile=(event:KeyboardEvent<HTMLLabelElement>)=>{if(!working && ['Enter',' '].includes(event.key)){event.preventDefault();event.currentTarget.querySelector('input')?.click()}}
  const upload=<label tabIndex={working?-1:0} onKeyDown={pickFile} style={{...control,opacity:working?.4:1,cursor:working?'not-allowed':'pointer'}}>+ Upload work<input aria-label="Upload portfolio work" type="file" accept="image/*" multiple disabled={working} style={{display:'none'}} onChange={event=>{const files=Array.from(event.target.files ?? []);if(files.length && !working)onUpload(files);event.target.value=''}}/></label>
  const booking=safeUrl(artist.booking_url,true)
  const socials=[['Instagram',artist.instagram_url],['Facebook',artist.facebook_url],['TikTok',artist.tiktok_url],['Website',artist.website_url]].flatMap(([label,url])=>{const safe=safeUrl(url);return safe?[<a key={label} href={safe} target="_blank" rel="noreferrer" style={control}>{label} ↗</a>]:[]})
  const values: Values={
    agPanelLine:'var(--ag-panel-line)',weldRad:'0 18px 18px 0',pageName:artist.name,artistTitle:artist.title ?? artist.slug,studioBio:artist.bio ?? 'No biography provided.',
    hustlTabs:[['page','Page'],['gallery','Gallery']].map(([id,label])=>({id,label,onStr:String(tab===id),op:tab===id?1:.5,tglow:tab===id?'0 0 10px rgba(228,217,192,.75), 0 0 22px rgba(200,161,94,.5)':'none',pick:()=>setTab(id)})),
    showHustlPage:tab==='page',showHustlGallery:tab==='gallery',showUnavailable:false,unavailableTitle:'',unavailableCopy:'',
    avatar:<ArtistImage key={artist.avatar_url} url={artist.avatar_url} alt={`${artist.name} profile`} avatar/>,
    bookingLink:booking?<a style={{...control,border:'2px solid var(--gold)',color:'var(--gold)',minHeight:30}} href={booking} target="_blank" rel="noreferrer">Book Now ↗</a>:null,
    avatarUpload:onUploadAvatar?<label tabIndex={working?-1:0} onKeyDown={pickFile} style={{...control,fontSize:10,opacity:working?.4:1}}>Change photo<input type="file" aria-label="Upload profile photo" accept="image/*" disabled={working} style={{display:'none'}} onChange={event=>{const file=event.target.files?.[0];if(file&&!working)onUploadAvatar(file);event.target.value=''}}/></label>:<span style={{fontSize:10,lineHeight:1.5,color:'var(--ink-ghost)'}}>Photo editing unavailable.</span>,
    specialtyPills:(artist.specialty ?? []).map(label=>({id:label,label})),specialtyEmpty:artist.specialty?.length?'':'No specialties provided.',editTitle:onEditProfile?'Edit artist profile':'Profile editing is not connected here',editProfile:onEditProfile,editDisabled:working || !onEditProfile,editStatus:onEditProfile?'':'Profile editing is not connected here.',socialLinks:socials.length?socials:<span style={{fontSize:12,color:'var(--ink-ghost)'}}>No supported social links provided.</span>,
    pageFeedTabs:[{id:'gallery',label:'Gallery',count:images.length,line:'var(--gold)',fg:'var(--gold)',pick:()=>setTab('gallery')}],
    pagePreviewTiles:images.slice(0,8).map((url,index)=>({id:url,image:<button key={url} type="button" onClick={()=>{setTab('gallery');setFlipped(new Set([url]))}} aria-label={`Inspect portfolio image ${index+1}`} style={{aspectRatio:1,border:'1px solid var(--line)',padding:0,borderRadius:8,overflow:'hidden',background:'var(--surface)',cursor:'pointer'}}><ArtistImage url={url} alt={`Portfolio work ${index+1}`}/></button>})),
    galleryEmpty:images.length?'':'No portfolio images provided.',galleryLabel:`${images.length} ${images.length===1?"image":"images"} · tap a plate to inspect`,galleryUpload:upload,
    galleryTiles:images.map((url,index)=>{const turned=flipped.has(url);const safe=safeUrl(url);return {id:url,flipLabel:`Inspect portfolio image ${index+1}`,flipped:String(turned),flip:()=>flip(url),keydown:(event:KeyboardEvent)=>{if(event.target===event.currentTarget && ['Enter',' '].includes(event.key)){event.preventDefault();flip(url)}},rot:turned?'180deg':'0deg',frontVis:turned?'hidden':'visible',backVis:turned?'visible':'hidden',line:'var(--line)',isCover:false,frame:String(index+1).padStart(2,'0'),style:'Portfolio image',dot:'var(--ink-ghost)',image:<ArtistImage key={url} url={url} alt={`Portfolio work ${index+1}`}/>,fields:[{id:'artist',k:'Artist',v:artist.name},{id:'position',k:'Position',v:String(index+1)},{id:'style',k:'Image style',v:'Not provided'},{id:'status',k:'Publication',v:'Not provided'}],actions:[{id:'open',label:'Open',disabled:!safe,title:safe?'Open original image':'Image URL unavailable',pick:(event:MouseEvent)=>{event.stopPropagation();if(safe)window.open(safe,'_blank','noopener,noreferrer')}},{id:'remove',label:'Remove',disabled:working || !onRemoveImage,title:onRemoveImage?'Remove gallery image':'Gallery removal is not connected here',pick:(event:MouseEvent)=>{event.stopPropagation();if(onRemoveImage&&!working)onRemoveImage(url)}}]}}),
  }
  return <section data-studio-workspace={artist.id} aria-label="Artist workspace" style={{display:'flex',flexDirection:'column',height:'100%',minHeight:580,minWidth:0,flexShrink:0,overflowY:'auto'}}><style>{compiled.css}{`[data-studio-workspace] button:focus-visible,[data-studio-image]:focus-visible{outline:2px solid var(--ag-accent);outline-offset:-2px}[data-studio-workspace] span{overflow-wrap:anywhere}@media(prefers-reduced-motion:reduce){[data-studio-image] div{transition:none!important}}`}</style>{compiled.render(values)}</section>
}
