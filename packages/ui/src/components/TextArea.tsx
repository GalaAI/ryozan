import * as React from 'react';
import { color, font, radius, motion, layout } from '../tokens';

export interface TextAreaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  /** Mono label above the field. */
  label?: React.ReactNode;
  hint?: React.ReactNode;
}

/**
 * Multi-line input. TextField's label, hint, well, hairline and gold focus,
 * with the Form Inputs card's textarea rules: grows vertically, body leading,
 * line breaks kept. Card corner only: a pill cannot hold more than one line.
 */
export const TextArea = React.forwardRef<HTMLTextAreaElement, TextAreaProps>(function TextArea(
  { label, hint, style, id, rows = 3, onFocus, onBlur, ...rest },
  ref,
) {
  const [focused, setFocused] = React.useState(false);
  const autoId = React.useId();
  const fieldId = id ?? autoId;
  const hintId = hint ? `${fieldId}-hint` : undefined;
  const describedBy = [rest['aria-describedby'], hintId].filter(Boolean).join(' ') || undefined;
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
      <textarea
        {...rest}
        ref={ref}
        id={fieldId}
        rows={rows}
        aria-describedby={describedBy}
        onFocus={(e) => { setFocused(true); onFocus?.(e); }}
        onBlur={(e) => { setFocused(false); onBlur?.(e); }}
        style={{
          display: 'block',
          width: '100%',
          boxSizing: 'border-box',
          minHeight: layout.touchMin,
          padding: 'var(--space-2) var(--space-3)',
          border: `1px solid ${focused ? color.borderActive : color.panelBorder}`,
          // Focus ring: the gold edge doubled to 2px, so focus never rests on a 1px hue shift.
          boxShadow: focused ? `0 0 0 var(--hair) ${color.borderActive}` : undefined,
          borderRadius: radius.md,
          background: color.bgDark,
          color: color.text,
          fontFamily: font.body,
          fontSize: 'var(--text-sm)',
          lineHeight: 'var(--lh-body)',
          resize: 'vertical',
          outline: 'none',
          transition: `border-color ${motion.fast}`,
          ...style,
        }}
      />
      {hint ? (
        <span id={hintId} style={{ fontFamily: font.btn, fontSize: 'var(--text-xs)', letterSpacing: '0.08em', color: color.textDim }}>
          {hint}
        </span>
      ) : null}
    </div>
  );
});
