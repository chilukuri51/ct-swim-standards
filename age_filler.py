"""Background job that fills team_member ages from swimstandards.com.

For each linked team_member: fetch the swimstandards profile (one request),
triangulate birth_year/birth_month from per-meet (age, meet_date) pairs using
the 'age-up = first day of meet' rule, persist results.

Cache TTL is 90 days. Members within 60 days of a known birthday are
automatically re-fetched even if cached.

One job at a time, cancellable, polite delays. Lives in its own thread state
so it can run alongside the CT Swim batch fetcher (different host).
"""

import threading
import time
import random
import re
import json
from dataclasses import dataclass, asdict, field
from typing import Optional
from datetime import datetime, date, timedelta, timezone

import requests

import db


SWIMSTANDARDS_BASE = 'https://swimstandards.com/swimmer'
# Cloudflare on swimstandards.com 403s requests with non-browser UAs from
# cloud IP ranges (Render etc.). Use a current Chrome on Mac string + the
# usual browser-companion headers so the request blends in.
USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
)
BROWSER_HEADERS = {
    'User-Agent': USER_AGENT,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,'
              'image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Upgrade-Insecure-Requests': '1',
}

# Polite delays between swimstandards.com fetches.
DELAY_MIN = 5
DELAY_MAX = 12

# Cache TTL: skip if synced within this many days, unless near a known birthday.
CACHE_DAYS = 90
NEAR_BIRTHDAY_WINDOW = 60  # days either side of known birth_month boundary


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_delay() -> float:
    return random.uniform(DELAY_MIN, DELAY_MAX)


@dataclass
class FillerState:
    running: bool = False
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    cancelled: bool = False
    total: int = 0
    done: int = 0
    current: str = ''
    updated: int = 0       # birth_year and/or birth_month set
    observed_only: int = 0 # found page but no record-level age data
    not_found: int = 0     # 404 on swimstandards
    skipped: int = 0       # contradictory or empty data
    gender_filled: int = 0 # gender pulled when previously blank
    fully_resolved: int = 0 # members already locked-in (year+month set), skipped by selection
    errors: list = field(default_factory=list)

    def progress_pct(self) -> int:
        if self.total == 0:
            return 0
        return int((self.done / self.total) * 100)

    def eta_seconds(self) -> int:
        avg = (DELAY_MIN + DELAY_MAX) / 2
        return int(max(0, self.total - self.done) * avg)


_state = FillerState()
_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


def get_status() -> dict:
    with _lock:
        d = asdict(_state)
    d['progress_pct'] = _state.progress_pct()
    d['eta_seconds'] = _state.eta_seconds()
    return d


def cancel_job():
    with _lock:
        _state.cancelled = True


def _slug_bases(first: str, last: str):
    """Generate base slug candidates (no numeric disambig suffix). swimstandards
    uses lowercase 'firstname-lastname'. Real-world last names like 'Mc Namar'
    are collapsed to 'mcnamar' on swimstandards, so we try both the
    space-preserved hyphenated form and a compact-no-internal-spaces form.
    """
    f = (first or '').strip().lower()
    l = (last or '').strip().lower()
    # Primary: hyphenate every whitespace run, drop punctuation
    base = re.sub(r"[^a-z0-9 ]+", "", f"{f} {l}")
    primary = re.sub(r"\s+", "-", base.strip())
    out = [primary]
    # Compact internal spaces: "mc namar" -> "mcnamar"
    f_compact = re.sub(r"\s+", "", f)
    l_compact = re.sub(r"\s+", "", l)
    compact = re.sub(r"[^a-z0-9]+", "-",
                     re.sub(r"\s+", "-", f"{f_compact} {l_compact}".strip())).strip('-')
    if compact and compact != primary:
        out.append(compact)
    return out


# Cap on numeric disambiguation suffixes per base. swimstandards numbers
# contiguously (-1, -2, -3, ...) so we stop early at the first 404; the cap
# is just a safety belt for runaway loops.
MAX_DISAMBIG = 20


# ===== swimstandards parsing =====

