#!/usr/bin/env python3
"""One-time tool: convert SportsEngine MemberDirectory PDF to data/team_roster.json.

Usage:
    python3 scripts/import_pdf_to_json.py /path/to/MemberDirectory.pdf

Run once. The resulting JSON becomes the project baseline.
"""

import json
import os
import sys

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdf_parser import parse_directory_pdf


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/import_pdf_to_json.py <path-to-pdf>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    if not os.path.exists(pdf_path):
        print(f"File not found: {pdf_path}")
        sys.exit(1)

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(project_root, 'data')
    os.makedirs(data_dir, exist_ok=True)
    out_path = os.path.join(data_dir, 'team_roster.json')

    print(f"Parsing {pdf_path}...")
    result = parse_directory_pdf(pdf_path)

    members = []
    for entry in result['included']:
        members.append({
            'first_name': entry['first_name'],
            'last_name': entry['last_name'],
            'full_name': entry['full_name'],
            'roster': entry['roster'],
            'gender': '',  # to be filled in by coach via UI
            'dob': '',     # to be filled in by coach via UI
        })

    output = {
        'source_pdf': os.path.basename(pdf_path),
        'total_rows_in_pdf': result['total'],
        'included_count': len(members),
        'excluded_count': len(result['excluded']),
        'team_members': members,
        'excluded_preview': result['excluded'][:10],
    }

    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"Wrote {len(members)} team members to {out_path}")
    print(f"Excluded: {result['excluded_count' if 'excluded_count' in result else 'excluded']} rows" if False else f"Excluded: {len(result['excluded'])} rows")


if __name__ == '__main__':
    main()
