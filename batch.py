"""Background batch fetcher for CT Swim data.

Runs in a single background thread. Polite rate limiting (5s between requests).
Safe to run across browser sessions since state is in-memory on the server.

One job at a time. Admin can cancel and start a new one.
"""

import threading
import time
import random
import string
from dataclasses import dataclass, asdict, field
from typing import Optional
from datetime import datetime, timezone

import db
from pdf_parser import normalize_for_match


def _trigger_age_autofill_if_idle():
    """Kick off the age-fill job once a batch finishes. Safe-imported lazily
    to avoid circular imports at module load."""
    try:
        import age_filler
        age_filler.start_autofill(force_all=False)
    except Exception:
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Randomized polite delay between CT Swim requests (in seconds).
# Mimics human pacing rather than a steady automated cadence.
RATE_LIMIT_MIN = 5
RATE_LIMIT_MAX = 30
# Average for ETA display
RATE_LIMIT_AVG = (RATE_LIMIT_MIN + RATE_LIMIT_MAX) / 2


def _next_delay() -> float:
    return random.uniform(RATE_LIMIT_MIN, RATE_LIMIT_MAX)


@dataclass
class BatchState:
    running: bool = False
    mode: str = ''  # 'last_names' | 'team_code'
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    cancelled: bool = False

    # Phase 1: searching
    search_total: int = 0
    search_done: int = 0
    current_search: str = ''

    # Phase 2: fetching swimmers
    fetch_total: int = 0
    fetch_done: int = 0
    current_fetch: str = ''

    # Results
    swimmers_found: int = 0
    swimmers_cached: int = 0
    errors: list = field(default_factory=list)

    # Meta
    team_filter: str = ''

    def progress_pct(self) -> int:
        total = self.search_total + self.fetch_total
        done = self.search_done + self.fetch_done
        if total == 0:
            return 0
        return int((done / total) * 100)

    def eta_seconds(self) -> int:
        remaining = (self.search_total - self.search_done) + (self.fetch_total - self.fetch_done)
        return int(remaining * RATE_LIMIT_AVG)


_state = BatchState()
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


def _reset_state(mode: str, team_filter: str = ''):
    global _state
    _state = BatchState(
        running=True,
        mode=mode,
        started_at=_now_iso(),
        team_filter=team_filter.upper() if team_filter else '',
    )


def start_batch_last_names(last_names: list, team_filter: str = '', ct_search=None, ct_fetch_and_cache=None) -> bool:
    """Start a batch job searching by last names.

    ct_search(last_name) -> (swimmers_list, tokens, cookies)
    ct_fetch_and_cache(ct_id) -> result dict
    """
    global _thread
    if _thread and _thread.is_alive():
        return False

    _reset_state('last_names', team_filter)
    names = [n.strip() for n in last_names if n.strip()]
    with _lock:
        _state.search_total = len(names)

    _thread = threading.Thread(target=_run_last_names, args=(names, team_filter, ct_search, ct_fetch_and_cache), daemon=True)
    _thread.start()
    return True


def start_batch_team_code(team_code: str, ct_search=None, ct_fetch_and_cache=None) -> bool:
    """Start a batch job: iterate A-Z letter searches filtered by team code."""
    global _thread
    if _thread and _thread.is_alive():
        return False

    _reset_state('team_code', team_code)
    letters = list(string.ascii_uppercase)
    with _lock:
        _state.search_total = len(letters)

    _thread = threading.Thread(target=_run_team_code, args=(team_code, letters, ct_search, ct_fetch_and_cache), daemon=True)
    _thread.start()
    return True


def _sleep_respecting_cancel(seconds: float) -> bool:
    """Sleep in small chunks so cancel is responsive. Returns False if cancelled."""
    end = time.time() + seconds
    while time.time() < end:
        with _lock:
            if _state.cancelled:
                return False
        time.sleep(min(0.5, end - time.time()))
    return True


