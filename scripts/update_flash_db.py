import asyncio
import uuid
import json
from datetime import datetime, timezone
import asyncpg

async def main():
    conn = await asyncpg.connect('postgresql://postgres:postgres@localhost:5433/nexus')
    sheet_id = uuid.UUID('7a0fee1a-4374-4d92-8fe1-84745dc23f51')
    artist_id = uuid.UUID('c7845794-ebbe-4fc1-9b86-011a320176f0')
    now = datetime.now(timezone.utc)

    # Delete existing designs
    await conn.execute('DELETE FROM ryozan_flash_designs WHERE sheet_id = $1', sheet_id)
    print('Deleted old designs')

    designs = [
        ('Kappa', 'Kappa Mask', 520.0, 180, '6 in', 2.35, 4.85, 32.78, 38.64, '/dependencies/@bttco/booking-flow/1.0.0/assets/thumbs/flash-yokai-kappa.png'),
        ('Hitotsume', 'Third Eye Monk', 540.0, 180, '6.5 in', 32.78, 2.42, 33.78, 41.07, '/dependencies/@bttco/booking-flow/1.0.0/assets/thumbs/flash-yokai-third-eye.png'),
        ('Tengu', 'Tengu Mask', 560.0, 180, '7 in', 64.47, 3.64, 35.53, 44.71, '/dependencies/@bttco/booking-flow/1.0.0/assets/thumbs/flash-yokai-tengu.png'),
        ('Heikegani', 'Samurai Ghost Crab', 580.0, 210, '7.5 in', 0.18, 47.14, 39.46, 44.71, '/dependencies/@bttco/booking-flow/1.0.0/assets/thumbs/flash-yokai-crab.png'),
        ('Lion Dance', 'Umbrella Dragon', 560.0, 180, '6 in', 39.46, 49.57, 31.13, 43.50, '/dependencies/@bttco/booking-flow/1.0.0/assets/thumbs/flash-yokai-lion-dance.png'),
        ('Wrapped Eye', 'Chochin-obake Lantern', 460.0, 150, '5.5 in', 70.59, 48.36, 29.41, 44.71, '/dependencies/@bttco/booking-flow/1.0.0/assets/thumbs/flash-yokai-mummy-eye.png'),
    ]

    for name, ai_name, price, mins, size_lbl, cx, cy, cw, ch, img_url in designs:
        meta = json.dumps({'asset_version': 3, 'transparent_cutout': True})
        await conn.execute('''
            INSERT INTO ryozan_flash_designs (
                id, sheet_id, artist_id, name, ai_suggested_name, price, estimated_minutes,
                size_label, crop_x, crop_y, crop_w, crop_h, image_url, style, status,
                gala_sync_enabled, ai_metadata, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, 'japanese', 'published',
                false, $14::jsonb, $15, $15
            )
        ''', uuid.uuid4(), sheet_id, artist_id, name, ai_name, price, mins, size_lbl, cx, cy, cw, ch, img_url, meta, now)
        print('Inserted design:', name)

    await conn.execute('''
        UPDATE ryozan_flash_sheets
        SET design_count = 6,
            status = $1,
            image_url = $2,
            crop_applied = true,
            crop_x = $3,
            crop_y = $4,
            crop_w = $5,
            crop_h = $6
        WHERE id = $7
    ''', 'segmented', '/dependencies/@bttco/booking-flow/1.0.0/assets/flash-sheets/flash-yokai-clean.jpg', 1.587, 16.846, 98.145, 76.416, sheet_id)
    print('Updated sheet design_count = 6, clean image_url and crop bounds')
    await conn.close()

if __name__ == '__main__':
    asyncio.run(main())
