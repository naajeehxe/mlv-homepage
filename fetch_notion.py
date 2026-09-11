#!/usr/bin/env python3
"""
fetch_notion.py — Notion "MLV Lab homepage DB" → site_data.json (+ images)

Reads the 10 databases under the "MLV Lab homepage DB" page through the official
Notion API and writes:
  build/site_data.json      the data the template renders
  site/img/…                every image the site needs (downloaded/resized)

Usage:
  export NOTION_TOKEN=secret_xxx          # internal integration token
  python fetch_notion.py                  # full fetch
  python fetch_notion.py --no-images      # skip image downloads (fast, text only)

Image priority for every row: "… File" (uploaded to Notion)  >  "… URL"  >  snapshot/ copy
shipped with this repo (the 2026-09 Google Sites snapshot), so the site keeps working
even when a Google-hosted URL has expired.
"""
import os, sys, json, re, io, hashlib, time, argparse, urllib.request, urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFG = json.load(open(HERE / 'config.json'))
TOKEN = os.environ.get('NOTION_TOKEN', '')
API = 'https://api.notion.com/v1'
HEADERS = {'Authorization': f'Bearer {TOKEN}', 'Notion-Version': CFG['notion_version'], 'Content-Type': 'application/json'}
OUT = HERE / CFG['output_dir']
IMG = OUT / 'img'
BUILD = HERE / 'build'

try:
    from PIL import Image
except ImportError:
    Image = None

