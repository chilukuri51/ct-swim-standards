"""Download every CT Swim meet result PDF listed in data/meet_index.json
(or live-scraped from Results.aspx) into ./pdfs_local/.

Filenames: YYYYMMDD_<meet-slug>_<basename>.pdf — chronological + human-
readable, so you can spot-check by date and the original Hy-Tek
filename is preserved for cross-referencing.

Skips already-downloaded files. Resumes safely after Ctrl-C. Polite
~0.5s delay between downloads to be nice to ctswim.org.

Usage:
    python3 scripts/download_all_pdfs.py            # uses committed index
    python3 scripts/download_all_pdfs.py --live     # live-scrape index first
    python3 scripts/download_all_pdfs.py --limit 10 # download first N only
"""

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Make the project importable so we can reuse ct_pdf.download_pdf
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ct_pdf  # noqa: E402


OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'pdfs_local')
INDEX_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'data', 'meet_index.json')


def slugify(s: str, max_len: int = 40) -> str:
    """Turn 'CDOG YMCA State Champs' → 'cdog_ymca_state_champs'."""
    s = (s or '').lower()
    s = re.sub(r"[^a-z0-9]+", '_', s).strip('_')
    return s[:max_len] or 'meet'


def normalize_iso(d: str) -> str:
    """'2026-01-30' → '20260130' for sortable filenames."""
    if not d:
        return '00000000'
    return re.sub(r'[^0-9]', '', d)[:8] or '00000000'


def filename_for(row: dict) -> str:
    """Build a stable, human-readable filename from a meet_index row."""
    date_part = normalize_iso(row.get('start_date', ''))
    slug = slugify(row.get('meet_name', ''))
    url = row.get('url', '')
    base = os.path.basename(url) or 'unknown.pdf'
    base = re.sub(r'[^a-zA-Z0-9._-]', '_', base)
    if not base.lower().endswith('.pdf'):
        base += '.pdf'
    return f"{date_part}_{slug}_{base}"


def load_index(live: bool = False) -> list:
    if live:
        print('Live-scraping CT Swim Results.aspx ...', flush=True)
        rows = ct_pdf.scrape_results_index()
        print(f'  scraped {len(rows)} rows')
        return rows
    with open(INDEX_PATH) as f:
        return json.load(f).get('rows', [])


def download_one(row: dict, out_path: str) -> tuple:
    """Returns (status, message). Status: 'ok' | 'skip' | 'fail'."""
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return ('skip', 'already downloaded')
    body = ct_pdf.download_pdf(row['url'])
    if not body:
        return ('fail', 'download_failed')
    if not body.startswith(b'%PDF'):
        return ('fail', f'not a PDF (first bytes: {body[:8]!r})')
    tmp = out_path + '.partial'
    with open(tmp, 'wb') as f:
        f.write(body)
    os.replace(tmp, out_path)
    return ('ok', f'{len(body)} bytes')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--live', action='store_true',
                    help='Live-scrape Results.aspx instead of using committed index')
    ap.add_argument('--limit', type=int, default=0,
                    help='Stop after N successful downloads (0 = no limit)')
    ap.add_argument('--workers', type=int, default=4,
                    help='Parallel download workers (default 4)')
    args = ap.parse_args()

    rows = load_index(live=args.live)
    print(f'index: {len(rows)} meets', flush=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    # Build the work list: (row, out_path) for rows with a url
    todo = []
    for row in rows:
        if not row.get('url'):
            continue
        out = os.path.join(OUT_DIR, filename_for(row))
        todo.append((row, out))

    print(f'plan:  {len(todo)} downloads → {OUT_DIR}', flush=True)
    n_ok = n_skip = n_fail = 0
    failures = []

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(download_one, r, p): (r, p) for r, p in todo}
        done = 0
        for fut in as_completed(futures):
            row, path = futures[fut]
            done += 1
            try:
                status, msg = fut.result()
            except Exception as e:
                status, msg = 'fail', f'{type(e).__name__}: {e}'
            if status == 'ok':
                n_ok += 1
            elif status == 'skip':
                n_skip += 1
            else:
                n_fail += 1
                failures.append((path, msg))
            mark = {'ok': '+', 'skip': '.', 'fail': 'x'}[status]
            print(f"  [{done:3d}/{len(todo)}] {mark} {os.path.basename(path)} — {msg}",
                  flush=True)
            if args.limit and n_ok >= args.limit:
                # Cancel remaining
                for f2 in futures:
                    f2.cancel()
                break

    print(f'\ndone: ok={n_ok} skip={n_skip} fail={n_fail}')
    if failures:
        print('\nfailures:')
        for path, msg in failures[:20]:
            print(f'  {os.path.basename(path)}: {msg}')
        if len(failures) > 20:
            print(f'  ... +{len(failures) - 20} more')


if __name__ == '__main__':
    main()
