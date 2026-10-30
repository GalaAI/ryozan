import * as React from 'react';
import { color, radius, shadow } from '../tokens';

export type PanelSurface = 'raised' | 'flat' | 'well';
export type PanelRadius = 'md' | 'lg' | 'xl';

export interface PanelProps extends React.HTMLAttributes<HTMLDivElement> {
  /** raised = the top-lit gradient panel; flat = solid panel; well = recessed. */
  surface?: PanelSurface;
  radiusStep?: PanelRadius;
  /** Draw the 1px border. Off for panels nested inside a bordered parent. */
  bordered?: boolean;
  /** Apply the cast-shadow lift. */
  lifted?: boolean;
  children?: React.ReactNode;
}

const surfaces: Record<PanelSurface, string> = {
  raised: color.panelRaised,
  flat: color.panel,
  well: color.bgDark,
};

/** The portal's card/panel container. Every bordered surface in both scenes. */
export const Panel = React.forwardRef<HTMLDivElement, PanelProps>(function Panel(
  { surface = 'raised', radiusStep = 'lg', bordered = true, lifted = false, style, children, ...rest },
  ref,
) {
  return (
    <div
      ref={ref}
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        background: surfaces[surface],
        border: bordered ? `1px solid ${color.panelBorder}` : undefined,
        borderRadius: radius[radiusStep],
        boxShadow: lifted ? shadow.lift : undefined,
        overflow: 'hidden',
        boxSizing: 'border-box',
        ...style,
      }}
      {...rest}
    >
      {children}
    </div>
  );
});
