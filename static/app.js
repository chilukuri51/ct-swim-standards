// ===== TAB NAVIGATION =====
document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById(btn.dataset.tab).classList.add('active');
    });
});

// ===== TIME STANDARDS TAB =====
const standardType = document.getElementById('standardType');
const ageGroupSel = document.getElementById('ageGroup');
const courseType = document.getElementById('courseType');
const courseControl = document.getElementById('courseControl');

function getAgeGroupsList() {
    const type = standardType.value;
    if (type === 'ct') return Object.keys(CT_STANDARDS);
    if (type === 'ez') return Object.keys(EZ_STANDARDS);
    const groups = new Set();
    for (const key of Object.keys(USA_STANDARDS)) {
        const parts = key.match(/^(.+?)\s+(Girls|Boys)\s+(SCY|LCM|SCM)$/);
        if (parts) groups.add(parts[1]);
    }
    return [...groups];
}

function getCoursesList() {
    const type = standardType.value;
    if (type === 'ct') return ['SCY', 'LCM'];
    if (type === 'ez') return ['LCM', 'SCY'];
    // USA: check what courses exist for selected age group
    const age = ageGroupSel.value;
    const courses = new Set();
    for (const key of Object.keys(USA_STANDARDS)) {
        if (key.startsWith(age)) {
            const m = key.match(/(SCY|LCM|SCM)$/);
            if (m) courses.add(m[1]);
        }
    }
    return courses.size > 0 ? [...courses] : ['SCY'];
}

function updateAgeGroups() {
    const groups = getAgeGroupsList();
    ageGroupSel.innerHTML = groups.map(g => `<option value="${g}">${g}</option>`).join('');
    updateCourses();
}

function updateCourses() {
    courseControl.classList.remove('hidden');
    const courses = getCoursesList();
    courseType.innerHTML = courses.map(c => `<option value="${c}">${c}</option>`).join('');
    renderStandards();
}

function renderStandards() {
    const type = standardType.value;
    const age = ageGroupSel.value;
    const course = courseType.value;
    const container = document.getElementById('standardsTable');

    if (type === 'ct') renderCTTable(age, course, container);
    else if (type === 'ez') renderEZTable(age, course, container);
    else renderUSATable(age, course, container);
}

function renderCTTable(age, course, container) {
    const data = CT_STANDARDS[age];
    if (!data) { container.innerHTML = '<p style="padding:1rem">No data available</p>'; return; }
    let html = `<table><thead><tr><th>Event</th><th>Girls ${course}</th><th>Boys ${course}</th></tr></thead><tbody>`;
    data.events.forEach((event, i) => {
        html += `<tr><td class="event-name">${event}</td><td>${data.girls[course]?.[i] || 'N/A'}</td><td>${data.boys[course]?.[i] || 'N/A'}</td></tr>`;
    });
    container.innerHTML = html + '</tbody></table>';
}

function renderEZTable(age, course, container) {
    const data = EZ_STANDARDS[age];
    if (!data) { container.innerHTML = '<p style="padding:1rem">No data available</p>'; return; }
    let html = `<table><thead><tr><th>Event</th><th>Women ${course}</th><th>Men ${course}</th></tr></thead><tbody>`;
    data.events.forEach((event, i) => {
        html += `<tr><td class="event-name">${event}</td><td>${data.women[course]?.[i] || 'N/A'}</td><td>${data.men[course]?.[i] || 'N/A'}</td></tr>`;
    });
    container.innerHTML = html + '</tbody></table>';
}

