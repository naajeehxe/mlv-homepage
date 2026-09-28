#!/usr/bin/env python3
"""collect_thumbs.py — one-off helper, meant to run in GitHub Actions (the runner has internet).

For every publication listed in tools/thumb_requests.json that has no thumbnail yet, find a
representative image, in this order of preference:

  1. the main image of the paper's GitHub README (`readme_img`)
  2. the first figure of the arXiv HTML version (arxiv.org/html/<id>, then ar5iv)
  3. Figure 1 cropped out of the paper PDF — the picture only, caption excluded

Outputs (committed back to the repo by the workflow):
  snapshot/img/pubthumb/<order>.png|jpg   the thumbnail
  tools/thumb_debug/<order>.jpg           page render with the detected crop box (PDF path only)
  tools/thumb_report.json                 what was used for each paper (+ failures)

Usage:  python tools/collect_thumbs.py [--only 5,9,12] [--force]
"""
import io, json, os, re, sys, time, html, argparse
from urllib.parse import urljoin
import requests
from PIL import Image, ImageOps
import pymupdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQ = os.path.join(ROOT, 'tools', 'thumb_requests.json')
OUT = os.path.join(ROOT, 'snapshot', 'img', 'pubthumb')
DBG = os.path.join(ROOT, 'tools', 'thumb_debug')
REPORT = os.path.join(ROOT, 'tools', 'thumb_report.json')
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) mlv-homepage-thumbs/1.0 (+https://naajeehxe.github.io/mlv-homepage/)'}
MAX_W = 1600          # stored width cap (the site builder resizes again)
MIN_W = 240           # anything narrower is not a usable thumbnail


# ----------------------------------------------------------------------------- helpers
def get(url, **kw):
    kw.setdefault('timeout', 90); kw.setdefault('headers', UA); kw.setdefault('allow_redirects', True)
    r = requests.get(url, **kw)
    r.raise_for_status()
    return r


def to_raw_github(url):
    """github.com/<o>/<r>/blob/<b>/<p>  ->  raw.githubusercontent.com/<o>/<r>/<b>/<p>"""
    m = re.match(r'https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)', url)
    return f'https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}/{m.group(4)}' if m else url


def load_image(data):
    im = Image.open(io.BytesIO(data))
    if getattr(im, 'n_frames', 1) > 1:           # gif / animated: first frame
        im.seek(0)
    im = ImageOps.exif_transpose(im)
    if im.mode in ('RGBA', 'LA', 'P'):
        im = im.convert('RGBA')
        bg = Image.new('RGB', im.size, (255, 255, 255)); bg.paste(im, mask=im.split()[-1]); im = bg
    else:
        im = im.convert('RGB')
    return im


def save_thumb(im, order):
    os.makedirs(OUT, exist_ok=True)
    if im.width > MAX_W:
        im = im.resize((MAX_W, round(im.height * MAX_W / im.width)), Image.LANCZOS)
    for ext in ('png', 'jpg'):
        p = os.path.join(OUT, f'{order}.{ext}')
        if os.path.exists(p): os.remove(p)
    buf = io.BytesIO(); im.save(buf, 'PNG', optimize=True)
    if buf.tell() <= 2_500_000:
        path = os.path.join(OUT, f'{order}.png'); open(path, 'wb').write(buf.getvalue())
    else:
        path = os.path.join(OUT, f'{order}.jpg'); im.save(path, 'JPEG', quality=90, optimize=True)
    return os.path.relpath(path, ROOT), im.size


def arxiv_id(url):
    m = re.search(r'arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(?:v\d+)?', url or '')
    return m.group(1) if m else None


def arxiv_search(title):
    """Find an arXiv id by exact-ish title match (for paywalled journals that also have a preprint)."""
    q = re.sub(r'[^A-Za-z0-9 ]+', ' ', title)
    q = ' '.join(w for w in q.split() if len(w) > 2)[:200]
    try:
        time.sleep(3)      # arXiv API politeness
        r = get('https://export.arxiv.org/api/query', params={'search_query': f'ti:"{q}"', 'max_results': 5}, timeout=60)
    except Exception:
        return None
    norm = lambda s: re.sub(r'[^a-z0-9]', '', s.lower())
    for m in re.finditer(r'<entry>(.*?)</entry>', r.text, re.S):
        e = m.group(1)
        t = re.search(r'<title>(.*?)</title>', e, re.S); i = re.search(r'<id>.*?/abs/(\d{4}\.\d{4,5})', e)
        if t and i and norm(html.unescape(t.group(1))) == norm(title):
            return i.group(1)
    return None


# ----------------------------------------------------------------------------- arXiv HTML
FIG_RE = re.compile(r'<figure[^>]*class="[^"]*\bltx_figure\b[^"]*"[^>]*>(.*?)</figure>', re.S | re.I)
IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.I)


