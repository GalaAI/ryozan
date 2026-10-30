import os
from collections import deque
from PIL import Image, ImageFilter

def generate_all():
    sheet_path = 'sheet.jpg'
    sheet = Image.open(sheet_path).convert('RGBA')
    W, H = sheet.size
    pix = sheet.load()

    # Step 1: Detect art pixels on full master sheet
    print('Scanning full sheet for art pixels...')
    is_art = [[False]*W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            r, g, b, _ = pix[x, y]
            bright = (r + g + b) / 3.0
            sat = max(r, g, b) - min(r, g, b)
            # Paper is bright >= 135 and sat <= 22
            if not (bright >= 135 and sat <= 22):
                is_art[y][x] = True

    seeds = [
        ('flash-yokai-kappa.png', 750, 1450),
        ('flash-yokai-third-eye.png', 2000, 1500),
        ('flash-yokai-tengu.png', 3250, 1550),
        ('flash-yokai-crab.png', 700, 2800),
        ('flash-yokai-lion-dance.png', 2200, 3000),
        ('flash-yokai-mummy-eye.png', 3400, 3000),
    ]

    dest_dirs = [
        'app/public/dependencies/@bttco/booking-flow/1.0.0/assets/thumbs',
        'app/dist/dependencies/@bttco/booking-flow/1.0.0/assets/thumbs',
    ]
    for d in dest_dirs:
        os.makedirs(d, exist_ok=True)

    for filename, sx, sy in seeds:
        print(f'Extracting {filename} from seed ({sx}, {sy})...')
        visited = set([(sx, sy)])
        q = deque([(sx, sy)])
        min_x, max_x = sx, sx
        min_y, max_y = sy, sy

        while q:
            cx, cy = q.popleft()
            min_x = min(min_x, cx)
            max_x = max(max_x, cx)
            min_y = min(min_y, cy)
            max_y = max(max_y, cy)
            for dx, dy in ((-1,0), (1,0), (0,-1), (0,1)):
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < W and 0 <= ny < H and (nx, ny) not in visited and is_art[ny][nx]:
                    visited.add((nx, ny))
                    q.append((nx, ny))

        pad = 25
        bx1 = max(0, min_x - pad)
        by1 = max(0, min_y - pad)
        bx2 = min(W, max_x + pad)
        by2 = min(H, max_y + pad)
        bw = bx2 - bx1
        bh = by2 - by1

        crop = sheet.crop((bx1, by1, bx2, by2))
        bg = [[False]*bw for _ in range(bh)]
        bq = deque()

        for x in range(bw):
            gx = bx1 + x
            for y in (0, bh-1):
                gy = by1 + y
                if (gx, gy) not in visited:
                    bg[y][x] = True
                    bq.append((x, y))

        for y in range(bh):
            gy = by1 + y
            for x in (0, bw-1):
                gx = bx1 + x
                if not bg[y][x] and (gx, gy) not in visited:
                    bg[y][x] = True
                    bq.append((x, y))

        while bq:
            cx, cy = bq.popleft()
            for dx, dy in ((-1,0), (1,0), (0,-1), (0,1)):
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < bw and 0 <= ny < bh and not bg[ny][nx]:
                    gx = bx1 + nx
                    gy = by1 + ny
                    if (gx, gy) not in visited:
                        bg[ny][nx] = True
                        bq.append((nx, ny))

        fg = Image.new('L', (bw, bh), 0)
        fg_pix = fg.load()
        for y in range(bh):
            for x in range(bw):
                if not bg[y][x]:
                    fg_pix[x, y] = 255

        mask = fg.filter(ImageFilter.MaxFilter(size=3)).filter(ImageFilter.GaussianBlur(radius=0.8))
        crop.putalpha(mask)

        bbox = crop.getbbox()
        if bbox:
            crop = crop.crop(bbox)

        crop.thumbnail((1200, 1200), Image.LANCZOS)
        for d in dest_dirs:
            out_file = os.path.join(d, filename)
            crop.save(out_file, 'PNG')
        print(f'Saved {filename}, final size: {crop.size}')

    print('All 6 cutouts regenerated with 100% natural bounds!')

if __name__ == '__main__':
    generate_all()