function renderUSATable(age, course, container) {
    let html = '';
    ['Girls', 'Boys'].forEach(g => {
        const key = `${age} ${g} ${course}`;
        const data = USA_STANDARDS[key];
        if (!data) return;
        html += `<h3 style="padding:0.75rem 1rem;margin:0;color:#003366;${g==='Boys'?'margin-top:1rem':''}">${age} ${g} - ${course}</h3>`;
        html += `<table><thead><tr><th>Event</th>`;
        data.levels.forEach(l => { html += `<th>${l}</th>`; });
        html += `</tr></thead><tbody>`;
        data.events.forEach((event, i) => {
            html += `<tr><td class="event-name">${event}</td>`;
            data.times[i].forEach(t => { html += `<td>${t}</td>`; });
            html += `</tr>`;
        });
        html += '</tbody></table>';
    });
    if (!html) html = '<p style="padding:1rem">No data available for this combination</p>';
    container.innerHTML = html;
}

standardType.addEventListener('change', updateAgeGroups);
ageGroupSel.addEventListener('change', updateCourses);
courseType.addEventListener('change', renderStandards);
updateAgeGroups();


// ===== SWIMMER SEARCH =====
let currentTokens = null;
let currentCookies = null;

const lastNameInput = document.getElementById('lastName');
const dobInput = document.getElementById('dob');
const genderInput = document.getElementById('gender');
const searchBtn = document.getElementById('searchBtn');
const searchResults = document.getElementById('searchResults');
const swimmerList = document.getElementById('swimmerList');
const reportCard = document.getElementById('reportCard');
const loading = document.getElementById('loading');
const errorMsg = document.getElementById('errorMsg');

function showLoading() { loading.classList.remove('hidden'); errorMsg.classList.add('hidden'); }
function hideLoading() { loading.classList.add('hidden'); }
function showError(msg) { errorMsg.textContent = msg; errorMsg.classList.remove('hidden'); }
function hideAllSections() {
    searchResults.classList.add('hidden');
    reportCard.classList.add('hidden');
    document.getElementById('eventHistory').classList.add('hidden');
    errorMsg.classList.add('hidden');
}

searchBtn.addEventListener('click', async () => {
    const lastName = lastNameInput.value.trim();
    if (!lastName) { showError('Please enter a last name'); return; }
    hideAllSections(); showLoading(); searchBtn.disabled = true;
    try {
        const resp = await fetch('/api/search', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ last_name: lastName })
        });
        const data = await resp.json();
        hideLoading(); searchBtn.disabled = false;
        if (data.error) { showError(data.error); return; }
        if (!data.swimmers?.length) { showError('No swimmers found'); return; }
        currentTokens = data.tokens;
        currentCookies = data.cookies;
        swimmerList.innerHTML = '';
        data.swimmers.forEach(s => {
            const item = document.createElement('div');
            item.className = 'swimmer-item';
            item.innerHTML = `<span class="name">${s.name}</span><span class="team">${s.team}</span>`;
            item.addEventListener('click', () => loadBestTimes(s.id, s.name));
            swimmerList.appendChild(item);
        });
        searchResults.classList.remove('hidden');
    } catch (e) { hideLoading(); searchBtn.disabled = false; showError('Search failed.'); }
});
lastNameInput.addEventListener('keydown', e => { if (e.key === 'Enter') searchBtn.click(); });