# ----------------------------------------------------------------------------- Notion API
def api(method, path, body=None, retries=4):
    for attempt in range(retries):
        req = urllib.request.Request(API + path, method=method, headers=HEADERS,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(2 ** attempt); continue
            print('Notion API error', e.code, e.read().decode()[:400], file=sys.stderr); raise
    raise RuntimeError('Notion API: too many retries for ' + path)

def query_all(ds_id):
    rows, cursor = [], None
    while True:
        body = {'page_size': 100}
        if cursor: body['start_cursor'] = cursor
        res = api('POST', f'/data_sources/{ds_id}/query', body)
        rows += res.get('results', [])
        if not res.get('has_more'): break
        cursor = res['next_cursor']
    return rows

def blocks(page_id):
    out, cursor = [], None
    while True:
        q = f'/blocks/{page_id}/children?page_size=100' + (f'&start_cursor={cursor}' if cursor else '')
        res = api('GET', q)
        out += res.get('results', [])
        if not res.get('has_more'): break
        cursor = res['next_cursor']
    return out

# ----------------------------------------------------------------------------- property helpers
def rich(arr):
    """rich_text array → markdown (bold + links)."""
    s = ''
    for t in arr or []:
        txt = t.get('plain_text', '')
        if t.get('annotations', {}).get('bold'): txt = f'**{txt}**'
        if t.get('href'): txt = f'[{txt}]({t["href"]})'
        s += txt
    return s

def plain(props, name, default=''):
    """Read a title/rich_text property as plain text (no markdown, no auto-links)."""
    p = props.get(name)
    if not p: return default
    arr = p.get(p['type']) if p['type'] in ('title', 'rich_text') else None
    if arr is None: return prop(props, name, default=default)
    return ''.join(t.get('plain_text', '') for t in arr).strip()

def prop(props, *names, default=''):
    """Read a property by (any of) its name(s), returning a plain Python value."""
    for n in names:
        p = props.get(n)
        if p is None: continue
        t = p['type']
        if t == 'title': return rich(p['title']).replace('**', '')
        if t == 'rich_text': return rich(p['rich_text'])
        if t == 'select': return (p['select'] or {}).get('name', '')
        if t == 'multi_select': return [o['name'] for o in p['multi_select']]
        if t == 'number': return p['number']
        if t == 'checkbox': return bool(p['checkbox'])
        if t in ('url', 'email', 'phone_number'): return p[t] or ''
        if t == 'date': return (p['date'] or {}).get('start', '') or ''
        if t == 'relation': return [r['id'] for r in p['relation']]
        if t == 'files':
            out = []
            for f in p['files']:
                out.append(f.get('file', {}).get('url') or f.get('external', {}).get('url') or '')
            return [u for u in out if u]
        if t == 'formula': return p['formula'].get(p['formula']['type'], '')
    return default

def body_markdown(page_id):
    """Page body → simple markdown (headings, paragraphs, bullets)."""
    lines = []
    for b in blocks(page_id):
        t = b['type']; d = b.get(t, {})
        if t == 'paragraph': lines.append(rich(d.get('rich_text')))
        elif t in ('heading_1', 'heading_2', 'heading_3'): lines.append('\n' + '#' * int(t[-1]) + ' ' + rich(d.get('rich_text')) + '\n')
        elif t == 'bulleted_list_item': lines.append('- ' + rich(d.get('rich_text')))
        elif t == 'numbered_list_item': lines.append('- ' + rich(d.get('rich_text')))
        elif t == 'quote': lines.append('> ' + rich(d.get('rich_text')))
        elif t == 'divider': lines.append('')
    return '\n'.join(lines).strip()

def sort_rows(rows, key='Order', desc=False):
    return sorted(rows, key=lambda r: (prop(r['properties'], key, default=0) or 0), reverse=desc)

def visible(rows):
    return [r for r in rows if prop(r['properties'], 'Show on Site', default=True) is not False]

# ----------------------------------------------------------------------------- images
SNAP = json.load(open(HERE / 'snapshot' / 'manifest.json'))
SNAP_BY_LABEL = {}
for k, lab in SNAP['labels'].items():
    SNAP_BY_LABEL.setdefault((k.split(':')[0], lab.strip().lower()), k)

def snapshot_file(key=None, kind=None, label=None):
    if key and key in SNAP['files']: return HERE / 'snapshot' / SNAP['files'][key]
    if kind and label:
        k = SNAP_BY_LABEL.get((kind, label.strip().lower()))
        if k: return HERE / 'snapshot' / SNAP['files'][k]
    return None

def fetch_bytes(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (mlv-site-builder)', 'Referer': 'https://www.hyunwoojkim.com/'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()

def save_image(key, kind, sources, label='', no_images=False):
    """Try each source (Notion file URL, external URL, snapshot path) → site/img/<hash>.webp. Returns relative path or ''."""
    IMG.mkdir(parents=True, exist_ok=True)
    width = CFG['image_widths'].get(kind, 900)
    name = hashlib.sha1(key.encode()).hexdigest()[:12]
    # Google Sites image URLs (lh3.googleusercontent.com/sitesv/...) are re-signed on every page view and
    # always answer 403 outside the site, so they are skipped silently; the bundled snapshot copy is used instead.
    cands = [s for s in sources if s and 'googleusercontent.com/sitesv/' not in s]
    snap = snapshot_file(key, kind, label)
    if snap: cands.append(str(snap))
    errors = []
    for src in cands:
        try:
            if src.startswith('http'):
                if no_images: continue
                data = fetch_bytes(src)
            else:
                data = open(src, 'rb').read()
            ext = 'png' if data[:4] == b'\x89PNG' and kind == 'social' else 'webp'
            out = IMG / f'{name}.{ext}'
            if Image is not None:
                im = Image.open(io.BytesIO(data)); im.load()
                if im.mode not in ('RGB', 'RGBA'): im = im.convert('RGBA' if 'A' in im.mode else 'RGB')
                if im.width > width: im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
                if ext == 'webp' and im.mode == 'RGBA':
                    bg = Image.new('RGB', im.size, (255, 255, 255)); bg.paste(im, mask=im.split()[-1]); im = bg
                im.save(out, quality=82)
            else:
                out.write_bytes(data)
            return f'img/{out.name}'
        except Exception as e:  # try the next candidate
            errors.append(f'{str(e)[:60]} ({src[:50]})')
    if errors:
        print(f'  ! no image for {key}: ' + ' | '.join(errors), file=sys.stderr)
    return ''

# ----------------------------------------------------------------------------- main mapping
def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--no-images', action='store_true'); args = ap.parse_args()
    if not TOKEN:
        sys.exit('NOTION_TOKEN is not set. Create an internal integration at notion.so/profile/integrations and share the "MLV Lab homepage DB" page with it.')
    ds = CFG['data_sources']; no_img = args.no_images
    A = {}   # asset key → relative path (the template's image map)

    # ---- People
    people, id_to_person = [], {}
    rows = sort_rows(visible(query_all(ds['people'])))
    for i, r in enumerate(rows, 1):
        p = r['properties']; name = prop(p, 'Name')
        key = f'person:{i}'
        photo = save_image(key, 'person', prop(p, 'Photo File', default=[]) + [prop(p, 'Photo', 'Photo URL')], label=name, no_images=no_img)
        if photo: A[key] = photo
        bio = affil = ''; awards = []
        if prop(p, 'Group') == 'Faculty':
            md = body_markdown(r['id'])
            parts = re.split(r'\n## ', '\n' + md)
            for part in parts:
                if part.lower().startswith('about me'):
                    lines = [l for l in part.split('\n')[1:] if l.strip()]
                    # first lines up to the first long paragraph are affiliations
                    aff, rest = [], []
                    for l in lines:
                        (rest if (rest or len(l) > 120) else aff).append(l)
                    affil = '\n'.join(aff); bio = '\n\n'.join(rest)
                elif part.lower().startswith('awards'):
                    awards = [l[2:].strip() for l in part.split('\n')[1:] if l.startswith('- ')]
        person = {
            'id': i, 'name': name, 'ko': prop(p, 'Korean Name'), 'group': prop(p, 'Group'), 'status': prop(p, 'Status'),
            'position': prop(p, 'Position'), 'affiliation': '', 'email': prop(p, 'Email'), 'tel': prop(p, 'Tel'),
            'scholar': prop(p, 'Google Scholar'), 'homepage': prop(p, 'Homepage'), 'cv': prop(p, 'CV'),
            'interests': prop(p, 'Research Interests', default=[]), 'bio': bio, 'affil_detail': affil, 'awards': awards,
            'graduated': prop(p, 'Graduated'), 'after': prop(p, 'After Graduation'), 'photo': key if photo else '',
        }
        people.append(person); id_to_person[r['id']] = i

    # ---- Research figures (needed for publication thumbnails)
    fig_rows = sort_rows(visible(query_all(ds['figures'])))
    fig_by_id, fig_url_to_key = {}, {}
    topic_rows = sort_rows(visible(query_all(ds['topics'])))
    topic_index = {r['id']: n for n, r in enumerate(topic_rows, 1)}
    per_topic_count = {}
    for r in fig_rows:
        p = r['properties']; trel = prop(p, 'Topic', default=[]); t = topic_index.get(trel[0]) if trel else 0
        n = per_topic_count.get(t, 0); per_topic_count[t] = n + 1
        key = f'fig:{t}:{n}'; cap = prop(p, 'Caption')
        files = prop(p, 'Image File', default=[]); url = prop(p, 'Image URL', 'Image')
        path = save_image(key, 'fig', files + [url], label=cap, no_images=no_img)
        if path: A[key] = path
        fig_by_id[r['id']] = {'key': key, 'caption': cap, 'topic': t, 'pub': (prop(p, 'Publication', default=[]) or [None])[0]}
        if url: fig_url_to_key[url] = key

    # ---- Publications
    pubs, pub_index = [], {}
    rows = sort_rows(visible(query_all(ds['publications'])))
    for i, r in enumerate(rows, 1):
        p = r['properties']; pub_index[r['id']] = i
        links = {}
        for label, names in [('Code', ['Code']), ('Video', ['Video']), ('Slides', ['Slides']), ('Poster', ['Poster']), ('Project page', ['Project Page']), ('Demo', ['Demo'])]:
            v = prop(p, *names)
            if v: links[label] = v
        pres = prop(p, 'Presentation'); pl = pres.lower(); badge = ''
        if 'gold star' in pl: badge = 'Gold Star'
        elif 'spotlight' in pl: badge = 'Spotlight'
        elif 'oral' in pl: badge = 'Oral'
        elif 'highlight' in pl: badge = 'Highlight'
        thumb = ''
        tfiles = prop(p, 'Thumbnail File', default=[]); turl = prop(p, 'Thumbnail URL')
        if tfiles or (turl and turl not in fig_url_to_key):
            key = f'pubthumb:{i}'; path = save_image(key, 'fig', tfiles + [turl], no_images=no_img)
            if path: A[key] = path; thumb = key
        elif turl in fig_url_to_key: thumb = fig_url_to_key[turl]
        pubs.append({
            'id': i, 'pid': prop(p, 'Paper ID'), 'type': prop(p, 'Type'), 'year': int(prop(p, 'Year', default=0) or 0),
            'title': prop(p, 'Title'), 'url': prop(p, 'Paper Link', 'Paper'), 'authors': prop(p, 'Authors'), 'venue': prop(p, 'Venue'),
            'venue_short': prop(p, 'Venue (short)'), 'presentation': pres, 'badge': badge, 'collab': prop(p, 'Collaboration'),
            'note': prop(p, 'Note'), 'highlight': bool(prop(p, 'Highlight', default=False)), 'links': links,
            'members': [id_to_person[m] for m in prop(p, 'MLV Members', default=[]) if m in id_to_person], 'thumb': thumb,
            '_topics': prop(p, 'Research Topics', default=[]),
        })
    # thumbnails from figure→publication relation (when a figure points at a paper that has no thumbnail yet)
    for f in fig_by_id.values():
        if f['pub'] in pub_index:
            pb = pubs[pub_index[f['pub']] - 1]
            if not pb['thumb']: pb['thumb'] = f['key']

    # ---- Topics
    topics = []
    for n, r in enumerate(topic_rows, 1):
        p = r['properties']
        figs = [{'img': f['key'], 'caption': f['caption']} for f in fig_by_id.values() if f['topic'] == n]
        rel = []
        for pid in prop(p, 'Related Publications', default=[]):
            if pid in pub_index:
                pb = pubs[pub_index[pid] - 1]
                rel.append({'venue': f"{pb['venue_short']} '{str(pb['year'])[2:]}", 'title': pb['title'], 'url': pb['url'], 'note': pb['presentation'], 'pub': pb['id']})
                pb.setdefault('topics', []).append(n)
        rel.sort(key=lambda x: -pubs[x['pub'] - 1]['year'])
        topics.append({'id': n, 'name': prop(p, 'Topic'), 'desc': prop(p, 'Description'), 'figures': figs, 'related': rel})
    for pb in pubs:
        pb['topics'] = sorted(set(pb.get('topics', []) + [topic_index[t] for t in pb.pop('_topics') if t in topic_index]))

    # ---- News
    news = []
    for r in sort_rows(visible(query_all(ds['news'])), desc=True):
        p = r['properties']; md = prop(p, 'News title', 'Title', 'News')
        extras = []
        for k in (1, 2, 3):
            u = prop(p, f'Link {k}'); lab = prop(p, f'Link Label {k}') or 'Link'
            if u: extras.append(f'[{lab}]({u})')
        if extras: md += ' ' + ' '.join(extras)
        news.append({'date': prop(p, 'Date'), 'md': md, 'new': bool(prop(p, 'New', default=False))})
    news.sort(key=lambda n: n['date'], reverse=True)

    # ---- Teaching
    teaching = []
    for r in visible(query_all(ds['teaching'])):
        p = r['properties']
        teaching.append({'semester': prop(p, 'Semester'), 'year': int(prop(p, 'Year', default=0) or 0), 'term': prop(p, 'Term'),
                         'title': prop(p, 'Course'), 'code': prop(p, 'Code'), 'note': prop(p, 'Note'), 'inst': prop(p, 'Institution')})
    teaching.sort(key=lambda t: (-t['year'], t['term'] != 'Fall', t['title']))

    # ---- Photos
    photos = []
    for i, r in enumerate(sort_rows(visible(query_all(ds['photos']))), 0):
        p = r['properties']; cap = prop(p, 'Caption'); key = f'photo:{i}'
        path = save_image(key, 'photo', prop(p, 'Image File', default=[]) + [prop(p, 'Image URL', 'Image')], label=cap, no_images=no_img)
        if path: A[key] = path; photos.append({'img': key, 'caption': cap})

    # ---- Videos
    videos = []
    for r in sort_rows(visible(query_all(ds['videos']))):
        p = r['properties']; vid = prop(p, 'YouTube ID') or re.sub(r'.*(?:v=|youtu\.be/)([\w-]{11}).*', r'\1', prop(p, 'YouTube URL'))
        key = f'yt:{vid}'
        path = save_image(key, 'social', [f'https://i.ytimg.com/vi/{vid}/mqdefault.jpg'], no_images=no_img)
        if path: A[key] = path
        videos.append({'id': vid, 'title': prop(p, 'Title'), 'url': prop(p, 'YouTube URL') or f'https://www.youtube.com/watch?v={vid}', 'thumb': key})

    # ---- Highlights
    highlights = []
    for i, r in enumerate(sort_rows(visible(query_all(ds['highlights']))), 0):
        p = r['properties']; cap = prop(p, 'Caption'); key = f'slide:{i}'
        path = save_image(key, 'slide', prop(p, 'Image File', default=[]) + [prop(p, 'Image URL', 'Image')], label=cap, no_images=no_img)
        if path: A[key] = path; highlights.append({'img': key, 'caption': cap, 'link': prop(p, 'Link')})

    # ---- Site content (key/value + page bodies)
    SC = {}
    for r in visible(query_all(ds['site_content'])):
        p = r['properties']; k = plain(p, 'Key'); v = plain(p, 'Value')   # plain text: Notion auto-links URLs/emails, which would break hrefs/iframes
        if v.startswith('(') or k in ('home_intro', 'contact_address_en', 'contact_address_ko', 'openings_en', 'openings_ko'):
            body = body_markdown(r['id'])
            if body: v = body
        SC[k] = v
    for key, kind in [('hero', 'hero'), ('collab', 'collab')]:
        src = SC.get({'hero': 'home_hero_image_url', 'collab': 'home_collaborators_image_url'}[key], '')
        path = save_image(key, kind, [SC.get(key + '_file', ''), src], no_images=no_img)
        if path: A[key] = path
    for i, k in enumerate(['youtube_icon_url', 'facebook_icon_url', 'x_icon_url']):
        path = save_image(f'social:{i}', 'social', [SC.get(k, '')], no_images=no_img)
        if path: A[f'social:{i}'] = path
    # research_banner / logo / banner_people / banner_photos: optional Site Content rows "<key>_url" override the snapshot copy
    for key in ('research_banner', 'logo', 'banner_people', 'banner_photos'):
        path = save_image(key, key, [SC.get(key + '_url', '')], no_images=no_img)
        if path: A[key] = path
    socials = [{'name': 'YouTube', 'url': SC.get('youtube_url', ''), 'icon': 'social:0'},
               {'name': 'Facebook', 'url': SC.get('facebook_url', ''), 'icon': 'social:1'},
               {'name': 'X', 'url': SC.get('x_url', ''), 'icon': 'social:2'}]
    site = {
        'lab_name': SC.get('lab_name', 'Machine Learning and Vision Lab'), 'lab_short': SC.get('lab_short_name', 'MLV Lab'),
        'site_url': SC.get('site_url', ''), 'pi_email': SC.get('pi_email', ''), 'apply_email': SC.get('apply_email', ''),
        'scholar': SC.get('google_scholar', ''), 'intro': SC.get('home_intro', '').replace('https://mlv.korea.ac.kr/contact/openings', '#/openings'),
        'address_en': SC.get('contact_address_en', ''), 'address_ko': SC.get('contact_address_ko', ''),
        'map_embed': SC.get('contact_map_embed_url', ''), 'calendar_embed': SC.get('contact_calendar_embed_url', ''),
        'map_link': SC.get('contact_map_link', 'https://www.google.com/maps/search/?api=1&query=KRAFTON+SoC+Building+KAIST'),
        'calendar_link': SC.get('contact_calendar_embed_url', ''),
        'openings_title': SC.get('openings_title', 'Openings'), 'openings_open': SC.get('openings_open', 'true').strip().lower() == 'true',
        'openings_en': SC.get('openings_en', ''), 'openings_ko': SC.get('openings_ko', ''),
        'home_news_count': int(SC.get('home_news_count', '10') or 10),
        'home_new_count': int(SC.get('home_new_count', '5') or 5),   # how many newest News items get the NEW badge
    }
    data = {'site': site, 'people': people, 'pubs': pubs, 'topics': topics, 'news': news, 'teaching': teaching, 'photos': photos,
            'videos': videos, 'socials': socials, 'highlights': highlights, 'generated': time.strftime('%Y-%m-%d %H:%M')}
    BUILD.mkdir(exist_ok=True)
    json.dump(data, open(BUILD / 'site_data.json', 'w'), ensure_ascii=False)
    json.dump(A, open(BUILD / 'assets.json', 'w'), ensure_ascii=False)
    print(f"people {len(people)} · pubs {len(pubs)} · topics {len(topics)} · news {len(news)} · teaching {len(teaching)} · photos {len(photos)} · videos {len(videos)} · highlights {len(highlights)} · images {len(A)}")

if __name__ == '__main__':
    main()
