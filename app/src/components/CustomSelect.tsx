import { useState, useRef, useEffect, useCallback } from 'react';

export interface SelectOption {
  value: string;
  label: string;
  description?: string;
}

interface CustomSelectProps {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  placeholder?: string;
  className?: string;
  disabled?: boolean;
  /** Compact mode for inline/table usage (smaller padding, no border-radius) */
  compact?: boolean;
}

export function CustomSelect({
  value,
  onChange,
  options,
  placeholder = 'Select...',
  className = '',
  disabled = false,
  compact = false,
}: CustomSelectProps) {
  const [open, setOpen] = useState(false);
  const [focusIdx, setFocusIdx] = useState(-1);
  const wrapRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const selected = options.find((o) => o.value === value);

  // ── Close on outside click ──
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  // ── Scroll focused option into view ──
  useEffect(() => {
    if (!open || focusIdx < 0 || !listRef.current) return;
    const el = listRef.current.children[focusIdx] as HTMLElement;
    el?.scrollIntoView({ block: 'nearest' });
  }, [focusIdx, open]);

  // ── Keyboard nav ──
  const handleKey = useCallback(
    (e: React.KeyboardEvent) => {
      if (disabled) return;

      switch (e.key) {
        case 'Enter':
        case ' ':
          e.preventDefault();
          if (open && focusIdx >= 0) {
            onChange(options[focusIdx].value);
            setOpen(false);
          } else {
            setOpen(true);
            setFocusIdx(options.findIndex((o) => o.value === value));
          }
          break;
        case 'ArrowDown':
          e.preventDefault();
          if (!open) {
            setOpen(true);
            setFocusIdx(options.findIndex((o) => o.value === value));
          } else {
            setFocusIdx((i) => Math.min(i + 1, options.length - 1));
          }
          break;
        case 'ArrowUp':
          e.preventDefault();
          if (open) setFocusIdx((i) => Math.max(i - 1, 0));
          break;
        case 'Escape':
          setOpen(false);
          break;
        case 'Tab':
          if (open) setOpen(false);
          break;
      }
    },
    [disabled, open, focusIdx, options, value, onChange]
  );

  const toggle = () => {
    if (disabled) return;
    setOpen((prev) => {
      if (!prev) setFocusIdx(options.findIndex((o) => o.value === value));
      return !prev;
    });
  };

  const pick = (val: string) => {
    onChange(val);
    setOpen(false);
  };

  const cls = [
    'csel',
    compact ? 'csel--compact' : '',
    open ? 'csel--open' : '',
    disabled ? 'csel--disabled' : '',
    className,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={cls} ref={wrapRef} onKeyDown={handleKey}>
      <button
        type="button"
        className="csel-trigger"
        onClick={toggle}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        tabIndex={0}
      >
        <span className={selected ? 'csel-value' : 'csel-placeholder'}>
          {selected ? selected.label : placeholder}
        </span>
        <svg className="csel-chevron" width="12" height="12" viewBox="0 0 12 12" fill="none">
          <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <div className="csel-dropdown" role="listbox" ref={listRef}>
          {options.map((opt, i) => (
            <div
              key={opt.value}
              role="option"
              aria-selected={opt.value === value}
              className={[
                'csel-option',
                opt.value === value ? 'csel-option--selected' : '',
                i === focusIdx ? 'csel-option--focused' : '',
              ]
                .filter(Boolean)
                .join(' ')}
              onClick={() => pick(opt.value)}
              onMouseEnter={() => setFocusIdx(i)}
            >
              <span className="csel-option-label">{opt.label}</span>
              {opt.description && (
                <span className="csel-option-desc">{opt.description}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
