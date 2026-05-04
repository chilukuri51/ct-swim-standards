import os
import re
from functools import wraps
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, jsonify, request, session, redirect, url_for

import db
import batch
import age_filler
import standards_store

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-me-in-production-please-a8f3d9x2')

BASE_URL = "https://fast.ctswim.org/CTNet"

# ===== Three role-based accounts =====
# Each role has its own username/password (override via env vars in production)
USERS = {
    'admin': os.environ.get('ADMIN_PASSWORD', 'ItsNaga!23'),
    'coach': os.environ.get('COACH_PASSWORD', 'ItsEllis!23'),
}

# Feature access matrix
ROLE_PERMISSIONS = {
    'admin': {'search', 'refresh', 'batch', 'club', 'roster', 'roster_edit', 'standards', 'standards_edit', 'dashboard', 'whatif'},
    'coach': {'search', 'club', 'roster', 'roster_edit', 'standards', 'standards_edit', 'dashboard', 'whatif'},
}


# Initialize DB on import
db.init_db()

# Seed team_members from team_roster.json on first startup (no-op if table populated).
# Looks in the persistent data dir first (so the file survives deploys), then falls
# back to the project-bundled copy for local dev.
import paths
_TEAM_ROSTER_CANDIDATES = [
    os.path.join(paths.DATA_DIR, 'team_roster.json'),
    os.path.join(paths.PROJECT_DATA_DIR, 'team_roster.json'),
]
TEAM_ROSTER_JSON = next((p for p in _TEAM_ROSTER_CANDIDATES if os.path.exists(p)),
                       _TEAM_ROSTER_CANDIDATES[0])
_seeded = db.seed_team_members_from_json(TEAM_ROSTER_JSON)
if _seeded:
    app.logger.info(f"Seeded {_seeded} team members from {TEAM_ROSTER_JSON}")
app.logger.info(
    f"Persist root: {paths.PERSIST_ROOT or '(local dev)'} | "
    f"DB: {paths.DB_PATH} | DATA_DIR: {paths.DATA_DIR}"
)

# Ensure standards.json (working copy) exists, copying from default if needed
standards_store.ensure_seeded()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('logged_in'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('login', next=request.path))
        return view(*args, **kwargs)
    return wrapped