def first_html_figure(base_url):
    """Return the URL of the image of the first figure that is a single picture (not a table, not subfigures)."""
    r = get(base_url, timeout=60)
    if r.status_code != 200 or 'ltx_' not in r.text:
        return None, 'no-html'
    for m in FIG_RE.finditer(r.text):
        body = m.group(1)
        if 'ltx_table' in body[:200] and '<table' in body: continue
        imgs = [u for u in IMG_RE.findall(body) if not u.lower().endswith('.svg')]
        if not imgs: continue
        if len(set(imgs)) > 1 or '<figure' in body:      # sub-figures: leave it to the PDF crop, which keeps them together
            return None, 'subfigures'
        return urljoin(r.url, html.unescape(imgs[0])), 'ok'
    return None, 'no-figure'


def first_page_image(url):
    """Project pages (e.g. pages.cs.wisc.edu/~hwkim/projects/...): first reasonably large image on the page."""
    r = get(url, timeout=60)
    if 'text/html' not in r.headers.get('content-type', ''): return None, 'not-html'
    seen = []
    for u in IMG_RE.findall(r.text):
        u = urljoin(r.url, html.unescape(u))
        if u in seen or u.lower().endswith('.svg'): continue
        seen.append(u)
        try:
            im = load_image(get(u, timeout=60).content)
        except Exception:
            continue
        if im.width >= 300 and im.height >= 120 and 0.4 < im.width / im.height < 6:
            return im, u
        if len(seen) > 12: break
    return None, 'no-large-image'


# ----------------------------------------------------------------------------- PDF crop
CAP_RE = re.compile(r'^\s*(Figure|Fig\.?)\s*1\s*[.:|]', re.I)
GAP = 34          # pt: max vertical gap between graphic pieces of the same figure


