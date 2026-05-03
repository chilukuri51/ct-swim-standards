"""Parse SportsEngine MemberDirectory PDF to extract swimmer roster.

Columns: Account Name | Member Name | Preferred | Roster | Address | Email | Phone

We use:
- Column 2 (Member Name)  -> swimmer name in "Last, First" format
- Column 4 (Roster)       -> training group, e.g. "Breakers 1", "Seniors", "Waves"

Excluded rows:
- Roster is blank
- Roster is "Admin" or "Coach"
"""

import re
import pdfplumber

EXCLUDE_ROSTERS = {'admin', 'coach', ''}


def normalize_roster(roster: str) -> str:
    """Reinsert spaces stripped by pdfplumber, e.g. 'Breakers1' -> 'Breakers 1'."""
    if not roster:
        return ''
    s = roster.strip()
    # Add space between letters and digits
    s = re.sub(r'([A-Za-z])(\d)', r'\1 \2', s)
    return s


def normalize_for_match(text: str) -> str:
    """Lowercase + strip non-alphanumeric for fuzzy matching against CT Swim's name format."""
    return re.sub(r'[^a-z0-9]', '', text.lower())


def _add_camelcase_spaces(s: str) -> str:
    """Insert space between lowercase->uppercase boundaries: 'ThokalaVenkata' -> 'Thokala Venkata'."""
    return re.sub(r'([a-z])([A-Z])', r'\1 \2', s)


def parse_member_name(raw: str) -> dict | None:
    """Parse 'Last, First' (possibly with newlines) into structured form.

    Returns:
      dict with first_name, last_name, full_name, search_key, match_key
      or None if unparseable
    """
    if not raw:
        return None
    cleaned = raw.replace('\n', ' ').strip()
    if ',' not in cleaned:
        return None
    parts = cleaned.split(',', 1)
    last = parts[0].strip()
    first = parts[1].strip()
    if not last or not first:
        return None

    last = _add_camelcase_spaces(last)
    first = _add_camelcase_spaces(first)

    # Use the first word of last name for CT Swim search (handles compound surnames)
    search_key = last.split()[0] if last else ''
    full_name = f'{first} {last}'

    return {
        'first_name': first,
        'last_name': last,
        'full_name': full_name,
        'search_key': search_key,
        'match_key': normalize_for_match(full_name),
    }


def parse_directory_pdf(file_path: str) -> dict:
    """Parse a SportsEngine MemberDirectory PDF.

    Returns:
      {
        'included': [ { name fields..., roster, raw_name, raw_roster } ],
        'excluded': [ { raw_name, raw_roster, reason } ],
        'total':    int
      }
    """
    included = []
    excluded = []
    total = 0

    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table or len(table) < 2:
                    continue
                header = [str(c or '').strip() for c in table[0]]
                # Sanity check: header should mention Member Name and Roster
                header_text = ' '.join(header).lower()
                if 'member' not in header_text or 'roster' not in header_text:
                    continue

                for row in table[1:]:
                    if not row or len(row) < 4:
                        continue
                    total += 1

                    raw_name = (row[1] or '').strip()
                    raw_roster = (row[3] or '').strip()
                    roster = normalize_roster(raw_roster)

                    # Filter out blank/Admin/Coach rosters
                    if roster.lower() in EXCLUDE_ROSTERS:
                        excluded.append({
                            'raw_name': raw_name,
                            'raw_roster': raw_roster,
                            'reason': 'Empty roster' if not roster else f'Excluded: {roster}',
                        })
                        continue

                    parsed = parse_member_name(raw_name)
                    if not parsed:
                        excluded.append({
                            'raw_name': raw_name,
                            'raw_roster': raw_roster,
                            'reason': 'Could not parse member name',
                        })
                        continue

                    parsed['roster'] = roster
                    parsed['raw_name'] = raw_name
                    parsed['raw_roster'] = raw_roster
                    included.append(parsed)

    return {'included': included, 'excluded': excluded, 'total': total}


if __name__ == '__main__':
    # Quick CLI test
    import sys, json
    if len(sys.argv) > 1:
        result = parse_directory_pdf(sys.argv[1])
        print(json.dumps({
            'total': result['total'],
            'included_count': len(result['included']),
            'excluded_count': len(result['excluded']),
            'sample_included': result['included'][:3],
            'sample_excluded': result['excluded'][:3],
        }, indent=2))
