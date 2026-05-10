"""CT Swim meet result PDFs — index scraper, PDF parser, swimmer lookup.

Replaces the swimstandards.com triangulation source. Works entirely from
public CT Swim data: the meet results index at ctswim.org/Meets/Results.aspx
and the per-meet PDF artifacts.

Three responsibilities:
1. Scrape Results.aspx pages → list of (pdf_url, label) entries.
2. Parse a single Hy-Tek-format result PDF → list of swimmer rows.
3. Match a meet (name + date range) to its PDF in the index.
"""

import io
import re
from datetime import date, timedelta
from typing import Optional

import requests
from pypdf import PdfReader
from bs4 import BeautifulSoup


CT_SWIM_BASE = 'https://www.ctswim.org'
RESULTS_INDEX = f'{CT_SWIM_BASE}/Meets/Results.aspx'
INDEX_MAX_PAGES = 50  # generous upper bound; loop stops on first empty page

# Bump every time parse_results_pdf changes its row-recognition logic so
# meet_pdf_cache rows produced by older code are auto-invalidated and
# re-parsed on the next age-fill run. No admin reset needed.
#   v1: original Format A only (LEHY/Hy-Tek)
#   v2: + Format B (older NCA-style PDFs)
#   v3: + spaced-and-jammed handling, gender F/M normalization
#   v4: + middle initial in Format A (Harper M43)
#   v5: + DQ/--- anchor, Format C (champs no -CT, First Last), relay legs
#   v6: matcher only (no row-shape change); bump kept for clarity, not
#       strictly required since v6 doesn't alter parse_results_pdf output.
#   v7: + per-row event/distance/stroke/course/time capture (Data tab)
PARSER_VERSION = 7

USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
)


def normalize_name(first: str, last: str) -> str:
    """Canonical key for matching across CT Swim and PDF formats.

    Takes only the FIRST WORD of `first` so middle names don't break the
    match — CT Swim sometimes registers swimmers as 'Harper Grace' but
    coaches type just 'Harper' in the roster. PDF parser may extract
    'Harper Grace' as a multi-word first; normalizing both sides to
    'harper' makes them match.

    Last name keeps the full form (hyphenated names like 'Mejia-Arroyo'
    should match exactly).

    'Harper Grace' + 'Wetmore' → 'wetmore_harper'
    'Saanvi' + 'Chilukuri' → 'chilukuri_saanvi'
    'Ava Rose' + 'Finefrock' → 'finefrock_ava'
    """
    first_word = (first or '').strip().split()[0] if (first or '').strip() else ''
    f = re.sub(r"[^a-z]", "", first_word.lower())
    l = re.sub(r"[^a-z]", "", (last or '').lower())
    return f"{l}_{f}"


# ===== Index scraping =====

_PDF_LINK_RE = re.compile(
    r'_results\.pdf$', re.IGNORECASE
)
# Each Results.aspx <tr> follows roughly this shape:
#   "OAK Sanctioned S25-47 1/3/2026- 1/4/2026 OAK New Year's Splash …venue…"
# We parse: date range, then meet name = everything between the dates and
# the "Events Import File" / venue marker.
_TR_DATE_RANGE_RE = re.compile(
    r'(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})'
)
_TR_SINGLE_DATE_RE = re.compile(r'(\d{1,2}/\d{1,2}/\d{4})')
# Markers that typically follow the meet name in a tr row
_TR_TAIL_MARKERS = (
    'Events Import File',
    'Meet Announcement',
    'Updated ',
    'FULLY SUBSCRIBED',
)


def _extract_meet_from_tr(text: str) -> dict:
    """Parse a Results.aspx tr's full text into structured meet info.
    Returns {start_date, end_date, meet_name} or empty dict if dates absent.
    """
    out = {}
    drm = _TR_DATE_RANGE_RE.search(text)
    if drm:
        sd = parse_us_date(drm.group(1))
        ed = parse_us_date(drm.group(2))
        after = text[drm.end():].strip()
    else:
        sdm = _TR_SINGLE_DATE_RE.search(text)
        if not sdm:
            return out
        sd = parse_us_date(sdm.group(1))
        ed = sd
        after = text[sdm.end():].strip()
    # Trim meet name at first tail marker
    for marker in _TR_TAIL_MARKERS:
        idx = after.find(marker)
        if idx > 0:
            after = after[:idx].strip()
    # Strip trailing dash/comma fragments
    after = re.sub(r'[-,;:]\s*$', '', after).strip()
    out = {'start_date': sd, 'end_date': ed, 'meet_name': after}
    return out


