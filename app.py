import os
import re
from functools import wraps
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, jsonify, request, session, redirect, url_for

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-me-in-production-please-a8f3d9x2')

BASE_URL = "https://fast.ctswim.org/CTNet"

# Simple hardcoded credentials (MVP). Override via env vars in production.
APP_USERNAME = os.environ.get('APP_USERNAME', 'admin')
APP_PASSWORD = os.environ.get('APP_PASSWORD', 'swimgoal')


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('logged_in'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('login', next=request.path))
        return view(*args, **kwargs)
    return wrapped


def get_session_and_tokens():
    session = requests.Session()
    r = session.get(f"{BASE_URL}/DatabaseQuery.aspx?Opt=BT", timeout=10)
    html = r.text
    vs = re.search(r'name="__VIEWSTATE"[^>]*value="([^"]*)"', html)
    vsg = re.search(r'name="__VIEWSTATEGENERATOR"[^>]*value="([^"]*)"', html)
    ev = re.search(r'name="__EVENTVALIDATION"[^>]*value="([^"]*)"', html)
    return session, {
        '__VIEWSTATE': vs.group(1) if vs else '',
        '__VIEWSTATEGENERATOR': vsg.group(1) if vsg else '',
        '__EVENTVALIDATION': ev.group(1) if ev else '',
    }


def extract_tokens(html):
    vs = re.search(r'name="__VIEWSTATE"[^>]*value="([^"]*)"', html)
    vsg = re.search(r'name="__VIEWSTATEGENERATOR"[^>]*value="([^"]*)"', html)
    ev = re.search(r'name="__EVENTVALIDATION"[^>]*value="([^"]*)"', html)
    return {
        '__VIEWSTATE': vs.group(1) if vs else '',
        '__VIEWSTATEGENERATOR': vsg.group(1) if vsg else '',
        '__EVENTVALIDATION': ev.group(1) if ev else '',
    }


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if username == APP_USERNAME and password == APP_PASSWORD:
            session['logged_in'] = True
            session['username'] = username
            session.permanent = True
            next_url = request.args.get('next') or url_for('index')
            return redirect(next_url)
        error = 'Invalid username or password'
    if session.get('logged_in'):
        return redirect(url_for('index'))
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    return render_template('index.html')


@app.route('/api/search', methods=['POST'])
@login_required
def search_swimmer():
    last_name = request.json.get('last_name', '').strip()
    if not last_name:
        return jsonify({'error': 'Last name is required'}), 400

    try:
        session, tokens = get_session_and_tokens()
        data = {
            **tokens,
            'ddl_SearchType': 'Last Name Starts With',
            'tb_SearchFor': last_name,
            '_ctl0': 'Search'
        }
        r = session.post(f"{BASE_URL}/DatabaseQuery.aspx?Opt=BT", data=data, timeout=10)
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

        new_tokens = extract_tokens(html)
        cookies = session.cookies.get_dict()

        return jsonify({
            'swimmers': swimmers,
            'tokens': new_tokens,
            'cookies': cookies
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/best_times', methods=['POST'])
@login_required
def get_best_times():
    swimmer_id = request.json.get('swimmer_id', '')
    tokens = request.json.get('tokens', {})
    cookies = request.json.get('cookies', {})

    if not swimmer_id or not tokens:
        return jsonify({'error': 'Missing swimmer_id or tokens'}), 400

    try:
        session = requests.Session()
        for k, v in cookies.items():
            session.cookies.set(k, v)

        data = {
            **tokens,
            'SwimmerSelect': swimmer_id,
            'GoB': 'Display Times'
        }
        r = session.post(f"{BASE_URL}/DatabaseQuery.aspx?Opt=BT", data=data, timeout=10)
        html = r.text

        soup = BeautifulSoup(html, 'html.parser')

        swimmer_name = ''
        label2 = soup.find('span', id='Label2')
        if label2:
            swimmer_name = label2.get_text(strip=True)

        events = []
        table = soup.find('table', id='Table1')
        if table:
            rows = table.find_all('tr')
            for row in rows[1:]:
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

        return jsonify({
            'swimmer_name': swimmer_name,
            'events': events
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/event_history', methods=['POST'])
@login_required
def get_event_history():
    history_url = request.json.get('history_url', '')
    if not history_url:
        return jsonify({'error': 'Missing history URL'}), 400

    try:
        full_url = f"{BASE_URL}/{history_url}"
        r = requests.get(full_url, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')

        title = ''
        label2 = soup.find('span', id='Label2')
        label3 = soup.find('span', id='Label3')
        if label2:
            title = label2.get_text(strip=True)
        if label3:
            title = label3.get_text(strip=True) + ' - ' + title

        history = []
        table = soup.find('table', id='Table1')
        if table:
            rows = table.find_all('tr')
            for row in rows[1:]:
                cells = row.find_all('td')
                if len(cells) >= 3:
                    time_val = cells[0].get_text(strip=True)
                    swim_type = cells[1].get_text(strip=True)
                    date_val = cells[2].get_text(strip=True)
                    if time_val and time_val != 'Time':
                        history.append({
                            'time': time_val,
                            'date': date_val,
                            'meet': swim_type
                        })

        return jsonify({
            'title': title,
            'history': history
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)
