import * as React from 'react';
import { color } from '../tokens';
import { Sheet } from '../components/Sheet';
import { TextField } from '../components/TextField';
import { Chip, ChipGroup } from '../components/Chip';
import { Button } from '../components/Button';

export interface BookingOption {
  id: string;
  label: React.ReactNode;
}

export interface BookingSheetProps {
  open: boolean;
  onClose: () => void;
  /** Day being booked — the sheet's title. */
  dayLabel: React.ReactNode;

  name: string;
  onNameChange: (v: string) => void;

  times: BookingOption[];
  selectedTime?: string;
  onSelectTime: (id: string) => void;

  types: BookingOption[];
  selectedType?: string;
  onSelectType: (id: string) => void;

  artists: BookingOption[];
  selectedArtist?: string;
  onSelectArtist: (id: string) => void;

  onCommit: () => void;
  /** Booky's line above the commit button. */
  note?: React.ReactNode;
  commitLabel?: React.ReactNode;
}

/**
 * Staff quick-book sheet, assembled from the scene: name, then time, type and
 * artist as chip rows, then commit. Ordered exactly as designed — do not
 * reorder to "group the pickers."
 */
export function BookingSheet({
  open, onClose, dayLabel, name, onNameChange,
  times, selectedTime, onSelectTime,
  types, selectedType, onSelectType,
  artists, selectedArtist, onSelectArtist,
  onCommit, note, commitLabel = 'Book it →',
}: BookingSheetProps) {
  return (
    <Sheet open={open} onClose={onClose} label="New booking" eyebrow="New booking" title={dayLabel} accent={color.goldText}>
      <TextField
        value={name}
        onChange={(e) => onNameChange(e.currentTarget.value)}
        placeholder="Client name, or leave it for a walk-in"
      />
      <ChipGroup label="Time · open slots">
        {times.map((t) => (
          <Chip key={t.id} selected={t.id === selectedTime} onClick={() => onSelectTime(t.id)}>{t.label}</Chip>
        ))}
      </ChipGroup>
      <ChipGroup label="Sesh type">
        {types.map((t) => (
          <Chip key={t.id} selected={t.id === selectedType} onClick={() => onSelectType(t.id)}>{t.label}</Chip>
        ))}
      </ChipGroup>
      <ChipGroup label="Artist">
        {artists.map((a) => (
          <Chip key={a.id} selected={a.id === selectedArtist} onClick={() => onSelectArtist(a.id)}>{a.label}</Chip>
        ))}
      </ChipGroup>
      {note ? (
        <span style={{ fontFamily: 'var(--font-btn)', fontSize: 'var(--text-xs)', letterSpacing: '0.08em', color: color.textDim }}>
          {note}
        </span>
      ) : null}
      <Button variant="primary" fullWidth onClick={onCommit}>{commitLabel}</Button>
    </Sheet>
  );
}
