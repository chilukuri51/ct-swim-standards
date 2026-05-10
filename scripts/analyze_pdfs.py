"""Run ct_pdf.parse_results_pdf against every PDF in ./pdfs_local/ and
aggregate parser quality.

Outputs (to ./parser_report/):
  summary.json     — totals across all PDFs
  per_pdf.json     — every PDF's row count, time-coverage, missing fields,
                     unmatched-line samples
  unmatched.txt    — every "looks like a swimmer row but didn't match" line
                     across all PDFs, deduped, sorted by frequency
  no_event.txt     — rows that parsed but have NO event header attached
                     (parser saw the swimmer but didn't know what event)
  no_time.txt      — rows that parsed but have NO time (excluding relay
                     legs and DQ rows, which legitimately have no time)
  failures.txt     — PDFs that crashed the parser

Usage:
    python3 scripts/analyze_pdfs.py                # analyze all in pdfs_local/
    python3 scripts/analyze_pdfs.py --quick        # first 20 only (smoke)
    python3 scripts/analyze_pdfs.py --pdf <path>   # one specific PDF

The aggregated unmatched.txt is the gold mine — it shows you exactly
which row patterns the parser is missing across the entire corpus, so
you can see high-frequency edge cases first.
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ct_pdf  # noqa: E402


PDF_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'pdfs_local')
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'parser_report')


def normalize_unmatched(line: str) -> str:
    """Strip swimmer-specific tokens so similar-shape misses cluster.

    "WRAT-CT 12van Heerden, Hadley R*51 34.18" → "<TEAM>-CT <AGE><name>, <name> <RANK> <TIME>"
    Lets us count miss patterns rather than individual swimmers.
    """
    s = line
    s = re.sub(r'\b[A-Z]{2,6}\s*-CT\b', '<TEAM>-CT', s)
    s = re.sub(r'\b\d{1,2}:\d{2}\.\d{2}\b', '<TIME>', s)
    s = re.sub(r'\b\d{1,2}\.\d{2}\b', '<TIME>', s)
    s = re.sub(r'\b\d{4,}\b', '<NUM>', s)
    s = re.sub(r'\b\d{1,3}\b', '<N>', s)
    s = re.sub(r'\b[A-Z][A-Za-z\'.-]+(?:\s+[A-Z][A-Za-z\'.-]+)*,\s+[A-Z][A-Za-z\'.-]+\b',
               '<LAST>, <FIRST>', s)
    return re.sub(r'\s+', ' ', s).strip()


def analyze_one(path: str) -> dict:
    """Parse one PDF and return per-PDF stats."""
    out = {
        'path': path, 'filename': os.path.basename(path),
        'size_kb': os.path.getsize(path) // 1024 if os.path.exists(path) else 0,
        'rows': 0, 'unique_swimmers': 0,
        'rows_with_event': 0, 'rows_with_time': 0,
        'rows_no_event': 0, 'rows_no_time_non_relay': 0,
        'unmatched_lines': [], 'unique_events': 0,
        'meet_name': '', 'start_date': None,
        'error': None,
    }
    try:
        with open(path, 'rb') as f:
            body = f.read()
    except OSError as e:
        out['error'] = f'read_error: {e}'
        return out

    try:
        meta = ct_pdf.extract_meet_metadata_from_pdf(body)
        out['meet_name'] = meta.get('meet_name') or ''
        out['start_date'] = meta['start_date'].isoformat() if meta.get('start_date') else None
    except Exception as e:
        out['meta_error'] = f'{type(e).__name__}: {e}'

    try:
        rows, diag = ct_pdf.parse_results_pdf(body, return_diagnostics=True)
    except Exception as e:
        out['error'] = f'parse_error: {type(e).__name__}: {e}'
        return out

    out['rows'] = len(rows)
    out['unique_swimmers'] = len({r.get('name_key') for r in rows if r.get('name_key')})
    out['unique_events'] = len({(r.get('distance'), r.get('stroke'), r.get('course'))
                                 for r in rows
                                 if r.get('distance') and r.get('stroke')})
    out['unmatched_lines'] = diag['unmatched_sample']

    for r in rows:
        if r.get('event_name'):
            out['rows_with_event'] += 1
        else:
            out['rows_no_event'] += 1
        if r.get('time'):
            out['rows_with_time'] += 1
        elif r.get('stroke') not in ('FREE_RELAY', 'MEDLEY_RELAY') and r.get('team'):
            # Real (non-relay-leg) row missing a time. Could be a DQ.
            out['rows_no_time_non_relay'] += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true',
                    help='Analyze first 20 PDFs only')
    ap.add_argument('--pdf', help='Analyze a single specific PDF')
    ap.add_argument('--workers', type=int, default=4)
    args = ap.parse_args()

    if args.pdf:
        paths = [args.pdf]
    else:
        if not os.path.isdir(PDF_DIR):
            print(f'no PDFs found at {PDF_DIR}. Run download_all_pdfs.py first.',
                  file=sys.stderr)
            sys.exit(1)
        paths = sorted(os.path.join(PDF_DIR, f) for f in os.listdir(PDF_DIR)
                       if f.endswith('.pdf'))
        if args.quick:
            paths = paths[:20]
    print(f'analyzing {len(paths)} PDFs ...', flush=True)
    os.makedirs(REPORT_DIR, exist_ok=True)

    per_pdf = []
    failures = []
    unmatched_counter = Counter()
    unmatched_examples = defaultdict(list)
    no_event_examples = []
    no_time_examples = []

    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(analyze_one, p): p for p in paths}
        done = 0
        for fut in as_completed(futures):
            done += 1
            r = fut.result()
            per_pdf.append(r)
            if r.get('error'):
                failures.append(r)
                print(f'  [{done:3d}/{len(paths)}] X {r["filename"]} — {r["error"]}',
                      flush=True)
                continue
            for ln in r['unmatched_lines']:
                key = normalize_unmatched(ln)
                unmatched_counter[key] += 1
                if len(unmatched_examples[key]) < 3:
                    unmatched_examples[key].append((r['filename'], ln))
            if r['rows_no_event']:
                no_event_examples.append((r['filename'], r['rows_no_event']))
            if r['rows_no_time_non_relay']:
                no_time_examples.append((r['filename'], r['rows_no_time_non_relay']))
            print(
                f"  [{done:3d}/{len(paths)}] + {r['filename']} — "
                f"{r['rows']} rows, "
                f"event:{r['rows_with_event']}/{r['rows']}, "
                f"time:{r['rows_with_time']}/{r['rows']}, "
                f"unmatched:{len(r['unmatched_lines'])}",
                flush=True
            )

    # ===== Summary =====
    summary = {
        'pdfs_total': len(paths),
        'pdfs_ok': len(paths) - len(failures),
        'pdfs_failed': len(failures),
        'rows_total': sum(p['rows'] for p in per_pdf),
        'rows_with_event': sum(p['rows_with_event'] for p in per_pdf),
        'rows_with_time': sum(p['rows_with_time'] for p in per_pdf),
        'rows_no_event': sum(p['rows_no_event'] for p in per_pdf),
        'rows_no_time_non_relay': sum(p['rows_no_time_non_relay'] for p in per_pdf),
        'unmatched_total': sum(unmatched_counter.values()),
        'unmatched_unique_patterns': len(unmatched_counter),
        'unique_swimmers_total': sum(p['unique_swimmers'] for p in per_pdf),
    }
    if summary['rows_total']:
        summary['event_coverage_pct'] = round(
            100 * summary['rows_with_event'] / summary['rows_total'], 2)
        summary['time_coverage_pct'] = round(
            100 * summary['rows_with_time'] / summary['rows_total'], 2)

    with open(os.path.join(REPORT_DIR, 'summary.json'), 'w') as f:
        json.dump(summary, f, indent=2)
    with open(os.path.join(REPORT_DIR, 'per_pdf.json'), 'w') as f:
        json.dump(per_pdf, f, indent=2)

    # Unmatched.txt: top patterns sorted by frequency
    with open(os.path.join(REPORT_DIR, 'unmatched.txt'), 'w') as f:
        f.write(f'Unmatched-line patterns across {len(paths)} PDFs.\n')
        f.write('Format: <count>x  <normalized pattern>\n')
        f.write('         then up to 3 example lines from real PDFs.\n\n')
        for key, count in unmatched_counter.most_common():
            f.write(f'{count}x  {key}\n')
            for fn, ln in unmatched_examples[key]:
                f.write(f'    [{fn}]  {ln}\n')
            f.write('\n')

    with open(os.path.join(REPORT_DIR, 'no_event.txt'), 'w') as f:
        f.write('PDFs with rows that parsed but have NO event header attached.\n')
        f.write('(Parser found the swimmer but didn\'t know which event.)\n\n')
        for fn, n in sorted(no_event_examples, key=lambda x: -x[1]):
            f.write(f'  {n:5d}  {fn}\n')

    with open(os.path.join(REPORT_DIR, 'no_time.txt'), 'w') as f:
        f.write('PDFs with non-relay rows missing a time.\n')
        f.write('(Includes DQ rows, which legitimately have no time, plus\n')
        f.write(' true parser misses where time extraction failed.)\n\n')
        for fn, n in sorted(no_time_examples, key=lambda x: -x[1]):
            f.write(f'  {n:5d}  {fn}\n')

    with open(os.path.join(REPORT_DIR, 'failures.txt'), 'w') as f:
        f.write(f'PDFs that failed to parse ({len(failures)} total)\n\n')
        for r in failures:
            f.write(f'  {r["filename"]}: {r["error"]}\n')

    print('\n==================== SUMMARY ====================')
    for k, v in summary.items():
        print(f'  {k}: {v}')
    print(f'\nfull reports in {REPORT_DIR}/')
    print(f'  - summary.json: top-level numbers')
    print(f'  - per_pdf.json: per-PDF stats')
    print(f'  - unmatched.txt: aggregated parser misses (start here!)')
    print(f'  - no_event.txt / no_time.txt / failures.txt: edge cases')


if __name__ == '__main__':
    main()