def _run_last_names(last_names, team_filter, ct_search, ct_fetch_and_cache):
    team_filter_upper = team_filter.upper().strip() if team_filter else ''
    swimmers_to_fetch = []

    try:
        # Phase 1: search each last name
        for i, last_name in enumerate(last_names):
            with _lock:
                if _state.cancelled: break
                _state.current_search = last_name

            try:
                swimmers, _tokens, _cookies = ct_search(last_name)
                # Filter by team if specified
                if team_filter_upper:
                    swimmers = [s for s in swimmers if s.get('team', '').upper() == team_filter_upper]
                # Save to DB as candidates
                db.upsert_swimmers_bulk(swimmers)
                swimmers_to_fetch.extend(swimmers)
                db.log_sync(None, 'search', 'ok', f'Found {len(swimmers)} for "{last_name}"')
            except Exception as e:
                with _lock:
                    _state.errors.append(f"Search '{last_name}' failed: {e}")
                db.log_sync(None, 'search', 'error', f'"{last_name}": {e}')

            with _lock:
                _state.search_done = i + 1

            # Rate limit between searches
            if i < len(last_names) - 1:
                if not _sleep_respecting_cancel(_next_delay()):
                    break

        # Dedupe by ct_id
        seen = set()
        unique_swimmers = []
        for sw in swimmers_to_fetch:
            if sw['id'] not in seen:
                seen.add(sw['id'])
                unique_swimmers.append(sw)

        with _lock:
            _state.swimmers_found = len(unique_swimmers)
            _state.fetch_total = len(unique_swimmers)

        # Phase 2: fetch times for each swimmer
        for i, sw in enumerate(unique_swimmers):
            with _lock:
                if _state.cancelled: break
                _state.current_fetch = f"{sw['name']} ({sw.get('team','')})"

            # Rate limit before fetch
            if not _sleep_respecting_cancel(_next_delay()):
                break

            try:
                ct_fetch_and_cache(sw['id'])
                with _lock:
                    _state.swimmers_cached += 1
            except Exception as e:
                with _lock:
                    _state.errors.append(f"Fetch '{sw['name']}' failed: {e}")
                db.log_sync(sw['id'], 'batch_fetch', 'error', str(e))

            with _lock:
                _state.fetch_done = i + 1
    finally:
        with _lock:
            _state.running = False
            _state.finished_at = _now_iso()
            cached = _state.swimmers_cached
        if cached > 0:
            _trigger_age_autofill_if_idle()


def _run_team_code(team_code, letters, ct_search, ct_fetch_and_cache):
    team_upper = team_code.upper().strip()
    swimmers_to_fetch = []

    try:
        # Phase 1: A-Z searches, filter by team
        for i, letter in enumerate(letters):
            with _lock:
                if _state.cancelled: break
                _state.current_search = f"Letter {letter}"

            try:
                swimmers, _tokens, _cookies = ct_search(letter)
                team_matches = [s for s in swimmers if s.get('team', '').upper() == team_upper]
                db.upsert_swimmers_bulk(team_matches)
                swimmers_to_fetch.extend(team_matches)
                if team_matches:
                    # Auto-link any team_members whose names match the new cached swimmers
                    db.auto_link_team_members()
                    db.log_sync(None, 'search', 'ok', f'Letter {letter}: {len(team_matches)} on team {team_upper}')
            except Exception as e:
                with _lock:
                    _state.errors.append(f"Letter '{letter}' failed: {e}")
                db.log_sync(None, 'search', 'error', f'Letter {letter}: {e}')

            with _lock:
                _state.search_done = i + 1

            if i < len(letters) - 1:
                if not _sleep_respecting_cancel(_next_delay()):
                    break

        # Dedupe
        seen = set()
        unique_swimmers = []
        for sw in swimmers_to_fetch:
            if sw['id'] not in seen:
                seen.add(sw['id'])
                unique_swimmers.append(sw)

        with _lock:
            _state.swimmers_found = len(unique_swimmers)
            _state.fetch_total = len(unique_swimmers)

        # Phase 2: fetch times
        for i, sw in enumerate(unique_swimmers):
            with _lock:
                if _state.cancelled: break
                _state.current_fetch = f"{sw['name']} ({sw.get('team','')})"

            if not _sleep_respecting_cancel(_next_delay()):
                break

            try:
                ct_fetch_and_cache(sw['id'])
                with _lock:
                    _state.swimmers_cached += 1
            except Exception as e:
                with _lock:
                    _state.errors.append(f"Fetch '{sw['name']}' failed: {e}")
                db.log_sync(sw['id'], 'batch_fetch', 'error', str(e))

            with _lock:
                _state.fetch_done = i + 1
    finally:
        with _lock:
            _state.running = False
            _state.finished_at = _now_iso()
            cached = _state.swimmers_cached
        if cached > 0:
            _trigger_age_autofill_if_idle()


