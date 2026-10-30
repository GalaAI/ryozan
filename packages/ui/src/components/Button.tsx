import * as React from 'react';
import { color, font, radius, shadow, motion, layout } from '../tokens';

export type ButtonVariant = 'primary' | 'accent' | 'outline' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Pill (default) or the denser dashboard 8px radius. */
  shape?: 'pill' | 'card';
  fullWidth?: boolean;
  children?: React.ReactNode;
}

const heights: Record<ButtonSize, string> = { sm: '30px', md: layout.touchMin, lg: '48px' };
const pads: Record<ButtonSize, string> = { sm: '0 var(--space-3)', md: '0 var(--space-5)', lg: '0 var(--space-6)' };
const fonts: Record<ButtonSize, string> = { sm: 'var(--text-xs)', md: 'var(--text-sm)', lg: 'var(--text-sm)' };

/**
 * Always uppercase, always mono, always letter-spaced — the brand's button
 * signature. 'primary' is filled gold; 'accent' is the heritage-pink CTA;
 * 'outline' is the gold hairline; 'ghost' is chrome-less.
 */
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', shape = 'pill', fullWidth = false, style, children, ...rest },
  ref,
) {
  const skins: Record<ButtonVariant, React.CSSProperties> = {
    primary: { background: color.gold, color: color.bg, border: 0 },
    accent: { background: color.pink, color: color.smoke, border: 0, boxShadow: shadow.gold },
    outline: { background: 'transparent', color: color.gold, border: `1px solid ${color.gold}` },
    ghost: { background: 'transparent', color: color.textMuted, border: `1px solid ${color.borderSubtle}` },
    danger: { background: 'transparent', color: color.danger, border: `1px solid ${color.danger}` },
  };
  return (
    <button
      ref={ref}
      type="button"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 'var(--space-2)',
        width: fullWidth ? '100%' : undefined,
        minHeight: heights[size],
        padding: pads[size],
        borderRadius: shape === 'pill' ? radius.pill : radius.md,
        cursor: 'pointer',
        fontFamily: font.btn,
        fontSize: fonts[size],
        fontWeight: 700,
        letterSpacing: '0.16em',
        textTransform: 'uppercase',
        whiteSpace: 'nowrap',
        transition: `background ${motion.base}, color ${motion.base}, transform ${motion.base}`,
        ...skins[variant],
        ...style,
      }}
      {...rest}
    >
      {children}
    </button>
  );
});