// ===== BEST TIMES + REPORT CARD =====
async function loadBestTimes(swimmerId, name) {
    hideAllSections(); showLoading();
    try {
        const resp = await fetch('/api/best_times', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ swimmer_id: swimmerId, tokens: currentTokens, cookies: currentCookies })
        });
        const data = await resp.json();
        hideLoading();
        if (data.error) { showError(data.error); return; }

        const dob = dobInput.value;
        const gender = genderInput.value;
        const ageGrp = getAgeGroup(dob);
        const age = getAge(dob);
        const hasProfile = !!(ageGrp && gender);

        document.getElementById('swimmerName').textContent = data.swimmer_name || name;
        let metaParts = [];
        if (age !== null) metaParts.push(`Age: ${age}`);
        if (ageGrp) metaParts.push(`Age Group: ${ageGrp}`);
        if (gender) metaParts.push(gender === 'F' ? 'Female' : 'Male');
        document.getElementById('swimmerMeta').textContent = metaParts.join(' | ');

        const noNotice = document.getElementById('noProfileNotice');
        noNotice.classList.toggle('hidden', hasProfile);
        document.getElementById('legendBar').classList.toggle('hidden', !hasProfile);

        if (!data.events?.length) {
            document.getElementById('bestTimesTable').innerHTML = '<p style="padding:1rem">No times found.</p>';
            ['champSection','usaSection','closestSection'].forEach(id => document.getElementById(id).classList.add('hidden'));
            reportCard.classList.remove('hidden');
            return;
        }

        // Process all events
        const scyEvents = [], lcmEvents = [];
        const usaCounts = { B: 0, BB: 0, A: 0, AA: 0, AAA: 0, AAAA: 0 };
        const champResults = { 'CT AG': { qual: [], close: [], far: [] }, 'EZ': { qual: [], close: [], far: [] } };
        const closestToCut = []; // events closest to their next cut

        data.events.forEach(ev => {
            const eventInfo = normalizeEvent(ev.event);
            let standards = { usa: [], champ: [] };
            let comparison = { usaAchieved: [], usaNext: null, champAchieved: [], champNext: null, highestUSA: null };

            if (eventInfo && hasProfile) {
                standards = lookupStandards(eventInfo, ageGrp, gender);
                const swimSecs = timeToSeconds(ev.time);
                comparison = compareToStandards(swimSecs, standards);

                // Count USA levels - count at HIGHEST level only
                if (comparison.highestUSA) {
                    usaCounts[comparison.highestUSA.type]++;
                }

                // Championship tracking
                for (const cs of standards.champ) {
                    const stdSecs = timeToSeconds(cs.time);
                    const gap = swimSecs - stdSecs;
                    const type = cs.type;
                    if (gap <= 0) {
                        champResults[type].qual.push({ event: ev.event, gap: gap });
                    } else if (gap <= 5) {
                        champResults[type].close.push({ event: ev.event, gap: gap });
                    } else {
                        champResults[type].far.push({ event: ev.event, gap: gap });
                    }
                }

                // Closest to cut (USA next or champ next)
                const nextTargets = [];
                if (comparison.usaNext) {
                    const gap = swimSecs - timeToSeconds(comparison.usaNext.time);
                    nextTargets.push({ event: ev.event, target: comparison.usaNext, gap, current: ev.time });
                }
                if (comparison.champNext) {
                    const gap = swimSecs - timeToSeconds(comparison.champNext.time);
                    nextTargets.push({ event: ev.event, target: comparison.champNext, gap, current: ev.time });
                }
                nextTargets.forEach(nt => { if (nt.gap > 0 && nt.gap < 30) closestToCut.push(nt); });
            }

            const processed = { ...ev, eventInfo, standards, comparison };
            if (eventInfo?.course === 'LCM') lcmEvents.push(processed);
            else scyEvents.push(processed);
        });

        if (hasProfile) {
            buildChampSection(champResults);
            buildUSALadder(usaCounts);
            buildClosestSection(closestToCut);
        } else {
            ['champSection','usaSection','closestSection'].forEach(id => document.getElementById(id).classList.add('hidden'));
        }

        buildBestTimesTable(scyEvents, lcmEvents, hasProfile);
        reportCard.classList.remove('hidden');
    } catch (e) { hideLoading(); showError('Failed to load best times.'); }
}

