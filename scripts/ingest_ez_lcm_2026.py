"""One-off ingestion: parse the 2026 EZ LCM AG Zone Champs qualifying-time
PDF and produce the standards.json fragment for program 'ez_lcm__ag_2026'.

The PDF lays out two age groups per page in a 4-column block:
    WOMEN_LCM | WOMEN_SCY | EVENT | MEN_SCY | MEN_LCM

Run:
    python scripts/ingest_ez_lcm_2026.py
Prints the JSON fragment to stdout. The committed data/standards.json is
the canonical source at runtime — this script exists to (1) document where
the cuts came from and (2) let us re-ingest if EZ republishes.
"""

import io
import json
import re
import sys

import requests
from pypdf import PdfReader

PDF_URL = (
    'https://www.easternzoneswimming.org/easternzone/UserFiles/Image/'
    'QuickUpload/2026-ez-lc-age-group-zone-champs-qts_040490.pdf'
)

# Event labels in the order they appear in each age-group section.
EVENT_ORDER = {
    '10/Under': [
        '50 Free', '100 Free', '200 Free', '400/500 Free',
        '50 Back', '100 Back',
        '50 Breast', '100 Breast',
        '50 Fly', '100 Fly',
        '200 IM',
    ],
    '11-12': [
        '50 Free', '100 Free', '200 Free', '400/500 Free',
        '50 Back', '100 Back', '200 Back',
        '50 Breast', '100 Breast', '200 Breast',
        '50 Fly', '100 Fly', '200 Fly',
        '200 IM',
    ],
    '13-14': [
        '50 Free', '100 Free', '200 Free', '400/500 Free',
        '800/1000 Free', '1500/1650 Free',
        '100 Back', '200 Back',
        '100 Breast', '200 Breast',
        '100 Fly', '200 Fly',
        '200 IM', '400 IM',
    ],
}

TIME_RE = re.compile(r'\d+:\d+\.\d+|\d+\.\d+|-')


def parse_section_block(lines, age_group):
    """Lines after the 'WOMEN EVENT MEN' header for ONE age group.
    Each event line: WLCM WSCY EVENT MSCY MLCM (4 numeric, with the
    middle word(s) being event name). 13-14 uses '-' for missing events
    (50 back/breast/fly aren't contested at LCM AG)."""
    girls_lcm, girls_scy = [], []
    boys_scy, boys_lcm = [], []
    expected = EVENT_ORDER[age_group]
    idx = 0
    for line in lines:
        if idx >= len(expected):
            break
        nums = TIME_RE.findall(line)
        # An event row has exactly 4 time-or-dash fields.
        if len(nums) != 4:
            continue
        girls_lcm.append(nums[0])
        girls_scy.append(nums[1])
        boys_scy.append(nums[2])
        boys_lcm.append(nums[3])
        idx += 1
    if idx != len(expected):
        raise RuntimeError(
            f'{age_group}: expected {len(expected)} events, got {idx}'
        )
    return {
        'events': expected,
        'girls': {'LCM': girls_lcm, 'SCY': girls_scy},
        'boys': {'LCM': boys_lcm, 'SCY': boys_scy},
    }


def main():
    print(f'Fetching {PDF_URL}', file=sys.stderr)
    body = requests.get(PDF_URL, timeout=30).content
    reader = PdfReader(io.BytesIO(body))
    text = '\n'.join((p.extract_text() or '') for p in reader.pages)
    lines = [ln for ln in text.split('\n') if ln.strip()]

    # Pull out every line that looks like an event row (4 time-or-dash
    # tokens). Skip rows that are ALL dashes — the 13-14 PDF lists 50
    # back/breast/fly as '- - 50 BACK - -' since they aren't contested.
    raw_rows = [(ln, TIME_RE.findall(ln)) for ln in lines]
    event_rows = [
        ln for ln, toks in raw_rows
        if len(toks) == 4 and not all(t == '-' for t in toks)
    ]
    age_order = ['10/Under', '11-12', '13-14']
    expected_total = sum(len(EVENT_ORDER[ag]) for ag in age_order)
    if len(event_rows) != expected_total:
        raise RuntimeError(
            f'expected {expected_total} event rows, got {len(event_rows)}'
        )

    groups = {}
    cursor = 0
    for ag in age_order:
        n = len(EVENT_ORDER[ag])
        groups[ag] = parse_section_block(event_rows[cursor:cursor + n], ag)
        cursor += n

    program = {
        'display_name': '2026 EZ LC AG Zone Champs',
        'subtitle': '2026 LCM (Jul 29 - Aug 1, NOVA Aquatic Center, Richmond VA)',
        'season': '2026',
        'effective_date': '2026-04-26',
        'gender_keys': ['girls', 'boys'],
        'gender_labels': {'girls': 'Girls', 'boys': 'Boys'},
        'meet_info': {
            'name': 'EZ LCM Age Group Zone Summer Champs 2026',
            'dates': '2026-07-29..2026-08-01',
            'venue': 'NOVA Aquatic Center',
            'location': 'Richmond, VA',
            'source_pdf': PDF_URL,
        },
        'groups': groups,
    }
    json.dump({'ez_lcm__ag_2026': program}, sys.stdout, indent=2)
    print()


if __name__ == '__main__':
    main()