def role_required(*allowed_roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            user_role = session.get('role', 'coach')
            if user_role not in allowed_roles:
                if request.path.startswith('/api/'):
                    return jsonify({'error': 'Forbidden'}), 403
                return redirect(url_for('index'))
            return view(*args, **kwargs)
        return wrapped
    return decorator


# ===== CT Swim scraping helpers =====

def get_session_and_tokens():
    sess = requests.Session()
    r = sess.get(f"{BASE_URL}/DatabaseQuery.aspx?Opt=BT", timeout=10)
    return sess, _extract_tokens(r.text)


def _extract_tokens(html):
    vs = re.search(r'name="__VIEWSTATE"[^>]*value="([^"]*)"', html)
    vsg = re.search(r'name="__VIEWSTATEGENERATOR"[^>]*value="([^"]*)"', html)
    ev = re.search(r'name="__EVENTVALIDATION"[^>]*value="([^"]*)"', html)
    return {
        '__VIEWSTATE': vs.group(1) if vs else '',
        '__VIEWSTATEGENERATOR': vsg.group(1) if vsg else '',
        '__EVENTVALIDATION': ev.group(1) if ev else '',
    }


def ct_search_swimmers(last_name):
    """Search CT Swim for swimmers by last name. Returns (swimmers, tokens, cookies)."""
    sess, tokens = get_session_and_tokens()
    data = {
        **tokens,
        'ddl_SearchType': 'Last Name Starts With',
        'tb_SearchFor': last_name,
        '_ctl0': 'Search'
    }
    r = sess.post(f"{BASE_URL}/DatabaseQuery.aspx?Opt=BT", data=data, timeout=15)
    html = r.text

    swimmers = []
    for match in re.finditer(
        r'name="SwimmerSelect" value="([^"]*)"[^>]*/>'
        r'<label[^>]*>([^<]*)</label>',
        html
    ):
        swimmer_id = match.group(1)
        label = match.group(2).strip()
        name_match = re.match(r'(.+?)\s*\((\w+)\)', label)
        if name_match:
            swimmers.append({
                'id': swimmer_id,
                'name': name_match.group(1).strip(),
                'team': name_match.group(2).strip()
            })

    return swimmers, _extract_tokens(html), sess.cookies.get_dict()


def _extract_age_from_ct_html(html, swimmer_name=''):
    """Best-effort age scrape from a CT Swim Best Times page.
    Tries multiple patterns; returns int or None. Safe to call on any HTML.
    """
    # Search both the swimmer label and the broader page text.
    haystacks = []
    if swimmer_name:
        haystacks.append(swimmer_name)
    if html:
        # Strip tags so '12 (F)' style markers aren't broken across HTML
        text = re.sub(r'<[^>]+>', ' ', html)
        haystacks.append(text)
    patterns = [
        r'\bAge\s*[:#-]?\s*(\d{1,2})\b',
        r'\b(\d{1,2})\s*\(\s*[MF]\s*\)',
        r'\b(\d{1,2})\s*Y(?:ears?|rs?)?\b',
    ]
    for hay in haystacks:
        for p in patterns:
            m = re.search(p, hay, re.IGNORECASE)
            if m:
                try:
                    n = int(m.group(1))
                    if 5 <= n <= 25:
                        return n
                except ValueError:
                    continue
    return None


def ct_fetch_best_times(swimmer_id, tokens, cookies):
    """Fetch best times for a swimmer. Returns {swimmer_name, events, age}."""
    sess = requests.Session()
    for k, v in cookies.items():
        sess.cookies.set(k, v)
    data = {
        **tokens,
        'SwimmerSelect': swimmer_id,
        'GoB': 'Display Times'
    }
    r = sess.post(f"{BASE_URL}/DatabaseQuery.aspx?Opt=BT", data=data, timeout=15)
    soup = BeautifulSoup(r.text, 'html.parser')

    swimmer_name = ''
    label2 = soup.find('span', id='Label2')
    if label2:
        swimmer_name = label2.get_text(strip=True)

    age = _extract_age_from_ct_html(r.text, swimmer_name)

    events = []
    table = soup.find('table', id='Table1')
    if table:
        for row in table.find_all('tr')[1:]:
            cells = row.find_all('td')
            if len(cells) >= 4:
                event_code = cells[0].get_text(strip=True)
                stroke = cells[1].get_text(strip=True)
                time = cells[2].get_text(strip=True)
                date = cells[3].get_text(strip=True)
                event_link = ''
                if len(cells) >= 5:
                    link_tag = cells[4].find('a', string=re.compile(r'\[Event\]'))
                    if link_tag:
                        event_link = link_tag.get('href', '')
                events.append({
                    'event': f"{event_code} {stroke}",
                    'time': time,
                    'date': date,
                    'history_url': event_link
                })

    return {'swimmer_name': swimmer_name, 'events': events, 'age': age}


def ct_fetch_event_history(history_url):
    """Fetch event history. Returns {title, history}.

    Each row's 4th cell holds a [Meet] link to SwimmerAtMeet.aspx?...&m=<id>.
    We capture that meet_id so age_filler can look up the swimmer's age in
    the corresponding result PDF (the swim_type column only says
    'Finals'/'Prelims', not the actual meet name).
    """
    full_url = f"{BASE_URL}/{history_url}"
    r = requests.get(full_url, timeout=15)
    soup = BeautifulSoup(r.text, 'html.parser')

    title = ''
    label2 = soup.find('span', id='Label2')
    label3 = soup.find('span', id='Label3')
    if label2:
        title = label2.get_text(strip=True)
    if label3:
        title = label3.get_text(strip=True) + ' - ' + title

    history = []
    # The history table is the one whose header is Time | Swim | Date | [Meet].
    target_table = None
    for tbl in soup.find_all('table'):
        rows = tbl.find_all('tr')
        if not rows:
            continue
        hdr = [c.get_text(strip=True) for c in rows[0].find_all(['td', 'th'])]
        if hdr[:3] == ['Time', 'Swim', 'Date']:
            target_table = tbl
            break
    if target_table:
        for row in target_table.find_all('tr')[1:]:
            cells = row.find_all('td')
            if len(cells) < 3:
                continue
            time_val = cells[0].get_text(strip=True)
            swim_type = cells[1].get_text(strip=True)
            date_val = cells[2].get_text(strip=True)
            meet_id = ''
            if len(cells) >= 4:
                link = cells[3].find('a', href=True)
                if link:
                    m = re.search(r'm=(\d+)', link['href'])
                    if m:
                        meet_id = m.group(1)
            if time_val and time_val != 'Time':
                history.append({
                    'time': time_val,
                    'meet': swim_type,
                    'date': date_val,
                    'ct_meet_id': meet_id,
                })

    return {'title': title, 'history': history}


def ct_fetch_and_cache_swimmer(ct_id):
    """One-shot: search CT Swim for this swimmer's ID, fetch best times, save to cache.
    Used by per-swimmer refresh and batch refresh.
    """
    # To fetch a specific swimmer, we need to do a search first (ASP.NET requires the selection flow)
    # Pull existing name from cache to do a search, then pick this specific swimmer by ct_id.
    cached = db.get_best_times(ct_id)
    # Fall back: try getting swimmer row directly
    with db.get_conn() as conn:
        row = conn.execute("SELECT name FROM swimmers WHERE ct_id = ?", (ct_id,)).fetchone()
    if not row:
        raise ValueError(f"Swimmer {ct_id} not in cache; search for them first")
    last_name = row['name'].split()[-1]

    swimmers, tokens, cookies = ct_search_swimmers(last_name)
    if not any(s['id'] == ct_id for s in swimmers):
        # Swimmer no longer findable with that last name
        db.log_sync(ct_id, 'refresh', 'error', f'Not found in CT Swim search for "{last_name}"')
        raise ValueError('Swimmer not found in CT Swim search')

    result = ct_fetch_best_times(ct_id, tokens, cookies)
    db.save_best_times(ct_id, result['swimmer_name'], result['events'])
    if result.get('age'):
        db.update_member_observed_age(ct_id, result['age'])
    db.log_sync(ct_id, 'refresh', 'ok', f'Fetched {len(result["events"])} events')
    return result


# ===== Auth routes =====

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        if username in USERS and password == USERS[username]:
            session['logged_in'] = True
            session['username'] = username
            session['role'] = username
            session.permanent = True
            return redirect(request.args.get('next') or url_for('index'))
        error = 'Invalid username or password'
    if session.get('logged_in'):
        return redirect(url_for('index'))
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ===== Main app =====

@app.route('/')
@login_required
def index():
    role = session.get('role', 'coach')
    perms = list(ROLE_PERMISSIONS.get(role, set()))
    return render_template('index.html',
                           role=role,
                           username=session.get('username', ''),
                           permissions=perms,
                           standards=standards_store.load())


# ===== API: Search swimmers =====
# Behavior: check cache first, only hit CT Swim if cache has <5 results.

@app.route('/api/search', methods=['POST'])
@login_required
def search_swimmer():
    last_name = request.json.get('last_name', '').strip()
    if not last_name:
        return jsonify({'error': 'Last name is required'}), 400

    # Always check cache first
    cached_swimmers = db.search_swimmers_by_last_name(last_name)

    # If cache has results, use them (fast, no CT Swim hit)
    if cached_swimmers:
        return jsonify({
            'swimmers': cached_swimmers,
            'cached': True,
            'source': 'cache',
        })

    # Otherwise fall back to CT Swim
    try:
        swimmers, tokens, cookies = ct_search_swimmers(last_name)
        # Cache the swimmer list for next time
        db.upsert_swimmers_bulk(swimmers)
        return jsonify({
            'swimmers': swimmers,
            'tokens': tokens,
            'cookies': cookies,
            'cached': False,
            'source': 'ctswim',
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===== API: Best times =====
# Behavior: always read from cache. If not cached, fetch once and save.

@app.route('/api/best_times', methods=['POST'])
@login_required
def get_best_times():
    swimmer_id = request.json.get('swimmer_id', '')
    tokens = request.json.get('tokens', {})
    cookies = request.json.get('cookies', {})

    if not swimmer_id:
        return jsonify({'error': 'Missing swimmer_id'}), 400

    # Try cache first
    cached = db.get_best_times(swimmer_id)
    if cached and cached.get('events'):
        return jsonify(cached)

    # Not in cache - need to fetch. This only happens on first view.
    if not tokens:
        # We got a swimmer_id from cache (no search was done), so we don't have tokens.
        # Do a fresh CT Swim fetch using the cached swimmer's name.
        try:
            result = ct_fetch_and_cache_swimmer(swimmer_id)
            result['cached'] = False
            return jsonify(result)
        except Exception as e:
            return jsonify({'error': f'Cache miss and refresh failed: {str(e)}'}), 500

    try:
        result = ct_fetch_best_times(swimmer_id, tokens, cookies)
        db.save_best_times(swimmer_id, result['swimmer_name'], result['events'])
        if result.get('age'):
            db.update_member_observed_age(swimmer_id, result['age'])
        result['cached'] = False
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===== API: Event history =====

@app.route('/api/event_history', methods=['POST'])
@login_required
def get_event_history():
    history_url = request.json.get('history_url', '')
    swimmer_id = request.json.get('swimmer_id', '')
    event_name = request.json.get('event_name', '')

    if not history_url:
        return jsonify({'error': 'Missing history URL'}), 400

    # Check cache first
    cached = db.get_event_history(history_url)
    if cached:
        # Build title from swimmer + event
        with db.get_conn() as conn:
            row = conn.execute("SELECT name, team_code FROM swimmers WHERE ct_id = ?", (swimmer_id,)).fetchone()
        title = ''
        if row:
            name_team = f"{row['name']} ({row['team_code']})" if row['team_code'] else row['name']
            title = f"Swimmer Event History - {name_team}"
        return jsonify({'title': title, 'history': cached, 'cached': True})

    # Not cached - fetch
    try:
        result = ct_fetch_event_history(history_url)
        if swimmer_id:
            db.save_event_history(history_url, swimmer_id, event_name, result['history'])
        result['cached'] = False
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===== API: Refresh a single swimmer (admin only) =====

@app.route('/api/refresh_swimmer', methods=['POST'])
@role_required('admin')
def refresh_swimmer():
    swimmer_id = request.json.get('swimmer_id', '')
    if not swimmer_id:
        return jsonify({'error': 'Missing swimmer_id'}), 400
    try:
        result = ct_fetch_and_cache_swimmer(swimmer_id)
        # Best-effort: catch any newly-linked swimmers in the next age-fill pass
        age_filler.start_autofill(force_all=False)
        return jsonify({'ok': True, 'events_count': len(result.get('events', []))})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===== API: Cache stats (for admin tab) =====

@app.route('/api/cache_stats', methods=['GET'])
@login_required
def cache_stats():
    return jsonify({
        'total_swimmers': db.get_swimmer_count(),
        'cached_swimmers': db.get_cached_swimmer_count(),
    })


# ===== API: Admin batch fetch =====

@app.route('/api/team_members', methods=['GET'])
@role_required('admin', 'coach')
def api_list_team_members():
    members = db.list_team_members()
    return jsonify({'members': members, 'count': len(members)})


@app.route('/api/my_swimmers', methods=['GET'])
@login_required
def api_my_swimmers():
    """Roster + cached best_times for the logged-in role.
    Currently coach/admin only — parent role is disabled."""
    members = db.list_team_members_with_times()
    return jsonify({'members': members, 'count': len(members)})


@app.route('/api/team_members/auto_link', methods=['POST'])
@role_required('admin')
def api_auto_link():
    """Match cached swimmers to team_members by name. Local-only, no CT Swim calls."""
    result = db.auto_link_team_members()
    return jsonify({'ok': True, **result})


@app.route('/api/team_members/autofill_ages/start', methods=['POST'])
@role_required('admin')
def api_autofill_ages_start():
    """Kick off the CT Swim PDF age triangulation job."""
    force = bool((request.json or {}).get('force_all'))
    started = age_filler.start_autofill(force_all=force)
    if not started:
        return jsonify({'error': 'A job is already running. Cancel it first.'}), 409
    return jsonify({'ok': True, 'status': age_filler.get_status()})


@app.route('/api/admin/diagnose_index', methods=['GET'])
@role_required('admin')
def api_diagnose_index():
    """Show what ctswim.org/Meets/Results.aspx looks like from Render's
    view, AND whether direct PDF downloads work (Cloudflare often
    challenges HTML pages but allows static files)."""
    import requests as _req
    import re as _re
    import ct_pdf
    out = {}

    # Test 1: HTML index page
    page = request.args.get('page', '1')
    url = f'https://www.ctswim.org/Meets/Results.aspx?page={page}'
    out['index_test'] = {'url': url}
    try:
        r = _req.get(url, timeout=15, headers={'User-Agent': ct_pdf.USER_AGENT})
        out['index_test']['status_code'] = r.status_code
        out['index_test']['body_length'] = len(r.text)
        out['index_test']['cf_mitigated'] = r.headers.get('cf-mitigated', '')
        matches = _re.findall(r'href="([^"]*_results\.pdf)"', r.text, _re.IGNORECASE)
        out['index_test']['pdf_matches'] = len(matches)
    except Exception as e:
        out['index_test']['error'] = f"{type(e).__name__}: {e}"

    # Test 2: Direct PDF download (known LEHY 2025 12U Regional)
    pdf_url = ('https://www.ctswim.org/Customer-Content/www/Meets/'
               'LC2025/Regionals/071925lehy12u_results.pdf')
    out['pdf_test'] = {'url': pdf_url}
    try:
        r = _req.get(pdf_url, timeout=30, headers={'User-Agent': ct_pdf.USER_AGENT})
        out['pdf_test']['status_code'] = r.status_code
        out['pdf_test']['size_bytes'] = len(r.content)
        out['pdf_test']['cf_mitigated'] = r.headers.get('cf-mitigated', '')
        out['pdf_test']['is_pdf'] = r.content[:4] == b'%PDF'
        out['pdf_test']['content_type'] = r.headers.get('content-type', '')
    except Exception as e:
        out['pdf_test']['error'] = f"{type(e).__name__}: {e}"

    return jsonify(out)


@app.route('/api/admin/diagnose_pipeline', methods=['GET'])
@role_required('admin')
def api_diagnose_pipeline():
    """Trace one meet through the full age-fill pipeline and return JSON
    showing what each step produced. Use to debug 'Not found' issues.

    Query params:
      ct_id    — swimmer's CT Swim id (required)
      meet_id  — the m= number from a SwimmerAtMeet URL (required)
      reset    — '1' to clear all 'no_pdf' cache entries before running
    """
    import ct_pdf
    ct_id = request.args.get('ct_id', '').strip()
    meet_id = request.args.get('meet_id', '').strip()
    if not ct_id or not meet_id:
        return jsonify({'error': 'ct_id and meet_id required'}), 400

    out = {'ct_id': ct_id, 'meet_id': meet_id, 'steps': {}}

    if request.args.get('reset') == '1':
        with db.get_conn() as conn:
            n = conn.execute(
                "DELETE FROM meet_pdf_cache WHERE note = 'no_pdf'"
            ).rowcount
        out['cache_cleared'] = n

    # Step 1: SwimmerAtMeet metadata
    try:
        meta = ct_pdf.fetch_swimmer_at_meet(ct_id, meet_id)
        out['steps']['1_swimmer_at_meet'] = {
            'success': meta is not None,
            'meet_name': meta.get('meet_name') if meta else None,
            'start_date': meta['start_date'].isoformat() if meta and meta.get('start_date') else None,
            'end_date': meta['end_date'].isoformat() if meta and meta.get('end_date') else None,
        }
    except Exception as e:
        out['steps']['1_swimmer_at_meet'] = {'success': False, 'error': str(e)}
        return jsonify(out)

    if not meta:
        return jsonify(out)

    # Step 2: Load index from data/meet_index.json (same path auto-fill uses)
    try:
        import age_filler as _af
        index = _af._load_index_from_json()
        out['steps']['2_index_scrape'] = {
            'success': bool(index),
            'count': len(index),
            'sample_urls': [p['url'] for p in index[:3]],
            'source': 'data/meet_index.json',
        }
        # If the bundled JSON is missing, fall through to live scrape so
        # we can tell whether ctswim.org is reachable at all.
        if not index:
            live = ct_pdf.scrape_results_index()
            out['steps']['2_index_scrape']['live_fallback_count'] = len(live)
            index = live
    except Exception as e:
        out['steps']['2_index_scrape'] = {'success': False, 'error': str(e)}
        return jsonify(out)

    # Step 3: Match PDF
    try:
        pdf = ct_pdf.find_pdf_for_meet(
            meta.get('meet_name', ''),
            meta.get('start_date'),
            meta.get('end_date'),
            index,
        )
        out['steps']['3_find_pdf'] = {
            'success': pdf is not None,
            'pdf_url': pdf['url'] if pdf else None,
        }
    except Exception as e:
        out['steps']['3_find_pdf'] = {'success': False, 'error': str(e)}
        return jsonify(out)

    if not pdf:
        return jsonify(out)

    # Step 4: Download PDF
    try:
        body = ct_pdf.download_pdf(pdf['url'])
        out['steps']['4_download'] = {
            'success': body is not None,
            'size_bytes': len(body) if body else 0,
        }
    except Exception as e:
        out['steps']['4_download'] = {'success': False, 'error': str(e)}
        return jsonify(out)

    if not body:
        return jsonify(out)

    # Step 5: Parse PDF
    try:
        rows = ct_pdf.parse_results_pdf(body)
        # Look for our team's swimmers in the parsed rows
        ivy_rows = [r for r in rows if r['team'] == 'IVY']
        out['steps']['5_parse'] = {
            'success': True,
            'total_rows': len(rows),
            'ivy_rows': len(ivy_rows),
            'sample_ivy': [
                {'name': f"{r['first']} {r['last']}", 'age': r['age'],
                 'gender': r['gender'], 'name_key': r['name_key']}
                for r in ivy_rows[:5]
            ],
        }
    except Exception as e:
        out['steps']['5_parse'] = {
            'success': False,
            'error': f"{type(e).__name__}: {e}",
        }

    return jsonify(out)


@app.route('/api/team_members/autofill_ages/status', methods=['GET'])
@role_required('admin')
def api_autofill_ages_status():
    return jsonify(age_filler.get_status())


@app.route('/api/team_members/autofill_ages/cancel', methods=['POST'])
@role_required('admin')
def api_autofill_ages_cancel():
    age_filler.cancel_job()
    return jsonify({'ok': True})


@app.route('/api/dashboard', methods=['GET'])
@role_required('admin', 'coach')
def api_dashboard():
    """Return team_members with their cached best_times.
    Standards comparison happens client-side using existing helpers.
    """
    members = db.list_team_members_with_times()
    return jsonify({'members': members, 'count': len(members)})


@app.route('/api/team_members', methods=['POST'])
@role_required('admin', 'coach')
def api_create_team_member():
    data = request.json or {}
    first = (data.get('first_name') or '').strip()
    last = (data.get('last_name') or '').strip()
    if not first or not last:
        return jsonify({'error': 'First and last name are required'}), 400

    by, bm = _split_birth_month(data)
    member_id = db.create_team_member(
        first_name=first,
        last_name=last,
        roster=data.get('roster'),
        gender=data.get('gender'),
        birth_year=by,
        birth_month=bm,
        parent_email=data.get('parent_email'),
        notes=data.get('notes'),
    )
    return jsonify({'ok': True, 'id': member_id, 'member': db.get_team_member(member_id)})


@app.route('/api/team_members/<int:member_id>', methods=['PUT', 'PATCH'])
@role_required('admin', 'coach')
def api_update_team_member(member_id):
    existing = db.get_team_member(member_id)
    if not existing:
        return jsonify({'error': 'Member not found'}), 404
    data = request.json or {}
    update_fields = {
        k: data.get(k) for k in ('first_name', 'last_name', 'roster', 'gender',
                                 'parent_email', 'notes')
        if k in data
    }
    by, bm = _split_birth_month(data)
    if by is not None:
        update_fields['birth_year'] = by
    if bm is not None:
        update_fields['birth_month'] = bm
    db.update_team_member(member_id, **update_fields)
    return jsonify({'ok': True, 'member': db.get_team_member(member_id)})


@app.route('/api/team_members/<int:member_id>', methods=['DELETE'])
@role_required('admin', 'coach')
def api_delete_team_member(member_id):
    existing = db.get_team_member(member_id)
    if not existing:
        return jsonify({'error': 'Member not found'}), 404
    db.delete_team_member(member_id)
    return jsonify({'ok': True})


def _split_birth_month(data):
    """Accept either separate birth_year/birth_month fields or a combined
    'birth_month_str' shaped 'YYYY-MM' (the value an <input type=month> sends).
    Returns (year, month) — either may be None if absent/invalid.
    """
    by = data.get('birth_year')
    bm = data.get('birth_month')
    s = data.get('birth_month_str')
    if (not by or not bm) and s:
        parts = str(s).split('-')
        if len(parts) >= 2:
            by = by or parts[0]
            bm = bm or parts[1]
    return by, bm


# ===== API: Standards (read by anyone logged in; edit by admin/coach) =====

@app.route('/api/standards', methods=['GET'])
@login_required
def api_get_standards():
    return jsonify(standards_store.load())


@app.route('/api/standards/program', methods=['POST'])
@role_required('admin', 'coach')
def api_create_program():
    data = request.json or {}
    program_id = (data.get('program_id') or '').strip()
    if not program_id:
        return jsonify({'error': 'program_id is required'}), 400
    try:
        prog = standards_store.create_program(program_id, data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'ok': True, 'program_id': program_id, 'program': prog})


@app.route('/api/standards/program/<program_id>', methods=['DELETE'])
@role_required('admin', 'coach')
def api_delete_program(program_id):
    full = standards_store.load()
    if program_id not in full.get('programs', {}):
        return jsonify({'error': 'Unknown program'}), 404
    standards_store.delete_program(program_id)
    return jsonify({'ok': True})


@app.route('/api/standards/program/<program_id>/group', methods=['POST'])
@role_required('admin', 'coach')
def api_add_group(program_id):
    data = request.json or {}
    group_name = (data.get('group_name') or '').strip()
    try:
        prog = standards_store.add_group(program_id, group_name)
    except KeyError:
        return jsonify({'error': 'Unknown program'}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'ok': True, 'program': prog})


@app.route('/api/standards/program/<program_id>/group/<path:group>', methods=['DELETE'])
@role_required('admin', 'coach')
def api_delete_group(program_id, group):
    full = standards_store.load()
    prog = full.get('programs', {}).get(program_id)
    if not prog or group not in prog.get('groups', {}):
        return jsonify({'error': 'Unknown program/group'}), 404
    standards_store.delete_group(program_id, group)
    return jsonify({'ok': True})


@app.route('/api/standards/program/<program_id>/metadata', methods=['PATCH'])
@role_required('admin', 'coach')
def api_update_program_metadata(program_id):
    data = request.json or {}
    try:
        prog = standards_store.update_program_metadata(program_id, data)
    except KeyError:
        return jsonify({'error': 'Unknown program'}), 404
    return jsonify({'ok': True, 'program': prog})


@app.route('/api/standards/program/<program_id>/group/<path:group>/event/<int:event_index>',
           methods=['PATCH'])
@role_required('admin', 'coach')
def api_update_event(program_id, group, event_index):
    """Update a single event row.
    For non-USA: body = { gender_key: { course: time, ... }, ..., event_name?: str }
    For USA:     body = { times: [time per level], event_name?: str }
    """
    data = request.json or {}
    full = standards_store.load()
    if program_id not in full.get('programs', {}):
        return jsonify({'error': 'Unknown program'}), 404
    prog = full['programs'][program_id]
    if group not in prog.get('groups', {}):
        return jsonify({'error': 'Unknown group'}), 404

    grp = prog['groups'][group]
    if event_index < 0 or event_index >= len(grp.get('events', [])):
        return jsonify({'error': 'Event index out of range'}), 400

    # Optional rename
    if 'event_name' in data and data['event_name']:
        standards_store.rename_event(program_id, group, event_index, data['event_name'])

    if prog.get('multi_level'):
        times = data.get('times', [])
        if not isinstance(times, list) or len(times) != len(grp['levels']):
            return jsonify({'error': f"Expected list of {len(grp['levels'])} times"}), 400
        for li, t in enumerate(times):
            standards_store.update_usa_cell(program_id, group, event_index, li, t)
    else:
        for gender_key in prog.get('gender_keys', []):
            cell = data.get(gender_key) or {}
            for course in ['SCY', 'LCM']:
                if course in cell:
                    standards_store.update_event_time(program_id, group, gender_key, course,
                                                      event_index, cell[course])

    return jsonify({'ok': True, 'program': standards_store.load()['programs'][program_id]})


@app.route('/api/standards/program/<program_id>/group/<path:group>/event', methods=['POST'])
@role_required('admin', 'coach')
def api_add_event(program_id, group):
    """Add a new event. Body: { event_name: str, ...same payload shape as update }"""
    data = request.json or {}
    name = (data.get('event_name') or '').strip()
    if not name:
        return jsonify({'error': 'event_name is required'}), 400
    full = standards_store.load()
    if program_id not in full.get('programs', {}) or group not in full['programs'][program_id].get('groups', {}):
        return jsonify({'error': 'Unknown program/group'}), 404
    prog = full['programs'][program_id]

    if prog.get('multi_level'):
        # Map list of times to {level: time}
        times = data.get('times', [])
        levels = prog['groups'][group]['levels']
        payload = {levels[i]: times[i] if i < len(times) else '' for i in range(len(levels))}
    else:
        payload = {gk: data.get(gk) or {} for gk in prog.get('gender_keys', [])}

    new_idx = standards_store.add_event(program_id, group, name, payload)
    return jsonify({'ok': True, 'index': new_idx,
                    'program': standards_store.load()['programs'][program_id]})


@app.route('/api/standards/program/<program_id>/group/<path:group>/event/<int:event_index>',
           methods=['DELETE'])
@role_required('admin', 'coach')
def api_delete_event(program_id, group, event_index):
    full = standards_store.load()
    if program_id not in full.get('programs', {}) or group not in full['programs'][program_id].get('groups', {}):
        return jsonify({'error': 'Unknown program/group'}), 404
    try:
        standards_store.delete_event(program_id, group, event_index)
    except IndexError:
        return jsonify({'error': 'Event index out of range'}), 400
    return jsonify({'ok': True, 'program': standards_store.load()['programs'][program_id]})


@app.route('/api/batch/start', methods=['POST'])
@role_required('admin')
def batch_start():
    mode = request.json.get('mode', 'last_names')
    team_filter = request.json.get('team_filter', '').strip()

    if mode == 'last_names':
        raw = request.json.get('last_names', '')
        names = [n.strip() for n in raw.split('\n') if n.strip()] if isinstance(raw, str) else [str(n).strip() for n in raw if str(n).strip()]
        if not names:
            return jsonify({'error': 'Provide at least one last name'}), 400
        started = batch.start_batch_last_names(names, team_filter, ct_search_swimmers, ct_fetch_and_cache_swimmer)
    elif mode == 'team_code':
        team_code = request.json.get('team_code', '').strip()
        if not team_code:
            return jsonify({'error': 'Provide a team code'}), 400
        started = batch.start_batch_team_code(team_code, ct_search_swimmers, ct_fetch_and_cache_swimmer)
    elif mode == 'team_roster':
        only_new = bool(request.json.get('only_new'))
        team_members = db.list_team_members_with_times() if only_new else db.list_team_members()
        from pdf_parser import normalize_for_match
        included = []
        skipped_existing = 0
        for m in team_members:
            # When only_new is requested, skip swimmers who already have cached times.
            # An unmatched swimmer (no ct_id) is always included — they need the search.
            if only_new and m.get('ct_id') and (m.get('best_times') or []):
                skipped_existing += 1
                continue
            full_name = f"{m['first_name']} {m['last_name']}"
            included.append({
                'first_name': m['first_name'],
                'last_name': m['last_name'],
                'full_name': full_name,
                'search_key': m['last_name'].split()[0] if m['last_name'] else '',
                'match_key': normalize_for_match(full_name),
                'roster': m.get('roster') or '',
            })
        if not included:
            msg = ('All matched swimmers already have cached times. Uncheck '
                   '"Only newly added" to refresh everyone.') if only_new \
                  else 'Roster is empty. Add swimmers in the Roster tab first.'
            return jsonify({'error': msg}), 400
        started = batch.start_batch_member_directory(included, ct_search_swimmers, ct_fetch_and_cache_swimmer)
    else:
        return jsonify({'error': 'Invalid mode'}), 400

    if not started:
        return jsonify({'error': 'A batch job is already running. Cancel it first.'}), 409
    return jsonify({'ok': True, 'status': batch.get_status()})


@app.route('/api/batch/status', methods=['GET'])
@role_required('admin')
def batch_status():
    status = batch.get_status()
    status['recent_log'] = batch.get_recent_log(20)
    return jsonify(status)


@app.route('/api/batch/cancel', methods=['POST'])
@role_required('admin')
def batch_cancel():
    batch.cancel_job()
    return jsonify({'ok': True})


if __name__ == '__main__':
    app.run(debug=True, port=5000)
