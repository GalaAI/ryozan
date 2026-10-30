import * as React from 'react';

export interface EdgeDissolveRailProps {
  /** Controls that remain sharp and stationary above the scrolling viewport. */
  rail?: React.ReactNode;
  /** Elements that scroll and dissolve only as they leave through the top edge. */
  children?: React.ReactNode;
  className?: string;
  railClassName?: string;
  viewportClassName?: string;
  style?: React.CSSProperties;
  railStyle?: React.CSSProperties;
  viewportStyle?: React.CSSProperties;
  /** Scroll distance before the edge dissolve is armed. */
  threshold?: number;
  onScroll?: React.UIEventHandler<HTMLDivElement>;
}

function classes(...values: Array<string | undefined | false>) {
  return values.filter(Boolean).join(' ');
}

/**
 * Arksenal component: a fixed action rail paired with a masked scroll viewport.
 * The mask belongs to the scrolling elements, never the page background or rail.
 */
export function EdgeDissolveRail({
  rail,
  children,
  className,
  railClassName,
  viewportClassName,
  style,
  railStyle,
  viewportStyle,
  threshold = 2,
  onScroll,
}: EdgeDissolveRailProps) {
  const [isTopScrolled, setIsTopScrolled] = React.useState(false);

  const handleScroll: React.UIEventHandler<HTMLDivElement> = (event) => {
    const next = event.currentTarget.scrollTop > threshold;
    setIsTopScrolled((current) => current === next ? current : next);
    onScroll?.(event);
  };

  return (
    <div className={classes('bttco-edge-dissolve-rail', className)} style={style}>
      {rail ? (
        <div className={classes('bttco-edge-dissolve-rail__rail', railClassName)} style={railStyle}>
          {rail}
        </div>
      ) : null}
      <div
        className={classes(
          'bttco-edge-dissolve-rail__viewport',
          isTopScrolled && 'is-top-scrolled',
          viewportClassName,
        )}
        onScroll={handleScroll}
        style={viewportStyle}
      >
        {children}
      </div>
    </div>
  );
}