def scrape_results_index(max_pages: int = INDEX_MAX_PAGES) -> list[dict]:
    """Scrape every page of CT Swim's Results.aspx, walking each <tr> to
    capture the meet name + date range alongside the result PDF URL.
    Returns list of {url, label, meet_name, start_date, end_date} where
    dates are ISO strings (or None) — the richer data lets find_pdf_for_meet
    fuzzy-match by name when our SwimmerAtMeet meet_name differs slightly
    from the Results.aspx wording but the dates align.
    Stops early when a page yields zero PDFs (we've walked off the end).

    NOTE: the bare URL (no query string) is the implicit "newest meets"
    page; ?page=1 is actually the SECOND page. Iterating ?page=1..N alone
    silently skipped the most recent ~50 meets, including any meet
    published in the last few weeks. The loop below fetches the bare URL
    first, then ?page=1..max_pages."""
    out = []
    seen_urls = set()
    page_urls = [RESULTS_INDEX] + [f'{RESULTS_INDEX}?page={p}' for p in range(1, max_pages + 1)]
    for page_url in page_urls:
        try:
            r = requests.get(
                page_url,
                timeout=15,
                headers={'User-Agent': USER_AGENT}
            )
            if r.status_code != 200:
                break
        except Exception:
            break

        soup = BeautifulSoup(r.text, 'html.parser')
        page_added = 0
        for tr in soup.find_all('tr'):
            # Collect EVERY PDF link in this row. CT publishes sibling PDFs
            # for one meet (Senior + Age Group + Distance + Relays) as
            # separate links in the same row — so capturing only the first
            # would silently drop swimmers whose age group lives in PDF #2/3.
            pdf_anchors = [
                a for a in tr.find_all('a', href=True)
                if _PDF_LINK_RE.search(a['href'])
            ]
            if not pdf_anchors:
                continue
            text = tr.get_text(' ', strip=True)
            info = _extract_meet_from_tr(text)
            for pdf_anchor in pdf_anchors:
                url = pdf_anchor['href'].strip()
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                entry = {
                    'url': url,
                    'label': pdf_anchor.get_text(strip=True),
                    'meet_name': info.get('meet_name', ''),
                    'start_date': info['start_date'].isoformat() if info.get('start_date') else None,
                    'end_date': info['end_date'].isoformat() if info.get('end_date') else None,
                }
                out.append(entry)
                page_added += 1
        if page_added == 0:
            break
    return out


# ===== Meet → PDF matching =====

_DATE_RE = re.compile(r'(\d{1,2})/(\d{1,2})/(\d{4})')


def parse_us_date(s: str) -> Optional[date]:
    if not s:
        return None
    m = _DATE_RE.search(s)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


_NAME_STOP = {
    'and', 'the', 'for', 'sat', 'sun', 'fri', 'tues', 'wed', 'thur', 'mon',
    'sat-sun', 'sat-s', 'fri-sun', 'fri-sat', 'open', 'meet', 'swim',
    'swimming', 'ct', 'champ', 'champs', 'championship', 'championships',
    'champions', 'invitational', 'sanctioned', 'updated', 'event', 'events',
    'import', 'file', 'pool', 'natatorium', 'aquatic', 'center',
}


def _name_tokens(meet_name: str) -> set:
    """Extract distinctive lowercase keywords from a meet name."""
    words = re.findall(r"[A-Za-z][A-Za-z'.-]{2,}", (meet_name or '').lower())
    return {w for w in words if w not in _NAME_STOP}


def _dates_overlap(a_lo, a_hi, b_lo, b_hi) -> bool:
    """True if two inclusive date ranges share at least one day."""
    if not (a_lo and a_hi and b_lo and b_hi):
        return False
    return a_lo <= b_hi and b_lo <= a_hi


def _to_date(s):
    """Parse an ISO 'YYYY-MM-DD' string back to a date (helpers stored in JSON)."""
    if not s:
        return None
    if isinstance(s, date):
        return s
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


