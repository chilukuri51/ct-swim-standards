"""Scrape ctswim.org/Meets/Results.aspx for the full meet result PDF index.

Writes data/meet_index.json with {fetched_at, count, rows: [{url, label}]}.

Designed to be run from any non-Cloudflare-blocked context (local Mac,
GitHub Actions runner). The Render production app reads this JSON at
runtime — Render itself can't hit the index page (Cloudflare challenge)
but it can download PDFs directly once it has the URLs.

Usage:
    python scripts/scrape_meet_index.py
"""

import json
import os
import sys
from datetime import datetime, timezone

# Allow running from repo root
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import ct_pdf


def main():
    print('Scraping ctswim.org/Meets/Results.aspx ...')
    rows = ct_pdf.scrape_results_index()
    print(f'  found {len(rows)} PDFs')

    out_path = os.path.join(ROOT, 'data', 'meet_index.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump({
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'count': len(rows),
            'rows': rows,
        }, f, indent=2)
    print(f'  wrote {out_path}')


if __name__ == '__main__':
    main()