// ===== CHAMPIONSHIP QUALIFICATION SECTION =====
function buildChampSection(results) {
    const champCards = document.getElementById('champCards');
    let html = '';

    const champInfo = {
        'CT AG': { title: 'CT Age Group Championships', color: '#dc2626' },
        'EZ': { title: 'Eastern Zone Championships', color: '#9333ea' }
    };

    for (const [type, info] of Object.entries(champInfo)) {
        const r = results[type];
        if (!r) continue;
        const total = r.qual.length + r.close.length + r.far.length;
        if (total === 0) continue;

        const isQualified = r.qual.length > 0;
        const statusClass = r.qual.length > 0 ? 'qualified' : 'not-qualified';
        const statusBadge = r.qual.length > 0
            ? `<span class="champ-status yes">Qualified</span>`
            : r.close.length > 0
                ? `<span class="champ-status partial">Close</span>`
                : `<span class="champ-status no">Not Yet</span>`;

        html += `<div class="champ-card ${statusClass}">
            <div class="champ-card-header">
                <span class="champ-card-title">${info.title}</span>
                ${statusBadge}
            </div>
            <div class="champ-qualified-count">${r.qual.length} of ${total} events qualified</div>
            <div class="champ-events">`;

        r.qual.forEach(e => {
            html += `<span class="champ-event-chip qual">${e.event}</span>`;
        });
        r.close.forEach(e => {
            html += `<span class="champ-event-chip close">${e.event} (-${e.gap.toFixed(1)}s)</span>`;
        });
        r.far.slice(0, 5).forEach(e => {
            html += `<span class="champ-event-chip far">${e.event}</span>`;
        });

        html += `</div></div>`;
    }

    champCards.innerHTML = html;
    document.getElementById('champSection').classList.toggle('hidden', html === '');
}

// ===== USA MOTIVATIONAL LADDER =====
function buildUSALadder(counts) {
    const levels = [
        { key: 'B', label: 'B', cls: 'level-b' },
        { key: 'BB', label: 'BB', cls: 'level-bb' },
        { key: 'A', label: 'A', cls: 'level-a' },
        { key: 'AA', label: 'AA', cls: 'level-aa' },
        { key: 'AAA', label: 'AAA', cls: 'level-aaa' },
        { key: 'AAAA', label: 'AAAA', cls: 'level-aaaa' },
    ];

    let html = '';
    levels.forEach(l => {
        const count = counts[l.key] || 0;
        const hasClass = count > 0 ? ' has-events' : '';
        html += `<div class="ladder-level ${l.cls}${hasClass}">
            <div class="level-name">${l.label}</div>
            <div class="level-count">${count}</div>
            <div class="level-label">events</div>
        </div>`;
    });

    document.getElementById('usaLadder').innerHTML = html;
    document.getElementById('usaSection').classList.remove('hidden');
}

// ===== CLOSEST TO CUT =====
function buildClosestSection(items) {
    if (items.length === 0) { document.getElementById('closestSection').classList.add('hidden'); return; }

    items.sort((a, b) => a.gap - b.gap);
    const top = items.slice(0, 6);

    let html = '';
    top.forEach(item => {
        const gapClass = item.gap <= 1 ? 'very-close' : item.gap <= 3 ? 'close' : '';
        const pct = Math.max(0, Math.min(100, (1 - item.gap / 10) * 100));
        const barColor = item.target.cssClass.replace('badge-', '');
        const colors = { b: '#ea580c', bb: '#ca8a04', a: '#16a34a', aa: '#0891b2', aaa: '#2563eb', aaaa: '#7c3aed', ct: '#dc2626', ez: '#9333ea' };

        html += `<div class="closest-card">
            <div class="cc-event">${item.event}</div>
            <div class="cc-times">
                <span class="cc-current">${item.current}</span>
                <span class="cc-target"><span class="badge ${item.target.cssClass}">${item.target.type}</span> ${item.target.time}</span>
            </div>
            <div class="cc-gap ${gapClass}">-${item.gap.toFixed(2)}s to go</div>
            <div class="cc-progress"><div class="cc-progress-bar" style="width:${pct}%;background:${colors[barColor] || '#64748b'}"></div></div>
        </div>`;
    });

    document.getElementById('closestCards').innerHTML = html;
    document.getElementById('closestSection').classList.remove('hidden');
}