def start_batch_member_directory(included: list, ct_search=None, ct_fetch_and_cache=None) -> bool:
    """Start a batch from a parsed Member Directory.

    `included` is a list of dicts from pdf_parser.parse_directory_pdf each containing:
      first_name, last_name, full_name, search_key, match_key, roster
    """
    global _thread
    if _thread and _thread.is_alive():
        return False

    _reset_state('member_directory', '')

    # Group by unique search key (first word of last name) to dedupe searches
    by_search_key = {}
    for entry in included:
        key = entry['search_key']
        if not key:
            continue
        by_search_key.setdefault(key, []).append(entry)

    with _lock:
        _state.search_total = len(by_search_key)

    _thread = threading.Thread(
        target=_run_member_directory,
        args=(by_search_key, ct_search, ct_fetch_and_cache),
        daemon=True
    )
    _thread.start()
    return True


def _run_member_directory(by_search_key, ct_search, ct_fetch_and_cache):
    matched_swimmers = []  # list of (ct_id, name, team, roster)

    try:
        # Phase 1: search each unique last-name prefix; match results against PDF entries
        for i, (search_key, entries) in enumerate(by_search_key.items()):
            with _lock:
                if _state.cancelled: break
                _state.current_search = f'{search_key} ({len(entries)} expected)'

            try:
                swimmers, _tokens, _cookies = ct_search(search_key)

                # Build lookup: match_key -> (full_name, roster) from PDF
                expected = {e['match_key']: e for e in entries}

                # For each CT swim result, see if it matches an expected swimmer
                page_matches = 0
                for sw in swimmers:
                    sw_match_key = normalize_for_match(sw.get('name', ''))
                    if sw_match_key in expected:
                        entry = expected[sw_match_key]
                        # Save swimmer + roster
                        db.upsert_swimmers_bulk([sw])
                        db.set_roster(sw['id'], entry['roster'])
                        matched_swimmers.append({
                            'id': sw['id'],
                            'name': sw['name'],
                            'team': sw.get('team', ''),
                            'roster': entry['roster'],
                        })
                        page_matches += 1
                # After this letter's matches, link any team_members whose
                # names match cached swimmers (covers both this and previous batches).
                db.auto_link_team_members()
                db.log_sync(None, 'search', 'ok',
                            f'"{search_key}": {page_matches}/{len(entries)} matched')

                # Log unmatched expected swimmers as info
                matched_keys = {normalize_for_match(sw['name']) for sw in swimmers}
                for entry in entries:
                    if entry['match_key'] not in matched_keys:
                        with _lock:
                            _state.errors.append(
                                f'"{entry["full_name"]}" ({entry["roster"]}) — not found in CT Swim'
                            )
            except Exception as e:
                with _lock:
                    _state.errors.append(f'Search "{search_key}" failed: {e}')
                db.log_sync(None, 'search', 'error', f'"{search_key}": {e}')

            with _lock:
                _state.search_done = i + 1

            if i < len(by_search_key) - 1:
                if not _sleep_respecting_cancel(_next_delay()):
                    break

        # Dedupe by ct_id (a swimmer could appear in multiple search results edge case)
        seen = set()
        unique = []
        for sw in matched_swimmers:
            if sw['id'] not in seen:
                seen.add(sw['id'])
                unique.append(sw)

        with _lock:
            _state.swimmers_found = len(unique)
            _state.fetch_total = len(unique)

        # Phase 2: fetch best times for each matched swimmer
        for i, sw in enumerate(unique):
            with _lock:
                if _state.cancelled: break
                _state.current_fetch = f"{sw['name']} ({sw['roster']})"

            if not _sleep_respecting_cancel(_next_delay()):
                break

            try:
                ct_fetch_and_cache(sw['id'])
                with _lock:
                    _state.swimmers_cached += 1
            except Exception as e:
                with _lock:
                    _state.errors.append(f"Fetch '{sw['name']}' failed: {e}")
                db.log_sync(sw['id'], 'batch_fetch', 'error', str(e))

            with _lock:
                _state.fetch_done = i + 1
    finally:
        with _lock:
            _state.running = False
            _state.finished_at = _now_iso()
            cached = _state.swimmers_cached
        if cached > 0:
            _trigger_age_autofill_if_idle()


def get_recent_log(limit: int = 20):
    """Return recent sync_log entries."""
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT action, status, message, created_at
            FROM sync_log
            ORDER BY id DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]
