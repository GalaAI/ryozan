/**
 * @bttco/ui — Ryozan Studio portal components.
 *
 * Consumers import from HERE. Never from a component path, never a local
 * re-implementation. If a screen needs something this barrel does not export,
 * that component has not been designed yet — raise it, do not author it.
 *
 * Load order in the app shell:
 *   1. @bttco/ui/fonts.css
 *   2. @bttco/ui/tokens.css
 *   3. your app styles
 */

// ── tokens ────────────────────────────────────────────────
export { token, color, font, size, space, radius, shadow, motion, layout } from './tokens';
export type { Token } from './tokens';

// ── primitives ────────────────────────────────────────────
export { Panel } from './components/Panel';
export type { PanelProps, PanelSurface, PanelRadius } from './components/Panel';

export { BackgroundField } from './components/BackgroundField';
export type { BackgroundFieldProps } from './components/BackgroundField';

export { GrainOverlay } from './components/GrainOverlay';
export type { GrainOverlayProps } from './components/GrainOverlay';

export { EdgeDissolveRail } from './components/EdgeDissolveRail';
export type { EdgeDissolveRailProps } from './components/EdgeDissolveRail';

export { SectionLabel } from './components/SectionLabel';
export type { SectionLabelProps, SectionLabelTone } from './components/SectionLabel';

export { PageTitle } from './components/PageTitle';
export type { PageTitleProps } from './components/PageTitle';

export { MetaRow, MetaList } from './components/MetaRow';
export type { MetaRowProps, MetaListProps } from './components/MetaRow';

export { Button } from './components/Button';
export type { ButtonProps, ButtonVariant, ButtonSize } from './components/Button';

export { IconButton } from './components/IconButton';
export type { IconButtonProps } from './components/IconButton';

export { Chip, ChipGroup } from './components/Chip';
export type { ChipProps, ChipGroupProps } from './components/Chip';

export { Badge, StatusText } from './components/Badge';
export type { BadgeProps, BadgeTone, StatusTextProps } from './components/Badge';

export { TextField } from './components/TextField';
export type { TextFieldProps } from './components/TextField';

export { Avatar } from './components/Avatar';
export type { AvatarProps } from './components/Avatar';

export { Wordmark } from './components/Wordmark';
export type { WordmarkProps } from './components/Wordmark';

// ── portal elements ───────────────────────────────────────
export { SessionPass } from './components/SessionPass';
export type { SessionPassProps, SessionPassRow } from './components/SessionPass';

export { ScheduleList } from './components/ScheduleList';
export type { ScheduleListProps, ScheduleEntry } from './components/ScheduleList';

export { QuickTile } from './components/QuickTile';
export type { QuickTileProps } from './components/QuickTile';

export { TabBar } from './components/TabBar';
export type { TabBarProps, TabItem } from './components/TabBar';

export { NavRail } from './components/NavRail';
export type { NavRailProps } from './components/NavRail';

export { TopBar } from './components/TopBar';
export type { TopBarProps } from './components/TopBar';

export { AccountMenu } from './components/AccountMenu';
export type { AccountMenuProps, AccountMenuRow } from './components/AccountMenu';

export { Sheet } from './components/Sheet';
export type { SheetProps } from './components/Sheet';

export { AgentMark, AgentFab } from './components/AgentMark';
export type { AgentMarkProps, AgentFabProps } from './components/AgentMark';

export { AgentGlow } from './components/AgentGlow';
export type { AgentGlowProps } from './components/AgentGlow';

export { AgentSparkles } from './components/AgentSparkles';
export type { AgentSparklesProps } from './components/AgentSparkles';

export { ChatBubble } from './components/ChatBubble';
export type { ChatBubbleProps } from './components/ChatBubble';

export { ActionCard, ActionCardFace } from './components/ActionCard';
export type { ActionCardProps, ActionCardShell, AgentActionCard } from './components/ActionCard';

export { ReceiptBlock } from './components/ReceiptBlock';
export type { ReceiptBlockProps } from './components/ReceiptBlock';

// ── assembled screens ─────────────────────────────────────
// Use THESE, not the primitives, when you need a screen. A screen that is
// missing here has not been designed — ask, do not author it.
export * from './screens';