_CLUB_CODE_RE = re.compile(r'\b([A-Z]{3,5})\b')
# Two-letter "CT" appears in every meet name — exclude. Other generic codes
# we don't want to weight as host-club hints.
_NOT_CLUB_CODE = {'CT', 'USA', 'YMCA', 'AGC', 'AAA', 'AA'}


def _club_codes(meet_name: str) -> set:
    """Pull host-club codes (uppercase 3-5 letter abbreviations) from a
    meet name. e.g. "2025 CT LEHY 12U Regional" -> {'lehy'}.
    Excludes generic codes like CT/USA/YMCA."""
    return {m.group(1).lower() for m in _CLUB_CODE_RE.finditer(meet_name or '')
            if m.group(1) not in _NOT_CLUB_CODE}


def find_pdfs_for_meet(
    meet_name: str,
    start_date: Optional[date],
    end_date: Optional[date],
    index: list[dict],
    min_score: float = 0.5,
    max_results: int = 6,
    date_tolerance_days: int = 2,
) -> list[dict]:
    """Resolve a meet to ALL plausible result PDFs (a meet often publishes
    several — Senior, Age Group, Distance, Relays — each with a different
    swimmer subset). Returns candidates sorted best-first.

    Algorithm:
      1. Filter index entries whose date range overlaps the target ±2 days
         (fast.ctswim.org and www.ctswim.org/Meets/Results.aspx sometimes
         disagree by a day for the same meet). URL-embedded MMDDYY dates
         are also accepted as a date match.
      2. Score each candidate:
           - host-club code shared: +3 (and +2 more if club code is in URL)
           - name token shared (intersection): +1
           - meet-name token appears as substring of URL filename: +0.5
             (e.g. fast says "WHAT Wesleyan Winter" and the URL is
             '...121224what_results.pdf' — 'what' lives in the filename)
      3. If only one candidate matches the date window, accept it
         regardless of score.
      4. If multiple candidates, keep every one scoring ≥ min_score (default
         0.5 — generous because the date filter is the strong signal). Tied
         scores keep all; they're sibling PDFs for the same meet.
    """
    if not index:
        return []
    target_lo = start_date
    target_hi = end_date or start_date
    target_clubs = _club_codes(meet_name)
    target_tokens = _name_tokens(meet_name) - target_clubs
    target_dates_str = []
    if target_lo:
        target_dates_str.append(target_lo.strftime('%m%d%y'))
    if target_hi and target_hi != target_lo:
        target_dates_str.append(target_hi.strftime('%m%d%y'))

    # Apply ±N day tolerance to absorb fast vs Results.aspx date drift.
    if target_lo:
        target_lo_fuzzy = target_lo - timedelta(days=date_tolerance_days)
        target_hi_fuzzy = target_hi + timedelta(days=date_tolerance_days)
    else:
        target_lo_fuzzy = target_hi_fuzzy = None

    candidates = []
    for p in index:
        cand_lo = _to_date(p.get('start_date'))
        cand_hi = _to_date(p.get('end_date')) or cand_lo
        date_match = bool(
            cand_lo and target_lo_fuzzy and
            _dates_overlap(target_lo_fuzzy, target_hi_fuzzy, cand_lo, cand_hi)
        )
        if not date_match:
            for ds in target_dates_str:
                if ds in p['url']:
                    date_match = True
                    break
        if date_match:
            candidates.append(p)

    if not candidates:
        return []
    if len(candidates) == 1:
        return [candidates[0]]

    scored = []
    for p in candidates:
        cand_name = (p.get('meet_name', '') + ' ' + p.get('label', ''))
        cand_clubs = _club_codes(cand_name) | (
            {tok for tok in re.findall(r'[a-z]{3,5}', p['url'].lower())
             if tok not in _NAME_STOP}
        )
        cand_tokens = _name_tokens(cand_name) - cand_clubs
        url_lc = p['url'].lower()
        # Last segment of URL = filename, often contains the meet abbreviation
        # even when the Results.aspx label/meet_name is generic.
        url_filename = url_lc.rsplit('/', 1)[-1]

        score = 0.0
        for c in target_clubs:
            if c in cand_clubs:
                score += 3
                if c in url_lc:
                    score += 2
            elif c in url_filename:
                # Club code missing from index name/label but present in URL
                # filename — strong signal (e.g. 'lehy' in 'lehy_results.pdf').
                score += 2
        score += len(target_tokens & cand_tokens)
        # Fuzzy substring match for any meet-name token in the URL filename.
        # Discounts: 0.5 each, capped to keep this a tiebreaker not a primary.
        substr_hits = sum(
            0.5 for t in target_tokens
            if len(t) >= 4 and t in url_filename
        )
        score += min(substr_hits, 2.0)
        scored.append((score, p))

    scored.sort(key=lambda t: -t[0])
    top_score = scored[0][0]
    if top_score < min_score:
        return []
    # Keep every candidate at top_score (siblings for the same meet); plus
    # any other candidate with score within 1.5 of top (close sibling PDFs).
    out = [p for s, p in scored if s >= max(min_score, top_score - 1.5)]
    return out[:max_results]


