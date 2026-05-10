"""Build a portable seed of swim-meet data for one-time prod ingestion.

Reads every PDF in ./pdfs_local/, parses each with the current ct_pdf
parser, and writes only the three Data-tab tables (meet_pdf_cache,
meet_pdf_swimmers, meet_pdf_results) into a standalone sqlite file at
./seed/swim_seed.sqlite. Also emits ./seed/swim_seed.sql — INSERT
statements only — for shell-based ingestion in prod.

Usage:
    python3 scripts/build_seed_db.py [--pdfs DIR] [--out DIR] [--force]

Default DIR is pdfs_local/; default OUT is seed/. The script is
idempotent: rerunning with --force wipes the seed first; without
--force, existing rows for a given (ct_meet_id, name_key, event_name,
time) are skipped via INSERT OR IGNORE.

The output files are safe to ship: they contain no roster identity,
no auth tokens, no API keys — just public meet-result data extracted
from publicly-posted Hy-Tek PDFs.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import sqlite3
from datetime import datetime, timezone

# Re-route DB_PATH BEFORE importing db so init_db() targets our seed file.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pdfs', default=os.path.join(_PROJECT_ROOT, 'pdfs_local'),
                    help='Directory containing the PDFs to parse')
    ap.add_argument('--out', default=os.path.join(_PROJECT_ROOT, 'seed'),
                    help='Directory to write swim_seed.sqlite + swim_seed.sql')
    ap.add_argument('--force', action='store_true',
                    help='Delete any existing seed file before building')
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    seed_db = os.path.join(args.out, 'swim_seed.sqlite')
    seed_sql = os.path.join(args.out, 'swim_seed.sql')

    if args.force and os.path.exists(seed_db):
        os.remove(seed_db)
        print(f'wiped existing {seed_db}')

    os.environ['DB_PATH'] = seed_db
    # Now safe to import db — it will use the seed file.
    import db
    import ct_pdf
    db.init_db()
    print(f'seed db initialized at {seed_db}')
    print(f'parser version: {ct_pdf.PARSER_VERSION}')

    pdfs = sorted(f for f in os.listdir(args.pdfs) if f.lower().endswith('.pdf'))
    print(f'found {len(pdfs)} PDFs in {args.pdfs}')

    ok = fail = skipped = 0
    rows_total = 0
    for i, fn in enumerate(pdfs, 1):
        path = os.path.join(args.pdfs, fn)
        with open(path, 'rb') as fh:
            pdf_bytes = fh.read()
        pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()[:16]

        # Skip if we've already ingested this exact PDF (rerun-safe)
        dup = db.lookup_pdf_by_hash(pdf_hash)
        if dup and not args.force:
            skipped += 1
            print(f'[{i:3d}/{len(pdfs)}] - {fn[:60]} -- skip (already in seed)')
            continue

        try:
            rows, diag = ct_pdf.parse_results_pdf(pdf_bytes,
                                                   return_diagnostics=True)
        except Exception as e:
            print(f'[{i:3d}/{len(pdfs)}] x {fn[:60]} -- parse error: {e}')
            fail += 1
            continue

        if not rows:
            print(f'[{i:3d}/{len(pdfs)}] x {fn[:60]} -- 0 rows')
            fail += 1
            continue

        meta = ct_pdf.extract_meet_metadata_from_pdf(pdf_bytes)
        detected_name = meta.get('meet_name') or os.path.splitext(fn)[0]
        detected_start = meta.get('start_date')
        detected_end = meta.get('end_date') or detected_start
        start_iso = detected_start.isoformat() if detected_start else None
        end_iso = detected_end.isoformat() if detected_end else None

        # Same resolution as the upload endpoint: auto-attach by (name, date),
        # otherwise synthesize from hash so the id is stable across rebuilds.
        candidate = db.find_meet_by_name_date(detected_name, start_iso)
        if candidate:
            ct_meet_id = candidate['ct_meet_id']
            # Mode: if a meet was already in seed (from a sibling PDF), append.
            existing_cache = db.get_meet_cache(ct_meet_id)
            mode = ('append' if existing_cache
                    and existing_cache.get('parsed_at') else 'replace')
        else:
            ct_meet_id = f'seed_{pdf_hash[:8]}'
            existing_cache = db.get_meet_cache(ct_meet_id)
            mode = 'replace'

        new_hashes = pdf_hash
        if existing_cache and existing_cache.get('pdf_hashes'):
            existing = [h.strip() for h in existing_cache['pdf_hashes'].split(',')
                        if h.strip()]
            if pdf_hash not in existing:
                new_hashes = ','.join(existing + [pdf_hash])
            else:
                new_hashes = existing_cache['pdf_hashes']

        pdf_url_token = f'seed:{fn}'
        if existing_cache and existing_cache.get('pdf_url') and mode == 'append':
            urls = [u.strip() for u in existing_cache['pdf_url'].split(',')
                    if u.strip()]
            new_pdf_url = (','.join(urls + [pdf_url_token])
                           if pdf_url_token not in urls
                           else existing_cache['pdf_url'])
        else:
            new_pdf_url = pdf_url_token

        diag_blob = json.dumps({'pdfs': [{
            'url': pdf_url_token,
            'rows': len(rows),
            'total_lines': diag['total_lines'],
            'unmatched_sample': diag['unmatched_sample'],
        }]})

        db.save_meet_cache(
            ct_meet_id,
            meet_name=((existing_cache.get('meet_name')
                        if existing_cache and existing_cache.get('meet_name')
                        else detected_name)
                       or 'Seeded meet'),
            start_date=(existing_cache.get('start_date')
                        if existing_cache and existing_cache.get('start_date')
                        else start_iso),
            end_date=(existing_cache.get('end_date')
                      if existing_cache and existing_cache.get('end_date')
                      else end_iso),
            pdf_url=new_pdf_url,
            parsed_at=datetime.now(timezone.utc).isoformat(),
            note=None,
            parser_version=ct_pdf.PARSER_VERSION,
            parse_diagnostics=diag_blob,
            pdf_hashes=new_hashes,
        )
        db.save_meet_pdf_swimmers(ct_meet_id, rows, mode=mode)
        rows_total += len(rows)
        ok += 1
        if i % 25 == 0 or i == len(pdfs):
            print(f'[{i:3d}/{len(pdfs)}] + {fn[:60]} -- {len(rows)} rows '
                  f'(running total: {rows_total:,})')

    print()
    print(f'{ok} PDFs ingested, {skipped} skipped, {fail} failed')
    print(f'{rows_total:,} total swim rows in seed')

    # ===== Emit SQL dump for shell-based ingestion =====
    # We dump ONLY the three Data-tab tables. Prod's roster (team_members,
    # swimmers) is untouched.
    print(f'writing SQL dump to {seed_sql}…')
    with open(seed_sql, 'w') as out:
        out.write('-- swim_seed.sql\n')
        out.write(f'-- generated {datetime.now(timezone.utc).isoformat()}\n')
        out.write(f'-- parser_version {ct_pdf.PARSER_VERSION}\n')
        out.write(f'-- {rows_total:,} swim rows from {ok} PDFs\n')
        out.write('-- Apply with: sqlite3 <prod.db> < swim_seed.sql\n')
        out.write('--\n')
        out.write('-- Idempotent: uses INSERT OR IGNORE so rerunning is safe.\n')
        out.write('-- Does NOT touch team_members, swimmers, or any roster table.\n\n')
        out.write('BEGIN TRANSACTION;\n')
        conn = sqlite3.connect(seed_db)
        conn.row_factory = sqlite3.Row
        for table in ('meet_pdf_cache', 'meet_pdf_swimmers', 'meet_pdf_results'):
            cols_info = conn.execute(f"PRAGMA table_info({table})").fetchall()
            cols = [c['name'] for c in cols_info]
            for r in conn.execute(f"SELECT * FROM {table}"):
                vals = []
                for c in cols:
                    v = r[c]
                    if v is None:
                        vals.append('NULL')
                    elif isinstance(v, (int, float)):
                        vals.append(str(v))
                    else:
                        s = str(v).replace("'", "''")
                        vals.append("'" + s + "'")
                out.write(
                    f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) "
                    f"VALUES ({','.join(vals)});\n"
                )
        out.write('COMMIT;\n')
        conn.close()
    size_mb = os.path.getsize(seed_sql) / (1024 * 1024)
    print(f'wrote {seed_sql} ({size_mb:.1f} MB)')

    # Also compress for upload convenience
    gz_path = seed_sql + '.gz'
    try:
        subprocess.run(['gzip', '-kf', seed_sql], check=True)
        if os.path.exists(gz_path):
            gz_mb = os.path.getsize(gz_path) / (1024 * 1024)
            print(f'wrote {gz_path} ({gz_mb:.1f} MB)')
    except Exception as e:
        print(f'gzip skipped: {e}')

    print()
    print('Next step: see workflow.md → "Seed prod from local data".')


if __name__ == '__main__':
    main()