// ===== BEST TIMES TABLE =====
function buildBestTimesTable(scyEvents, lcmEvents, hasProfile) {
    const cols = hasProfile ? 7 : 4;
    let html = `<table><thead><tr>
        <th>Event</th><th>Best Time</th><th>Date</th>`;
    if (hasProfile) html += `<th>Level</th><th>Next Target</th><th>Gap</th>`;
    html += `<th>History</th></tr></thead><tbody>`;

    if (scyEvents.length > 0) {
        html += `<tr class="course-separator"><td colspan="${cols}">Short Course Yards (SCY)</td></tr>`;
        scyEvents.forEach(ev => { html += buildRow(ev, hasProfile); });
    }
    if (lcmEvents.length > 0) {
        html += `<tr class="course-separator"><td colspan="${cols}">Long Course Meters (LCM)</td></tr>`;
        lcmEvents.forEach(ev => { html += buildRow(ev, hasProfile); });
    }

    html += '</tbody></table>';
    document.getElementById('bestTimesTable').innerHTML = html;
}

function buildRow(ev, hasProfile) {
    const c = ev.comparison;
    const hasAchievements = c && (c.usaAchieved.length > 0 || c.champAchieved.length > 0);
    let html = `<tr>`;
    html += `<td class="event-name">${ev.event}</td>`;
    html += `<td class="time-cell${hasAchievements ? ' fast' : ''}">${ev.time}</td>`;
    html += `<td>${ev.date}</td>`;

    if (hasProfile) {
        // Level achieved (show highest USA + champ badges)
        html += '<td><div class="achieved-badges">';
        if (c.highestUSA) {
            html += `<span class="badge ${c.highestUSA.cssClass}" title="Cut: ${c.highestUSA.time}">${c.highestUSA.type}</span>`;
        }
        c.champAchieved.forEach(a => {
            html += `<span class="badge ${a.cssClass}" title="Cut: ${a.time}">${a.type}</span>`;
        });
        if (!c.highestUSA && c.champAchieved.length === 0) {
            html += ev.standards.usa.length > 0 || ev.standards.champ.length > 0
                ? '<span style="color:#94a3b8;font-size:0.75rem">--</span>'
                : '<span style="color:#cbd5e0;font-size:0.75rem">N/A</span>';
        }
        html += '</div></td>';

        // Next target - show the NEXT achievable one (not the hardest)
        html += '<td class="next-target-cell">';
        const nextTarget = c.usaNext || c.champNext;
        if (nextTarget) {
            const swimSecs = timeToSeconds(ev.time);
            const targetSecs = timeToSeconds(nextTarget.time);
            // Progress within current band
            const allStds = [...ev.standards.usa, ...ev.standards.champ].map(s => ({ ...s, secs: timeToSeconds(s.time) })).filter(s => isFinite(s.secs));
            const prevCut = allStds.filter(s => swimSecs <= s.secs).sort((a, b) => b.secs - a.secs)[0];
            const prevSecs = prevCut ? prevCut.secs : targetSecs + 30;
            const range = prevSecs - targetSecs;
            const pct = range > 0 ? Math.min(100, ((prevSecs - swimSecs) / range) * 100) : 0;
            const barCls = nextTarget.cssClass.replace('badge-', '');
            const colors = { b: '#ea580c', bb: '#ca8a04', a: '#16a34a', aa: '#0891b2', aaa: '#2563eb', aaaa: '#7c3aed', ct: '#dc2626', ez: '#9333ea' };

            html += `<div class="nt-label"><span class="badge ${nextTarget.cssClass}">${nextTarget.type}</span></div>`;
            html += `<div class="nt-time">${nextTarget.time}</div>`;
            html += `<div class="nt-progress"><div class="nt-progress-bar" style="width:${pct}%;background:${colors[barCls] || '#64748b'}"></div></div>`;
        } else if (hasAchievements) {
            html += '<span style="color:#16a34a;font-size:0.75rem;font-weight:600">All cuts!</span>';
        } else {
            html += '<span style="color:#cbd5e0;font-size:0.75rem">--</span>';
        }
        html += '</td>';

        // Gap
        html += '<td>';
        if (nextTarget) {
            const gap = timeToSeconds(ev.time) - timeToSeconds(nextTarget.time);
            const gapClass = gap <= 1 ? 'gap-cell very-close' : gap <= 3 ? 'gap-cell close' : 'gap-cell';
            html += `<span class="${gapClass}">-${gap.toFixed(2)}s</span>`;
        } else if (hasAchievements) {
            html += '<span class="gap-cell achieved">Done</span>';
        } else {
            html += '<span style="color:#cbd5e0;font-size:0.75rem">--</span>';
        }
        html += '</td>';
    }

    const historyLink = ev.history_url
        ? `<span class="event-link" onclick="loadEventHistory('${encodeURIComponent(ev.history_url)}', '${ev.event.replace(/'/g, "\\'")}')">View</span>`
        : '';
    html += `<td>${historyLink}</td></tr>`;
    return html;
}

