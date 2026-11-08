/**
 * Consumer-only type boundary for the read-only @ryozan/ui source package.
 * The supplied package currently exposes TSX whose DOM prop names conflict with React 19;
 * Vite still resolves and renders the package itself, while this file keeps app typechecking local.
 */
import type * as React from 'react'

export interface SessionPassRow {
  label: React.ReactNode
  value: React.ReactNode
  valueColor?: string
}

export interface AvatarProps extends React.HTMLAttributes<HTMLSpanElement> {
  src?: string
  initial?: string
  diameter?: number
  ringColor?: string
  dashed?: boolean
}

export interface IconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  label: string
  badge?: number
  shape?: 'card' | 'pill'
}

export interface PageTitleProps extends React.HTMLAttributes<HTMLElement> {
  meta?: React.ReactNode
  action?: React.ReactNode
  as?: 'h1' | 'h2' | 'div'
  compact?: boolean
}

export interface SectionLabelProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: 'muted' | 'gold' | 'ghost' | 'ink'
  scale?: 'sm' | 'md' | 'lg'
}

export interface SessionPassProps {
  eyebrow: React.ReactNode
  titleTop: React.ReactNode
  titleAccent: React.ReactNode
  rows: SessionPassRow[]
  status?: React.ReactNode
  ctaLabel?: React.ReactNode
  onCta?: () => void
  secondaryLabel?: React.ReactNode
  onSecondary?: () => void
  railVariant?: 'stub' | 'date'
  date?: { month: string; day: string; weekday: string; note?: React.ReactNode }
  /** Letters set down the vertical stub. */
  brand?: string
  style?: React.CSSProperties
}

export interface QuickTileProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'value'> {
  label: React.ReactNode
  value: React.ReactNode
  background?: string
  valueColor?: string
  labelColor?: string
}

export interface AgentMarkProps extends React.HTMLAttributes<HTMLSpanElement> {
  glyph: React.ReactNode
  accent?: string
  scale?: number
  flipped?: boolean
}

export interface TabItem {
  id: string
  label: string
  icon?: React.ReactNode
  badge?: number
}

export interface TabBarProps extends Omit<React.HTMLAttributes<HTMLElement>, 'onSelect'> {
  tabs: TabItem[]
  activeId: string
  onSelect: (id: string) => void
  accent?: string
  accentRgb?: string
}

export interface ScheduleEntry {
  id: string
  time: React.ReactNode
  label: React.ReactNode
  state?: React.ReactNode
  stateColor?: string
  actionLabel?: React.ReactNode
  onAction?: () => void
  onSelect?: () => void
  dim?: boolean
}

export interface HomeTile {
  id: string
  label: React.ReactNode
  value: React.ReactNode
  background?: string
  valueColor?: string
  labelColor?: string
  onSelect?: () => void
}

export interface HomeScreenProps {
  role: 'staff' | 'collector'
  title: React.ReactNode
  meta?: React.ReactNode
  avatarSrc?: string
  onOpenAccount?: () => void
  headerAction?: React.ReactNode
  pass?: Omit<SessionPassProps, 'railVariant'>
  tiles?: HomeTile[]
  today?: ScheduleEntry[]
  galaLine?: React.ReactNode
  galaGlyph?: React.ReactNode
  passRail?: 'stub' | 'date'
}

export interface AccountMenuRow {
  id: string
  label: React.ReactNode
  value?: React.ReactNode
  valueColor?: string
  onSelect?: () => void
  href?: string
  external?: boolean
}

export interface AccountMenuProps extends React.HTMLAttributes<HTMLDivElement> {
  userName: React.ReactNode
  roleLabel: React.ReactNode
  avatarSrc?: string
  rows: AccountMenuRow[]
  children?: React.ReactNode
}

export interface PortalShellProps {
  tabs: TabItem[]
  activeTab: string
  onSelectTab: (id: string) => void
  agentAccent?: string
  agentBloomRgb?: string
  agentGlyph?: React.ReactNode
  agentName?: string
  onOpenAgent?: () => void
  overlays?: React.ReactNode
  header?: React.ReactNode
  field?: boolean
  children?: React.ReactNode
}

export interface ChatLine {
  id: string
  from: 'agent' | 'user'
  body: React.ReactNode
  presentation?: 'bubble' | 'card'
  animateReply?: boolean
}

export interface AgentChatSheetProps {
  open: boolean
  onClose: () => void
  agentName: string
  agentGlyph: React.ReactNode
  accent?: string
  accentRgb?: string
  status?: React.ReactNode
  lines: ChatLine[]
  starters?: Array<{ id: string; label: React.ReactNode; onSelect: () => void }>
  composer?: React.ReactNode
  dock?: React.ReactNode
  floatingAction?: React.ReactNode
  working?: boolean
  scrim?: boolean
  placement?: 'full' | 'docked'
  dockedTop?: number | string
  dockedBottom?: number | string
  ariaLabel?: string
}

export interface ReceiptBlockProps extends React.HTMLAttributes<HTMLDivElement> {
  eyebrow: React.ReactNode
  headline?: React.ReactNode
  rows?: Array<{ label: React.ReactNode; value: React.ReactNode; valueColor?: string }>
  accent?: string
  note?: React.ReactNode
  children?: React.ReactNode
}