def find_pdf_for_meet(
    meet_name: str,
    start_date: Optional[date],
    end_date: Optional[date],
    index: list[dict],
    min_score: float = 1.0,
) -> Optional[dict]:
    """Backward-compat wrapper: returns the highest-scoring PDF only."""
    pdfs = find_pdfs_for_meet(meet_name, start_date, end_date, index, min_score, max_results=1)
    return pdfs[0] if pdfs else None


# ===== PDF parsing =====

# CT meet result PDFs come in (at least) two formats, depending on the
# Hy-Tek version + how pypdf reorders columns:
#
# Format A — newer LC Regional / SC Champ meets (LEHY 2025):
#   "HHAC-CT 10Destefano, Mackenzie1 36.12   7"
#   = TEAM-CT AGE Last, First<rank> Time [points]
#
# Format B — older / dual-meet style (NCA 2023):
#   "IVY -CT  73 24.46Chilukuri, Saanvi"
#   "NCA-CT  81 17.31Ensling, Delaney M"
#   = TEAM-CT AGE<rank> Time Last, First [middle-initial]
#
# Both have TEAM-CT and "Last, First" — only the position of the time/
# rank differs. We try Format A first (it has the cleaner anchor of
# `,\s+First\d`), then Format B for lines that didn't match.
_PDF_ROW_A_RE = re.compile(
    # pypdf renders the same PDF differently across environments:
    # - Locally (Mac): age and last name jammed → "10Destefano"
    # - Render (Linux): age and last name spaced → "10 Destefano"
    # - 1-digit rank jammed to first name → "Mackenzie1"
    # - 2-digit rank spaced from first name → "Trinity 10"
    # - Tied placings get '*' prefix → "Emma *14"
    # - Multi-word first names exist → "Ava Rose"
    # - Middle initial appears between first name and rank → "Harper M43"
    # - DQ rows have '---' or 'DQ' in place of a numeric rank
    # \s* between fields handles both spacings; \*? handles tie markers.
    r"\b([A-Z]{2,6})\s*-CT\s+"                                # team
    r"(\d{1,2})\s*"                                           # age + opt whitespace
    r"([A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+)*),\s+"       # last (multi-word)
    r"([A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+)*)"           # first (multi-word)
    r"(?:\s+[A-Z])?"                                          # optional middle initial
    r"(?:\s*\*?\d|\s*---|\s+(?:DQ|NS|DFS|SCR)\b)"             # rank-digit OR DQ-style anchor
)
# Format B: name comes AFTER time (older NCA-style PDFs). Same spacing
# flexibility as Format A.
_PDF_ROW_B_RE = re.compile(
    r"\b([A-Z]{2,6})\s*-CT\s+"                                # team
    r"(\d{1,2})\s*"                                           # age + opt space
    r"\d{1,2}\s+"                                             # rank
    r"(?:\d+:\d+\.\d+|\d+\.\d+|---|DQ|NS|DFS|SCR)\s*"         # time / status
    r"([A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+)*),\s+"       # last
    r"([A-Z][A-Za-z'.-]+(?:\s+[A-Z](?:\s|$))?)"               # first + opt initial
)
# Format C: championship layout (no -CT, name is "First Last" with NO
# comma, fields jammed). E.g. "SSAC  9Cassidy Schatz1 2:38.68 AGC".
# Anchored at line start to avoid matching mid-paragraph text.
_PDF_ROW_C_RE = re.compile(
    r"^([A-Z]{2,6})"                                          # team (no -CT suffix)
    r"\s+(\d{1,2})\s*"                                        # age, then jammed-or-spaced
    r"([A-Z][A-Za-z'.-]+)"                                    # first (one word)
    r"\s+"                                                     # required space
    r"([A-Z][A-Za-z'.-]+(?:[A-Za-z'.-]*))"                    # last (single word, may be hyphenated)
    r"\s*\*?\d+\s+"                                           # rank
    r"(?:\d+:\d+\.\d+|\d+\.\d+|---|DQ|NS|DFS|SCR)"            # time / status
)
# Format D (relay leg): "1) Last, First [MidInit] Age". Each relay row
# lists 4 swimmers; finditer captures all of them. Gender comes from the
# event header above (Boys/Girls relay).
_PDF_RELAY_RE = re.compile(
    r"\d+\)\s+"                                                # leg number
    r"([A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+)*),\s+"       # last
    r"([A-Z][A-Za-z'.-]+)"                                    # first
    r"(?:\s+[A-Z])?"                                          # opt middle initial
    r"\s+(\d{1,2})\b"                                         # age
)
_GENDER_HEADER_RE = re.compile(
    r"\b(Girls|Boys|Women|Men|Mixed)\s+\d", re.IGNORECASE
)
# Map Hy-Tek event-section labels to the F/M values team_members.gender
# uses. 'Men' specifically mapped to M (not the parser stripping it to 'M'
# was producing 'M' for women swimmers when their row got mis-attributed
# to a Men's event header, which then overwrote a correct earlier 'F').
_GENDER_TO_FM = {'GIRLS': 'F', 'WOMEN': 'F', 'BOYS': 'M', 'MEN': 'M'}