def find_fig1(doc):
    """Locate Figure 1 in the first pages. Returns (page, crop_rect, caption_rect) or None."""
    for pno in range(min(8, len(doc))):
        page = doc[pno]
        pw, ph = page.rect.width, page.rect.height
        blocks = []
        for b in page.get_text('dict')['blocks']:
            if b['type'] != 0: continue
            spans = [s for l in b['lines'] for s in l['spans']]
            txt = ' '.join(s['text'] for s in spans).strip()
            if not txt: continue
            size = sum(s['size'] for s in spans) / len(spans)
            blocks.append(dict(rect=pymupdf.Rect(b['bbox']), text=txt, size=size, nlines=len(b['lines'])))
        cap = next((b for b in blocks if CAP_RE.match(b['text'])), None)
        if not cap: continue
        cr = cap['rect']
        # text margins of the page
        mx0 = min(b['rect'].x0 for b in blocks); mx1 = max(b['rect'].x1 for b in blocks)
        full = cr.width > 0.55 * pw or (cr.x0 < pw * 0.35 and cr.x1 > pw * 0.65)
        if full:
            x0, x1 = mx0, mx1
        elif (cr.x0 + cr.x1) / 2 < pw / 2:
            x0, x1 = mx0, pw / 2 - 4
        else:
            x0, x1 = pw / 2 + 4, mx1
        col = pymupdf.Rect(x0 - 6, 0, x1 + 6, cr.y0 + 2)
        colw = x1 - x0
        # graphics: embedded images + vector drawings
        gr = [pymupdf.Rect(i['bbox']) for i in page.get_image_info()]
        for d in page.get_drawings():
            r = d['rect']
            if r.width < 0.5 and r.height < 0.5: continue
            if (r.height < 1.2 and r.width > 0.7 * colw) or (r.width < 1.2 and r.height > 120): continue   # rules
            gr.append(r)
        cands = []
        for r in gr:
            if r.y1 > cr.y0 + 3 or r.y0 < 0 or r.width <= 0: continue
            ov = min(r.x1, col.x1) - max(r.x0, col.x0)
            if ov < 0.5 * r.width: continue
            cands.append(r)
        cands.sort(key=lambda r: -r.y1)
        body = [b for b in blocks if b['nlines'] >= 3 and b['rect'].width > 0.6 * colw and b is not cap
                and min(b['rect'].x1, col.x1) - max(b['rect'].x0, col.x0) > 0.5 * b['rect'].width]
        top = cr.y0; cluster = None
        for r in cands:
            if r.y1 < top - GAP: break
            # a paragraph sitting between this piece and the current cluster ends the figure
            if any(bb['rect'].y0 >= r.y1 - 2 and bb['rect'].y1 <= top + 2 for bb in body): break
            cluster = r if cluster is None else cluster | r
            top = min(top, r.y0)
        if cluster is None or cluster.height < 40 or cluster.width < 80:
            # fallback: everything between the previous paragraph and the caption
            above = [bb for bb in body if bb['rect'].y1 <= cr.y0]
            y0 = max((bb['rect'].y1 for bb in above), default=40) + 4
            cluster = pymupdf.Rect(x0, y0, x1, cr.y0 - 2)
            if cluster.height < 40: continue
        # pull in small text pieces that belong to the figure (axis labels, legends)
        for bb in blocks:
            if bb is cap: continue
            br = bb['rect']
            if br.y1 > cr.y0 + 1 or br.y0 < cluster.y0 - 14 or any(bb is x for x in body): continue
            inter = br & cluster
            if (inter.is_valid and inter.get_area() > 0.3 * br.get_area()) or (abs(br.y1 - cluster.y0) < 14 and bb['nlines'] <= 2 and br.width < 0.5 * colw):
                cluster |= br
        cluster = pymupdf.Rect(max(cluster.x0 - 4, 0), max(cluster.y0 - 4, 0), min(cluster.x1 + 4, pw), min(cluster.y1 + 3, cr.y0 - 1))
        return page, cluster, cr
    return None


def crop_pdf(pdf_bytes, order):
    doc = pymupdf.open(stream=pdf_bytes, filetype='pdf')
    hit = find_fig1(doc)
    if not hit: return None, 'no-figure-1-caption-found'
    page, rect, cr = hit
    pix = page.get_pixmap(clip=rect, dpi=220, alpha=False)
    im = Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
    # debug render
    os.makedirs(DBG, exist_ok=True)
    sh = page.new_shape(); sh.draw_rect(rect); sh.finish(color=(1, 0, 0), width=1.5)
    sh.draw_rect(cr); sh.finish(color=(0, 0.5, 1), width=1); sh.commit()
    dp = page.get_pixmap(dpi=55, alpha=False)
    Image.open(io.BytesIO(dp.tobytes('png'))).convert('RGB').save(os.path.join(DBG, f'{order}.jpg'), 'JPEG', quality=70)
    return im, f'page {page.number + 1}, box {[round(v) for v in rect]}'


def pdf_url_for(paper):
    """Turn a landing-page link into a direct PDF link when the pattern is known."""
    u = paper or ''
    a = arxiv_id(u)
    if a: return f'https://arxiv.org/pdf/{a}'
    if 'openaccess.thecvf.com' in u and u.endswith('.html'):
        return u.replace('/html/', '/papers/').replace('.html', '.pdf')
    m = re.match(r'https?://proceedings\.neurips\.cc/paper(?:_files)?/(\d{4})/hash/([0-9a-f]+)-Abstract\.html', u)
    if m: return f'https://proceedings.neurips.cc/paper/{m.group(1)}/file/{m.group(2)}-Paper.pdf'
    m = re.match(r'https?://papers\.nips\.cc/paper/(\d+-[a-z0-9-]+)$', u)
    if m: return f'https://papers.nips.cc/paper/{m.group(1)}.pdf'
    if 'openreview.net/pdf' in u or u.lower().endswith('.pdf'): return u
    return None