export type AgentToolTab = 'upload' | 'cards' | 'memory'

export type ActionCardShell = 'intake-docket' | 'chair-ticket' | 'receipt-tape' | 'courier-manifest' | 'heal-chart' | 'memory-shard'

export type NemotronManaPip = '⚡ Nano' | '👁️ Super-VL' | '🔥 Super' | '👑 Ultra'

export interface AgentActionCard {
  id: string
  name: React.ReactNode
  description: React.ReactNode
  shell: ActionCardShell
  manaPip?: NemotronManaPip
  nemotronModel?: string
  typeLine?: string
  oracleText?: string
  mcpTool?: string
}

export interface ActionCardProps {
  card: AgentActionCard
  agentName: string
  accent?: string
  accentRgb?: string
  onCommit?: () => void | Promise<void>
  commitLabel?: string
  onCast?: (card: AgentActionCard) => void | Promise<{ receiptId?: string; status?: string } | void>
  castReceiptId?: string | null
  children?: React.ReactNode
}

export type ButtonVariant = 'primary' | 'accent' | 'outline' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'lg'
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  shape?: 'pill' | 'card'
  fullWidth?: boolean
  children?: React.ReactNode
}

export interface AgentMemoryItem {
  id: string
  text: React.ReactNode
  source: React.ReactNode
  shared?: boolean
  onForget?: () => void
}

export interface AgentToolsProps {
  open: boolean
  onClose: () => void
  agentName: string
  agentGlyph: React.ReactNode
  accent?: string
  accentRgb?: string
  cards: AgentActionCard[]
  onCast: (card: AgentActionCard) => void
  onUpload?: (file: File) => void
  memory?: AgentMemoryItem[]
  initialTab?: AgentToolTab
  scrimTop?: string
  docked?: boolean
}

export interface TextFieldProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: React.ReactNode
  shape?: 'pill' | 'card'
  hint?: React.ReactNode
}

export type BadgeTone = 'ok' | 'pending' | 'danger' | 'info' | 'gold' | 'neutral'

export interface StatusTextProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone
  dot?: boolean
}

export const TextField: React.ForwardRefExoticComponent<TextFieldProps & React.RefAttributes<HTMLInputElement>>
export const StatusText: React.ComponentType<StatusTextProps>
export const Avatar: React.ComponentType<AvatarProps>
export const Button: React.ComponentType<ButtonProps>
export const ChatBubble: React.ComponentType<React.HTMLAttributes<HTMLDivElement> & {
  from: 'agent' | 'user'
  glyph?: React.ReactNode
  accent?: string
}>
export const IconButton: React.ComponentType<IconButtonProps>
export const PageTitle: React.ComponentType<PageTitleProps>
export const SectionLabel: React.ComponentType<SectionLabelProps>
export const SessionPass: React.ComponentType<SessionPassProps>
export const QuickTile: React.ComponentType<QuickTileProps>
export const AgentMark: React.ComponentType<AgentMarkProps>
export const TabBar: React.ComponentType<TabBarProps>
export const HomeScreen: React.ComponentType<HomeScreenProps>
export const AccountMenu: React.ComponentType<AccountMenuProps>
export const PortalShell: React.ComponentType<PortalShellProps>
export const AgentChatSheet: React.ComponentType<AgentChatSheetProps>
export const AgentTools: React.ComponentType<AgentToolsProps>
export const ActionCard: React.ComponentType<ActionCardProps>
export const ReceiptBlock: React.ComponentType<ReceiptBlockProps>

export const token: {
  readonly color: Record<string, string>
  readonly font: Record<string, string>
  readonly size: Record<string, string>
  readonly space: Record<string, string>
}

export interface ChipProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'aria-pressed'> {
  selected?: boolean
  accent?: string
  children?: React.ReactNode
}
export declare const Chip: React.ForwardRefExoticComponent<ChipProps & React.RefAttributes<HTMLButtonElement>>
export interface ChipGroupProps extends React.HTMLAttributes<HTMLDivElement> {
  label?: React.ReactNode
  children?: React.ReactNode
}
export declare function ChipGroup(props: ChipGroupProps): React.JSX.Element

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone
  filled?: boolean
}
export declare function Badge(props: BadgeProps): React.JSX.Element

export type PanelSurface = 'raised' | 'flat' | 'well'
export type PanelRadius = 'md' | 'lg' | 'xl'
export interface PanelProps extends React.HTMLAttributes<HTMLDivElement> {
  surface?: PanelSurface
  radiusStep?: PanelRadius
  bordered?: boolean
  lifted?: boolean
  children?: React.ReactNode
}
export declare const Panel: React.ForwardRefExoticComponent<PanelProps & React.RefAttributes<HTMLDivElement>>

export interface MetaListProps extends React.HTMLAttributes<HTMLDivElement> {
  rows: Array<{ label: React.ReactNode; value: React.ReactNode; valueColor?: string }>
}
export declare function MetaList(props: MetaListProps): React.JSX.Element
