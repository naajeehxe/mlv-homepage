#!/usr/bin/env python3
"""
render.py — build/site_data.json + build/assets.json + template.html → site/index.html

  python render.py               # render what fetch_notion.py produced
  python render.py --snapshot    # render the bundled 2026-09 snapshot (no Notion token needed)
"""
import json, shutil, argparse, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFG = json.load(open(HERE / 'config.json'))
OUT = HERE / CFG['output_dir']

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--snapshot', action='store_true'); args = ap.parse_args()
    if args.snapshot:
        data = json.load(open(HERE / 'snapshot' / 'site_data.json'))
        man = json.load(open(HERE / 'snapshot' / 'manifest.json'))
        assets = {}
        for key, rel in man['files'].items():
            src = HERE / 'snapshot' / rel; dst = OUT / rel
            dst.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src, dst)
            assets[key] = rel
    else:
        try:
            data = json.load(open(HERE / 'build' / 'site_data.json'))
            assets = json.load(open(HERE / 'build' / 'assets.json'))
        except FileNotFoundError:
            sys.exit('build/site_data.json not found — run fetch_notion.py first (or use --snapshot).')
    tpl = open(HERE / 'template.html', encoding='utf-8').read()
    j = lambda x: json.dumps(x, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    html = tpl.replace('__ASSETS__', j(assets)).replace('__DATA__', j(data)).replace('__MODE__', 'local')
    head, body = html.split('</style>', 1)
    full = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n' + head + '</style>\n</head>\n<body>\n' + body + '\n</body>\n</html>\n')
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'index.html').write_text(full, encoding='utf-8')
    (OUT / '.nojekyll').write_text('')
    cname = HERE / 'CNAME'
    if cname.exists(): shutil.copyfile(cname, OUT / 'CNAME')
    print('wrote', OUT / 'index.html', f'({len(full)/1e6:.2f} MB html, {len(assets)} images)')

if __name__ == '__main__':
    main()