def _fetch_one(slug: str) -> Optional[dict]:
    """Fetch a single slug. Returns swimmer dict or None on 404/missing."""
    url = f"{SWIMSTANDARDS_BASE}/{slug}"
    r = requests.get(url, timeout=15, headers=BROWSER_HEADERS)
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} from swimstandards")
    m = re.search(r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>([^<]+)</script>', r.text)
    if not m:
        raise RuntimeError("__NEXT_DATA__ not found on page")
    blob = json.loads(m.group(1))
    return blob.get('props', {}).get('pageProps', {}).get('swimmer')


def _name_matches(member: dict, sw: dict) -> bool:
    """Loose name compare so 'Mc Namar' vs 'McNamar' both match."""
    def norm(s):
        return re.sub(r"[^a-z]", "", (s or '').lower())
    expected = norm(member.get('first_name')) + norm(member.get('last_name'))
    got_full = sw.get('name') or ''
    return norm(got_full) == expected


def _team_matches(member: dict, sw: dict) -> bool:
    """Confirm the swimstandards swimmer is actually on our team.
    Compares CT-Swim team code with swimstandards clubCode (both should be e.g. 'IVY').
    """
    ct_team = (member.get('ct_team') or '').strip().upper()
    club = (sw.get('clubCode') or '').strip().upper()
    if not ct_team or not club:
        # If either side is empty, fall back to name-only confidence
        return True
    return ct_team == club


def fetch_swimstandards(member: dict) -> Optional[dict]:
    """Resolve a team_member to their swimstandards profile by walking name
    + team-disambiguated slug variants until we find a name+team match.

    Strategy:
      1. Try a small set of base slugs (primary 'first-last' and a compact
         form for multi-word names like 'Mc Namar' -> 'mcnamar').
      2. If a base 404s, skip its numbered series (swimstandards numbers
         contiguously, so a 404 means no siblings exist).
      3. If a base 200s but is the wrong team (common name collision),
         walk '-1', '-2', '-3', ... up to MAX_DISAMBIG. Stop at the first
         404 since numbering is contiguous.

    Returns the matched swimmer dict or None. Raises on non-404 HTTP errors.
    """
    seen = set()
    for base in _slug_bases(member.get('first_name', ''), member.get('last_name', '')):
        if base in seen:
            continue
        seen.add(base)
        sw = _fetch_one(base)
        if sw is None:
            # Base slug 404 — no namesakes under this base. Try next base form.
            continue
        if _name_matches(member, sw) and _team_matches(member, sw):
            return sw
        # Base slug exists but wrong team. Walk the numeric series.
        for n in range(1, MAX_DISAMBIG + 1):
            slug = f"{base}-{n}"
            if slug in seen:
                continue
            seen.add(slug)
            sw_n = _fetch_one(slug)
            if sw_n is None:
                # Contiguous numbering — no more namesakes under this base.
                break
            if _name_matches(member, sw_n) and _team_matches(member, sw_n):
                return sw_n
            # Wrong team again — keep walking.
    return None


def _date_from_meet_slug(slug: str) -> Optional[date]:
    """Slugs follow 'YYYY-...-MMDD'. Pull year + last 4 digits."""
    if not slug or not isinstance(slug, str):
        return None
    parts = slug.split('-')
    if len(parts) < 2:
        return None
    year = parts[0] if parts[0].isdigit() and len(parts[0]) == 4 else None
    md = parts[-1] if parts[-1].isdigit() and len(parts[-1]) == 4 else None
    if not year or not md:
        return None
    try:
        return date(int(year), int(md[:2]), int(md[2:]))
    except ValueError:
        return None


def triangulate(records: list, today: Optional[date] = None) -> Optional[dict]:
    """records: list of {'age': int, 'meet_date': date}. Apply the
    age-up = first day of meet rule and intersect birth windows.
    Returns dict with birth_year, birth_month (each may be None),
    window_days, samples; or None if no usable records or contradictory.
    """
    today = today or date.today()
    lo = date(1990, 1, 1)
    hi = today
    n = 0
    for rec in records:
        age = rec.get('age')
        d = rec.get('meet_date')
        if age is None or d is None:
            continue
        try:
            rec_lo = d.replace(year=d.year - age - 1) + timedelta(days=1)
        except ValueError:
            # d is Feb 29 in a non-leap shifted year; nudge to Mar 1
            rec_lo = date(d.year - age - 1, 3, 1)
        try:
            rec_hi = d.replace(year=d.year - age)
        except ValueError:
            rec_hi = date(d.year - age, 2, 28)
        lo = max(lo, rec_lo)
        hi = min(hi, rec_hi)
        n += 1
        if lo > hi:
            return None  # contradictory
    if n == 0:
        return None
    span = (hi - lo).days
    same_year = (lo.year == hi.year)
    same_month = same_year and (lo.month == hi.month)
    return {
        'birth_year': lo.year if same_year else None,
        'birth_month': lo.month if same_month else None,
        'window_days': span,
        'samples': n,
        'lo': lo.isoformat(),
        'hi': hi.isoformat(),
    }


# ===== Job orchestration =====

def _is_near_known_birthday(member: dict, window_days: int = NEAR_BIRTHDAY_WINDOW) -> bool:
    """If birth_month is known, return True when today is within `window_days`
    of that month boundary in either direction (so we re-check before/after age-up)."""
    bm = member.get('birth_month')
    if not bm:
        return False
    today = date.today()
    # Anchor: 1st of birth_month this year (treat birthday as month start since we
    # don't know the day). Compare window in days.
    try:
        anchor = date(today.year, int(bm), 1)
    except ValueError:
        return False
    # Earliest re-check window starts (window_days) before anchor.
    if abs((today - anchor).days) <= window_days:
        return True
    # Also handle cases near year boundary (e.g. birth_month=12 in January)
    try:
        anchor_prev = date(today.year - 1, int(bm), 1)
        anchor_next = date(today.year + 1, int(bm), 1)
    except ValueError:
        return False
    return min(abs((today - anchor_prev).days), abs((today - anchor_next).days)) <= window_days


def _select_candidates(members: list, force_all: bool) -> list:
    """Choose which members to process this run.

    Birth year + birth month never change. Once both are set we skip the swimmer
    forever — coach can hit "Force all" to override (e.g. to correct a bad
    triangulation). Members with only year, or with no data, get the 90-day
    cache + near-birthday refresh logic so future seasons can narrow the month.
    """
    cutoff = (date.today() - timedelta(days=CACHE_DAYS)).isoformat()
    out = []
    for m in members:
        if not m.get('ct_id'):
            continue  # only swimmers linked to CT Swim records get auto-filled
        if force_all:
            out.append(m)
            continue
        # Done permanently if both year + month nailed down
        if m.get('birth_year') and m.get('birth_month'):
            continue
        # No birth_year resolved yet → always retry. The previous attempt may
        # have failed because swimstandards hadn't indexed the swimmer yet, or
        # because our slug-disambiguation depth was too shallow at the time.
        # Skipping for 90 days strands real candidates (e.g. names that need
        # deep numeric suffixes like 'emma-baker-4').
        if not m.get('birth_year'):
            out.append(m)
            continue
        synced = m.get('age_synced_at') or ''
        if not synced:
            out.append(m)
            continue
        if synced[:10] < cutoff:
            out.append(m)
            continue
        if _is_near_known_birthday(m):
            out.append(m)
    return out


def start_autofill(force_all: bool = False) -> bool:
    """Start the autofill job. Returns False if a job is already running."""
    global _thread, _state
    if _thread and _thread.is_alive():
        return False

    members = db.list_team_members(include_birth=True)
    candidates = _select_candidates(members, force_all)
    fully_resolved = sum(1 for m in members
                         if m.get('ct_id') and m.get('birth_year') and m.get('birth_month'))

    _state = FillerState(
        running=True,
        started_at=_now_iso(),
        total=len(candidates),
        fully_resolved=fully_resolved,
    )

    if not candidates:
        with _lock:
            _state.running = False
            _state.finished_at = _now_iso()
        return True

    _thread = threading.Thread(target=_run, args=(candidates, force_all), daemon=True)
    _thread.start()
    return True


def _sleep_respecting_cancel(seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        with _lock:
            if _state.cancelled:
                return False
        time.sleep(min(0.5, end - time.time()))
    return True


_SEX_MAP = {'female': 'F', 'male': 'M', 'f': 'F', 'm': 'M'}


def _record_date(rec: dict):
    """Prefer the explicit `date` field on each record (ISO-ish), fall back
    to parsing the meet slug."""
    raw = rec.get('date')
    if raw:
        # Try a few formats: 'YYYY-MM-DD', 'YYYY-MM-DDTHH:MM:SS', 'M/D/YYYY'
        s = str(raw)[:10]
        try:
            return date.fromisoformat(s)
        except ValueError:
            pass
        m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', str(raw).strip())
        if m:
            try:
                return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
            except ValueError:
                pass
    return _date_from_meet_slug(rec.get('meetSlug', ''))


def _process_member(member: dict, force_all: bool = False) -> dict:
    """Process one member: fetch + triangulate + save. Returns a status tag.
    force_all=True overwrites gender even if a value already exists (treats the
    run as a re-verify pass — useful for fixing earlier wrong test data)."""
    sw = fetch_swimstandards(member)
    if sw is None:
        # Mark synced anyway so we don't keep retrying every job until something changes.
        db.save_member_triangulation(member['id'])
        return {'status': 'not_found'}

    # Gender: fill if missing OR force_all (re-verify mode). Only ever set values
    # that came from a confirmed name+team match (already validated in fetch).
    gender_filled = False
    sex_raw = (sw.get('sex') or '').strip().lower()
    g = _SEX_MAP.get(sex_raw)
    if g and (force_all or not member.get('gender')):
        if g != member.get('gender'):
            db.update_team_member(member['id'], gender=g)
            gender_filled = True

    current_age = sw.get('age')
    records = []
    for rec in (sw.get('records', {}) or {}).get('data', []) or []:
        d = _record_date(rec)
        a = rec.get('age')
        if d and a is not None:
            records.append({'age': a, 'meet_date': d})

    tri = triangulate(records)
    if tri is None:
        db.save_member_triangulation(member['id'], age_observed=current_age)
        return {
            'status': 'observed_only' if current_age is not None else 'skipped',
            'gender_filled': gender_filled,
        }

    db.save_member_triangulation(
        member['id'],
        birth_year=tri.get('birth_year'),
        birth_month=tri.get('birth_month'),
        window_days=tri.get('window_days'),
        age_observed=current_age,
    )
    return {
        'status': 'updated',
        'tri': tri,
        'narrowed_year': tri.get('birth_year') is not None,
        'narrowed_month': tri.get('birth_month') is not None,
        'gender_filled': gender_filled,
    }


def _run(candidates, force_all: bool = False):
    try:
        for i, m in enumerate(candidates):
            with _lock:
                if _state.cancelled:
                    break
                _state.current = f"{m['first_name']} {m['last_name']}"

            try:
                result = _process_member(m, force_all=force_all)
                with _lock:
                    s = result.get('status')
                    if s == 'updated':
                        _state.updated += 1
                    elif s == 'not_found':
                        _state.not_found += 1
                    elif s == 'observed_only':
                        _state.observed_only += 1
                    else:
                        _state.skipped += 1
                    if result.get('gender_filled'):
                        _state.gender_filled += 1
                db.log_sync(m.get('ct_id'), 'age_fill', 'ok' if s == 'updated' else s,
                            f"{m['first_name']} {m['last_name']}: {s}"
                            + (' +gender' if result.get('gender_filled') else ''))
            except Exception as e:
                with _lock:
                    _state.errors.append(f"{m['first_name']} {m['last_name']}: {e}")
                db.log_sync(m.get('ct_id'), 'age_fill', 'error',
                            f"{m['first_name']} {m['last_name']}: {e}")

            with _lock:
                _state.done = i + 1

            if i < len(candidates) - 1:
                if not _sleep_respecting_cancel(_next_delay()):
                    break
    finally:
        with _lock:
            _state.running = False
            _state.finished_at = _now_iso()