# Full event header — captures distance, course, stroke alongside gender.
# Examples:
#   "Girls 8 & Under 25 Yard Freestyle"
#   "Boys 13-14 200 Meter Backstroke"
#   "(Girls 8 & Under 25 Yard Backstroke)"  ← continuation page
#   "Mixed 11-12 200 Yard Medley Relay"
_EVENT_HEADER_RE = re.compile(
    r"\(?\s*(Girls|Boys|Women|Men|Mixed)\s+"
    r"(?:\d+\s*&\s*Under|\d+\s*&\s*Over|\d+\s*-\s*\d+|\d+)\s+"
    r"(\d+)\s+(Yard|Meter)\s+"
    r"(Freestyle|Backstroke|Breaststroke|Butterfly|"
    r"Individual\s+Medley|IM|"
    r"Free\s+Relay|Medley\s+Relay)\b",
    re.IGNORECASE
)
_STROKE_TO_CODE = {
    'FREESTYLE': 'FREE', 'BACKSTROKE': 'BACK', 'BREASTSTROKE': 'BREAST',
    'BUTTERFLY': 'FLY', 'IM': 'IM', 'INDIVIDUAL MEDLEY': 'IM',
    'FREE RELAY': 'FREE_RELAY', 'MEDLEY RELAY': 'MEDLEY_RELAY',
}

# After a swimmer row regex matches, look for the time on the same line.
# Format: "M:SS.cc" (e.g. 1:33.33) or "SS.cc" (e.g. 19.06). DQ rows have
# no time on the line, so missing match → None. We tolerate one rank
# digit/space between the row anchor and the time.
_TIME_AFTER_RE = re.compile(r"\b(\d{1,2}:\d{2}\.\d{2}|\d{1,3}\.\d{2})\b")

# Lines that LOOK like swimmer rows but didn't match any of A/B/C/D —
# used for parser-quality diagnostics. A line containing "-CT" with a
# digit is a strong tell.
_LIKELY_ROW_RE = re.compile(r"-CT\s+\d|-CT\b.*\d")


def _extract_time_after(line: str, end_pos: int) -> Optional[str]:
    """Find the first time pattern in `line` at or after `end_pos`.
    Returns the time string (e.g. '1:33.33' or '19.06') or None when the
    line has no time (DQ/NS rows)."""
    tail = line[end_pos:]
    m = _TIME_AFTER_RE.search(tail)
    return m.group(1) if m else None