// ===== EVENT HISTORY =====
async function loadEventHistory(encodedUrl, eventName) {
    const url = decodeURIComponent(encodedUrl);
    hideAllSections(); showLoading();
    try {
        const resp = await fetch('/api/event_history', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ history_url: url })
        });
        const data = await resp.json();
        hideLoading();
        if (data.error) { showError(data.error); return; }

        document.getElementById('historyTitle').textContent = data.title || `Event History: ${eventName}`;

        if (!data.history?.length) {
            document.getElementById('historyTable').innerHTML = '<p style="padding:1rem">No history found.</p>';
        } else {
            let html = `<table><thead><tr><th>Time</th><th>Swim</th><th>Date</th><th>Improvement</th></tr></thead><tbody>`;
            for (let i = 0; i < data.history.length; i++) {
                const h = data.history[i];
                const currSecs = timeToSeconds(h.time);
                let improvement = '<span style="color:#94a3b8">First swim</span>';
                if (i < data.history.length - 1) {
                    const prevSecs = timeToSeconds(data.history[i + 1].time);
                    const diff = prevSecs - currSecs;
                    if (diff > 0) improvement = `<span style="color:#16a34a;font-weight:600">-${diff.toFixed(2)}s</span>`;
                    else if (diff < 0) improvement = `<span style="color:#dc2626">+${Math.abs(diff).toFixed(2)}s</span>`;
                    else improvement = '<span style="color:#94a3b8">0.00s</span>';
                }
                html += `<tr><td class="time-cell">${h.time}</td><td>${h.meet}</td><td>${h.date}</td><td>${improvement}</td></tr>`;
            }
            if (data.history.length >= 2) {
                const firstSecs = timeToSeconds(data.history[data.history.length - 1].time);
                const lastSecs = timeToSeconds(data.history[0].time);
                const totalDrop = firstSecs - lastSecs;
                html += `<tr style="border-top:2px solid #003366;font-weight:700">
                    <td colspan="3" style="text-align:right">Total Improvement:</td>
                    <td><span style="color:${totalDrop > 0 ? '#16a34a' : '#dc2626'}">${totalDrop > 0 ? '-' : '+'}${Math.abs(totalDrop).toFixed(2)}s</span></td>
                </tr>`;
            }
            html += '</tbody></table>';
            document.getElementById('historyTable').innerHTML = html;
        }
        document.getElementById('eventHistory').classList.remove('hidden');
    } catch (e) { hideLoading(); showError('Failed to load event history.'); }
}

// Back buttons
document.getElementById('backToResults').addEventListener('click', () => {
    reportCard.classList.add('hidden'); searchResults.classList.remove('hidden');
});
document.getElementById('backToTimes').addEventListener('click', () => {
    document.getElementById('eventHistory').classList.add('hidden'); reportCard.classList.remove('hidden');
});
window.loadEventHistory = loadEventHistory;