# ----------------------------------------------------------------------------- main
def process(p, force=False):
    order = p['order']; rep = {'order': order, 'pid': p.get('pid'), 'title': p['title']}
    if not force:
        for ext in ('png', 'jpg'):
            if os.path.exists(os.path.join(OUT, f'{order}.{ext}')):
                rep.update(status='kept', file=f'snapshot/img/pubthumb/{order}.{ext}'); return rep
    tried = []
    # 1) GitHub README image
    if p.get('readme_img'):
        u = to_raw_github(p['readme_img'])
        try:
            im = load_image(get(u).content)
            if im.width >= MIN_W:
                f, size = save_thumb(im, order); rep.update(status='ok', source='github-readme', url=u, file=f, size=size); return rep
            tried.append(f'readme image too small {im.size}')
        except Exception as e:
            tried.append(f'readme: {e}')
    # 2) arXiv HTML first figure
    a = arxiv_id(p.get('paper')) or arxiv_search(p['title'])
    if a:
        rep['arxiv'] = a
        for base in (f'https://arxiv.org/html/{a}', f'https://ar5iv.labs.arxiv.org/html/{a}'):
            try:
                u, why = first_html_figure(base)
                if u:
                    im = load_image(get(u).content)
                    if im.width >= MIN_W:
                        f, size = save_thumb(im, order); rep.update(status='ok', source='arxiv-html', url=u, file=f, size=size); return rep
                    tried.append(f'{base}: image too small {im.size}')
                else:
                    tried.append(f'{base}: {why}')
            except Exception as e:
                tried.append(f'{base}: {e}')
    # 3) Figure 1 cropped from the PDF
    pu = pdf_url_for(p.get('paper')) or (f'https://arxiv.org/pdf/{a}' if a else None)
    if pu:
        try:
            r = get(pu)
            if r.content[:5] == b'%PDF-':
                im, why = crop_pdf(r.content, order)
                if im is not None and im.width >= MIN_W:
                    f, size = save_thumb(im, order); rep.update(status='ok', source='pdf-crop', url=pu, file=f, size=size, crop=why); return rep
                tried.append(f'pdf: {why if im is None else "crop too small"}')
            else:
                tried.append(f'pdf: not a PDF ({r.headers.get("content-type")})')
        except Exception as e:
            tried.append(f'pdf: {e}')
    # 4) project page: first large image (old pages.cs.wisc.edu project pages)
    pp = p.get('paper') or ''
    if pp and not pu and not arxiv_id(pp):
        try:
            im, why = first_page_image(pp)
            if im is not None:
                f, size = save_thumb(im, order); rep.update(status='ok', source='project-page', url=why, file=f, size=size); return rep
            tried.append(f'page: {why}')
        except Exception as e:
            tried.append(f'page: {e}')
    rep.update(status='none', tried=tried); return rep


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--only', default=''); ap.add_argument('--force', action='store_true')
    args = ap.parse_args()
    reqs = json.load(open(REQ))
    only = {int(x) for x in args.only.split(',') if x.strip()}
    if only: reqs = [p for p in reqs if p['order'] in only]
    report = json.load(open(REPORT)) if os.path.exists(REPORT) else {}
    for p in reqs:
        t = time.time(); rep = process(p, force=args.force); rep['secs'] = round(time.time() - t, 1)
        if rep['status'] == 'ok': time.sleep(1.0)      # be gentle with arxiv.org
        report[str(p['order'])] = rep
        print(f"[{p['order']:>3}] {rep['status']:<5} {rep.get('source', '')!s:<14} {rep.get('size', '')!s:<12} {p['title'][:70]}", flush=True)
        if rep['status'] == 'none': print('       ' + ' | '.join(rep['tried']), flush=True)
        json.dump(report, open(REPORT, 'w'), indent=1, ensure_ascii=False)
    n = sum(1 for r in report.values() if r['status'] in ('ok', 'kept'))
    print(f'\n{n}/{len(report)} thumbnails available')


if __name__ == '__main__':
    main()