def parse_results_pdf(path_or_bytes, return_diagnostics: bool = False):
    """Parse one Hy-Tek result PDF using pypdf (lighter than pdfplumber:
    ~37 MB peak vs ~154 MB, important on Render Starter's 512 MB cap).

    Tries Format A/B/C/D regexes per line. Returns one dict per swimmer-
    event row: {first, last, name_key, age, team, gender, event_name,
    distance, stroke, course, time}.

    Gender + event metadata come from the most recent "Girls 10 & Under
    25 Yard Freestyle"-style header seen above the row.

    When return_diagnostics=True, returns (rows, diagnostics) tuple. The
    diagnostics dict has total_lines, matched, and unmatched_sample so
    the Data tab can show parser quality per PDF.
    """
    out = []
    if isinstance(path_or_bytes, (bytes, bytearray)):
        reader = PdfReader(io.BytesIO(path_or_bytes))
    else:
        reader = PdfReader(path_or_bytes)

    current_gender = ''
    current_event = {'event_name': '', 'distance': None, 'stroke': '',
                     'course': ''}
    total_lines = 0
    unmatched_sample = []

    def _row_with_event(base: dict, time: Optional[str]) -> dict:
        """Stamp the running event metadata + time onto a parsed row."""
        return {
            **base,
            'event_name': current_event['event_name'],
            'distance': current_event['distance'],
            'stroke': current_event['stroke'],
            'course': current_event['course'],
            'time': time,
        }

    for page in reader.pages:
        text = page.extract_text() or ''
        for line in text.split('\n'):
            total_lines += 1
            # Event header (also stamps gender so the standalone gender
            # regex below is now mostly belt-and-suspenders).
            eh = _EVENT_HEADER_RE.search(line)
            if eh:
                gender_word, dist, course_word, stroke_word = eh.groups()
                current_gender = _GENDER_TO_FM.get(
                    gender_word.upper(), current_gender
                )
                stroke_norm = re.sub(r'\s+', ' ', stroke_word.strip()).upper()
                current_event = {
                    'event_name': re.sub(r'\s+', ' ', eh.group(0)).strip(' ()'),
                    'distance': int(dist),
                    'stroke': _STROKE_TO_CODE.get(stroke_norm, stroke_norm),
                    'course': 'Y' if course_word.upper().startswith('YARD') else 'L',
                }
            else:
                g = _GENDER_HEADER_RE.search(line)
                if g:
                    current_gender = _GENDER_TO_FM.get(
                        g.group(1).upper(), ''
                    )
            # Try Format A first
            matched_spans = []
            for m in _PDF_ROW_A_RE.finditer(line):
                team, age, last, first = m.groups()
                out.append(_row_with_event({
                    'first': first.strip(),
                    'last': last.strip(),
                    'name_key': normalize_name(first.strip(), last.strip()),
                    'age': int(age),
                    'team': team,
                    'gender': current_gender,
                }, _extract_time_after(line, m.end())))
                matched_spans.append(m.span())
            # Then Format B in any spans Format A didn't claim
            for m in _PDF_ROW_B_RE.finditer(line):
                if any(s <= m.start() < e for s, e in matched_spans):
                    continue
                team, age, last, first = m.groups()
                # Strip trailing single-letter initial like "Delaney M"
                first_clean = re.sub(r'\s+[A-Z]$', '', first.strip())
                # Format B has time BEFORE name — extract it from inside
                # the matched span (group 0).
                time_b = None
                inside = m.group(0)
                tm = re.search(r"(\d{1,2}:\d{2}\.\d{2}|\d{1,3}\.\d{2})", inside)
                if tm:
                    time_b = tm.group(1)
                out.append(_row_with_event({
                    'first': first_clean,
                    'last': last.strip(),
                    'name_key': normalize_name(first_clean, last.strip()),
                    'age': int(age),
                    'team': team,
                    'gender': current_gender,
                }, time_b))
                matched_spans.append(m.span())
            # Format C: championship "TEAM AGE First Last Rank Time" with
            # NO -CT marker and NO comma between names. Only match if A/B
            # didn't already claim this line.
            if not matched_spans:
                m = _PDF_ROW_C_RE.match(line)
                if m:
                    team, age, first, last = m.groups()
                    out.append(_row_with_event({
                        'first': first.strip(),
                        'last': last.strip(),
                        'name_key': normalize_name(first.strip(), last.strip()),
                        'age': int(age),
                        'team': team,
                        'gender': current_gender,
                    }, _extract_time_after(line, m.end())))
                    matched_spans.append(m.span())
            # Format D: relay legs "1) Last, First [Mid] Age" — finditer
            # captures all 4 legs in one row. Relay legs don't carry a
            # per-leg final time (only splits, which we don't need for
            # triangulation), so time stays None.
            for m in _PDF_RELAY_RE.finditer(line):
                if any(s <= m.start() < e for s, e in matched_spans):
                    continue
                last, first, age = m.groups()
                out.append(_row_with_event({
                    'first': first.strip(),
                    'last': last.strip(),
                    'name_key': normalize_name(first.strip(), last.strip()),
                    'age': int(age),
                    'team': '',
                    'gender': current_gender,
                }, None))
            # Diagnostic: line LOOKS like a swimmer row but no format
            # claimed it. Capture a small sample for admin visibility.
            if (not matched_spans
                    and _LIKELY_ROW_RE.search(line)
                    and len(unmatched_sample) < 10):
                unmatched_sample.append(line.strip()[:200])

    if return_diagnostics:
        diag = {
            'total_lines': total_lines,
            'matched': len(out),
            'unmatched_sample': unmatched_sample,
        }
        return out, diag
    return out


