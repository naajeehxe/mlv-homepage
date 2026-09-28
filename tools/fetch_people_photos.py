#!/usr/bin/env python3
"""fetch_people_photos.py — helper run in GitHub Actions.

Google Sites profile photos (lh7-us.googleusercontent.com/sitesv-images-rt/…) answer 403 unless the request
carries the site as Referer, so Notion cannot import them and Claude's sandbox cannot reach them at all.
This downloads the photos listed in tools/photo_requests.json (name + URL), resizes them to the snapshot
size and adds them to snapshot/manifest.json under a person:<slug> key labelled with the person's name —
the builder matches people photos by name, so the new rows pick them up on the next build.

Once the person uploads a picture to Notion's "Photo File" field, that takes precedence automatically.
"""
import io, json, os, re, sys
import requests
from PIL import Image, ImageOps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQ = os.path.join(ROOT, 'tools', 'photo_requests.json')
MAN = os.path.join(ROOT, 'snapshot', 'manifest.json')
OUT = os.path.join(ROOT, 'snapshot', 'img', 'people')
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) mlv-homepage-builder/1.0', 'Referer': 'https://www.hyunwoojkim.com/'}

reqs = json.load(open(REQ)) if os.path.exists(REQ) else []
man = json.load(open(MAN))
os.makedirs(OUT, exist_ok=True)
done = 0
for p in reqs:
    name, url = p['name'], p['url']
    slug = re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_')
    key = f'person:{slug.lower()}'
    if key in man['files'] and os.path.exists(os.path.join(ROOT, 'snapshot', man['files'][key])) and not p.get('force'):
        print(f'  = {name}: already in snapshot'); continue
    try:
        r = requests.get(url, headers=HEADERS, timeout=60); r.raise_for_status()
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(r.content))).convert('RGB')
        if im.width > 800: im = im.resize((800, round(im.height * 800 / im.width)), Image.LANCZOS)
        rel = f'img/people/{slug}.webp'
        im.save(os.path.join(ROOT, 'snapshot', rel), 'WEBP', quality=85)
        man['files'][key] = rel; man['labels'][key] = name; done += 1
        print(f'  + {name}: {im.size} -> {rel}')
    except Exception as e:
        print(f'  ! {name}: {e}', file=sys.stderr)
json.dump(man, open(MAN, 'w'), indent=1, ensure_ascii=False)
print(f'{done} photo(s) added to the snapshot')
