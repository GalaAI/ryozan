import * as React from 'react';
import { color, font, motion, layout } from '../tokens';

export interface CheckboxProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type'> {
  /** The sentence the box agrees to. Clicking it toggles the box. */
  label: React.ReactNode;
  hint?: React.ReactNode;
}

/**
 * An explicit opt-in: a real checkbox input inside its label, so the box, the
 * label, Space and screen readers all agree. Unchecked unless the caller says
 * otherwise. Gold check and gold focus ring, never the OS blue; label set in
 * TextField's input voice, hint in its hint voice.
 */
export const Checkbox = React.forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { label, hint, style, id, disabled, onFocus, onBlur, ...rest },
  ref,
) {
  const [focused, setFocused] = React.useState(false);
  const autoId = React.useId();
  const fieldId = id ?? autoId;
  const hintId = hint ? `${fieldId}-hint` : undefined;
  const describedBy = [rest['aria-describedby'], hintId].filter(Boolean).join(' ') || undefined;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)', ...style }}>
      <label
        htmlFor={fieldId}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 'var(--space-3)',
          minHeight: layout.touchMin,
          cursor: disabled ? 'not-allowed' : 'pointer',
          opacity: disabled ? 0.5 : 1,
          fontFamily: font.body,
          fontSize: 'var(--text-sm)',
          color: color.text,
        }}
      >
        <input
          {...rest}
          ref={ref}
          id={fieldId}
          type="checkbox"
          disabled={disabled}
          aria-describedby={describedBy}
          onFocus={(e) => { setFocused(true); onFocus?.(e); }}
          onBlur={(e) => { setFocused(false); onBlur?.(e); }}
          style={{
            flex: 'none',
            width: 'var(--space-5)',
            height: 'var(--space-5)',
            margin: 0,
            accentColor: color.gold,
            cursor: 'inherit',
            outline: focused ? `1px solid ${color.borderActive}` : 'none',
            outlineOffset: 'var(--space-1)',
            transition: `outline-color ${motion.fast}`,
          }}
        />
        <span>{label}</span>
      </label>
      {hint ? (
        <span id={hintId} style={{ fontFamily: font.btn, fontSize: 'var(--text-xs)', letterSpacing: '0.08em', color: color.textDim }}>
          {hint}
        </span>
      ) : null}
    </div>
  );
});