_META_DATE_RANGE_RE = re.compile(
    r'([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})\s*-\s*([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})'
)
_META_SINGLE_DATE_RE = re.compile(
    r'([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})'
)
_MONTH_NAMES = {'january': 1, 'february': 2, 'march': 3, 'april': 4,
                'may': 5, 'june': 6, 'july': 7, 'august': 8,
                'september': 9, 'october': 10, 'november': 11, 'december': 12}


def _parse_long_date(s: str) -> Optional[date]:
    """Parse 'January 30, 2026' or 'Jan 30 2026'-ish into a date object."""
    m = re.match(r'([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})', s.strip())
    if not m:
        return None
    mo = _MONTH_NAMES.get(m.group(1).lower()[:9])
    if not mo:
        return None
    try:
        return date(int(m.group(3)), mo, int(m.group(2)))
    except ValueError:
        return None


def extract_meet_metadata_from_pdf(path_or_bytes) -> dict:
    """Pull meet_name + start_date + end_date from a Hy-Tek PDF's header
    text. Returns {'meet_name': str, 'start_date': date|None,
    'end_date': date|None}.

    Hy-Tek result PDFs have a stable banner on page 1:
      Line 1: "CT Swimming - Results ONLY HY-TEK's MEET MANAGER 8.0..."
      Line 2: "<MEET NAME>, Sanction #: <SANCTION>"
      Line 3: "<MONTH DAY, YEAR> [- <MONTH DAY, YEAR>] - <CITY, ST>"

    Used by the manual-upload endpoint to attach an out-of-CT-Swim PDF
    to a synthesized ct_meet_id when the admin doesn't supply one.
    """
    out = {'meet_name': '', 'start_date': None, 'end_date': None}
    if isinstance(path_or_bytes, (bytes, bytearray)):
        reader = PdfReader(io.BytesIO(path_or_bytes))
    else:
        reader = PdfReader(path_or_bytes)
    if not reader.pages:
        return out
    text = reader.pages[0].extract_text() or ''
    lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
    # Skip the Hy-Tek banner line (HY-TEK's MEET MANAGER...)
    candidate_lines = [ln for ln in lines[:6]
                       if 'MEET MANAGER' not in ln.upper()
                       and 'PAGE ' not in ln.upper()
                       and 'RESULTS' not in ln.upper().rstrip()]
    # Meet name: first non-banner line, often ends with ", Sanction #: ..."
    for ln in candidate_lines:
        nm = re.sub(r',\s*Sanction\s*#?:?\s*[A-Z0-9-]+\s*$', '', ln,
                    flags=re.IGNORECASE).strip()
        if 4 <= len(nm) <= 200 and not _META_DATE_RANGE_RE.search(nm):
            out['meet_name'] = nm
            break
    # Date(s): scan all candidate lines for a long-form date
    for ln in candidate_lines:
        rng = _META_DATE_RANGE_RE.search(ln)
        if rng:
            out['start_date'] = _parse_long_date(rng.group(1))
            out['end_date'] = _parse_long_date(rng.group(2))
            break
        single = _META_SINGLE_DATE_RE.search(ln)
        if single:
            d = _parse_long_date(single.group(1))
            if d:
                out['start_date'] = d
                out['end_date'] = d
                break
    return out


