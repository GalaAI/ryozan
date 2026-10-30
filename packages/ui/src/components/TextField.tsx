import * as React from 'react';
import { color, font, radius, motion, layout } from '../tokens';

export interface TextFieldProps extends React.InputHTMLAttributes<HTMLInputElement> {
  /** Mono label above the field. */
  label?: React.ReactNode;
  /** Pill for collector-facing forms, card for dense staff forms. */
  shape?: 'pill' | 'card';
  hint?: React.ReactNode;
}

/** Single-line input. Gold focus ring, never the OS blue. */
export const TextField = React.forwardRef<HTMLInputElement, TextFieldProps>(function TextField(
  { label, hint, shape = 'card', style, id, ...rest },
  ref,
) {
  const [focused, setFocused] = React.useState(false);
  const autoId = React.useId();
  const fieldId = id ?? autoId;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
      {label ? (
        <label
          htmlFor={fieldId}
          style={{
            fontFamily: font.btn,
            fontSize: 'var(--text-xs)',
            fontWeight: 700,
            letterSpacing: '0.14em',
            textTransform: 'uppercase',
            color: color.textMuted,
          }}
        >
          {label}
        </label>
      ) : null}
      <input
        ref={ref}
        id={fieldId}
        onFocus={(e) => { setFocused(true); rest.onFocus?.(e); }}
        onBlur={(e) => { setFocused(false); rest.onBlur?.(e); }}
        style={{
          width: '100%',
          boxSizing: 'border-box',
          minHeight: layout.touchMin,
          padding: '0 var(--space-3)',
          border: `1px solid ${focused ? color.borderActive : color.panelBorder}`,
          borderRadius: shape === 'pill' ? radius.pill : radius.md,
          background: color.bgDark,
          color: color.text,
          fontFamily: font.body,
          fontSize: 'var(--text-sm)',
          outline: 'none',
          transition: `border-color ${motion.fast}`,
          ...style,
        }}
        {...rest}
      />
      {hint ? (
        <span style={{ fontFamily: font.btn, fontSize: 'var(--text-xs)', letterSpacing: '0.08em', color: color.textDim }}>
          {hint}
        </span>
      ) : null}
    </div>
  );
});