def download_pdf(url: str) -> Optional[bytes]:
    """Fetch a PDF from CT Swim. Relative URLs are resolved against the base."""
    if not url:
        return None
    full = url if url.startswith('http') else CT_SWIM_BASE + url
    try:
        r = requests.get(full, timeout=30, headers={'User-Agent': USER_AGENT})
        if r.status_code != 200:
            return None
        return r.content
    except Exception:
        return None


# ===== SwimmerAtMeet metadata =====

CT_FAST_BASE = 'https://fast.ctswim.org/CTNet'

# Label4 on SwimmerAtMeet has format like:
#   '2025 CT LEHY 12U Regional Sat-SunEast Hartford, CT7/19/2025 - 7/20/2025'
# Three logical parts concatenated without separators: meet_name, location,
# date range. We extract from the right: dates first, then strip them.
_DATE_RANGE_RE = re.compile(
    r'(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})'
)
_SINGLE_DATE_RE = re.compile(r'(\d{1,2}/\d{1,2}/\d{4})')


def fetch_swimmer_at_meet(swimmer_ct_id: str, ct_meet_id: str) -> Optional[dict]:
    """Hit SwimmerAtMeet.aspx → return {meet_name, start_date, end_date}.
    Dates are date objects; meet_name is the cleaned event title.

    Uses BeautifulSoup so nested HTML inside Label4 (br/span/etc.) is
    handled correctly. The label has three logical parts concatenated:
      meet_name + location + date_range
    e.g. '2025 CT LEHY 12U Regional Sat-SunEast Hartford, CT7/19/2025 - 7/20/2025'
    We pull the date range first, then strip the trailing 'City, ST' as
    location, and what remains is the meet name.
    """
    if not swimmer_ct_id or not ct_meet_id:
        return None
    url = f'{CT_FAST_BASE}/SwimmerAtMeet.aspx?id={swimmer_ct_id}&m={ct_meet_id}'
    try:
        r = requests.get(url, timeout=15, headers={'User-Agent': USER_AGENT})
        if r.status_code != 200:
            return None
    except Exception:
        return None

    soup = BeautifulSoup(r.text, 'html.parser')
    # Try Label4 first, fall back to Label3 if absent (page layout varies
    # for some older meets).
    label_text = ''
    for label_id in ('Label4', 'Label3'):
        el = soup.find(id=label_id)
        if not el:
            continue
        # get_text with a separator so "Sat-Sun<br>City" doesn't collapse
        # into "Sat-SunCity"; we'll re-collapse whitespace below.
        text = el.get_text(separator=' ', strip=True)
        text = re.sub(r'\s+', ' ', text).strip()
        if text and any(c.isdigit() for c in text):
            label_text = text
            break
    if not label_text:
        return None

    # Pull dates first (range or single). meet_name keeps the full pre-date
    # text including any "City, ST" location — extra keywords don't hurt
    # the matcher, and trying to strip them by regex was producing wrong
    # cuts (e.g. "2023 CT NCA Fall Invite" had its first "CT" mistaken
    # for a state code).
    drm = _DATE_RANGE_RE.search(label_text)
    if drm:
        start = parse_us_date(drm.group(1))
        end = parse_us_date(drm.group(2))
        name = label_text[:drm.start()].strip()
    else:
        sdm = _SINGLE_DATE_RE.search(label_text)
        if not sdm:
            return {
                'meet_name': label_text.strip(),
                'start_date': None,
                'end_date': None,
            }
        start = parse_us_date(sdm.group(1))
        end = start
        name = label_text[:sdm.start()].strip()

    return {
        'meet_name': name,
        'start_date': start,
        'end_date': end,
    }
