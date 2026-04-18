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
let ageMode = 'dob'; // 'dob' or 'age'

const lastNameInput = document.getElementById('lastName');
const dobInput = document.getElementById('dob');
const ageInput = document.getElementById('ageInput');
const genderInput = document.getElementById('gender');

// Age/DOB toggle
document.querySelectorAll('.age-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.age-tab-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        ageMode = btn.dataset.mode;
        if (ageMode === 'dob') {
            dobInput.classList.remove('hidden');
            ageInput.classList.add('hidden');
        } else {
            dobInput.classList.add('hidden');
            ageInput.classList.remove('hidden');
        }
    });
});

// Resolve current age (returns number or null) and age group
function getCurrentAge() {
    if (ageMode === 'age') {
        const v = parseInt(ageInput.value);
        return isFinite(v) && v > 0 ? v : null;
    }
    const dob = dobInput.value;
    if (!dob) return null;
    return getAge(dob);
}

function getCurrentAgeGroup() {
    const age = getCurrentAge();
    if (age === null) return null;
    if (age <= 10) return '10/Under';
    if (age <= 12) return '11/12';
    if (age <= 14) return '13/14';
    if (age <= 16) return '15/16';
    return '17/18';
}
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

        const gender = genderInput.value;
        const ageGrp = getCurrentAgeGroup();
        const age = getCurrentAge();
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
        // Split USA counts by course
        const usaCountsByCourse = {
            SCY: { B: 0, BB: 0, A: 0, AA: 0, AAA: 0, AAAA: 0 },
            LCM: { B: 0, BB: 0, A: 0, AA: 0, AAA: 0, AAAA: 0 }
        };
        // Split champ results by course
        const champResultsByCourse = {
            SCY: { 'CT AG': { qual: [], close: [], far: [] }, 'EZ': { qual: [], close: [], far: [] } },
            LCM: { 'CT AG': { qual: [], close: [], far: [] }, 'EZ': { qual: [], close: [], far: [] } }
        };
        const closestToCut = [];

        data.events.forEach(ev => {
            const eventInfo = normalizeEvent(ev.event);
            let standards = { usa: [], champ: [] };
            let comparison = { usaAchieved: [], usaNext: null, champAchieved: [], champNext: null, highestUSA: null };

            if (eventInfo && hasProfile) {
                standards = lookupStandards(eventInfo, ageGrp, gender);
                const swimSecs = timeToSeconds(ev.time);
                comparison = compareToStandards(swimSecs, standards);
                const course = eventInfo.course; // 'SCY' or 'LCM'

                // Count USA levels - at HIGHEST achieved level, per course
                if (comparison.highestUSA && usaCountsByCourse[course]) {
                    usaCountsByCourse[course][comparison.highestUSA.type]++;
                }

                // Championship tracking per course
                for (const cs of standards.champ) {
                    const stdSecs = timeToSeconds(cs.time);
                    const gap = swimSecs - stdSecs;
                    const type = cs.type;
                    const bucket = champResultsByCourse[course]?.[type];
                    if (!bucket) continue;
                    if (gap <= 0) bucket.qual.push({ event: ev.event, gap: gap });
                    else if (gap <= 5) bucket.close.push({ event: ev.event, gap: gap });
                    else bucket.far.push({ event: ev.event, gap: gap });
                }

                // Closest to cut
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
            buildChampSection(champResultsByCourse);
            buildUSALadder(usaCountsByCourse);
            buildClosestSection(closestToCut);
        } else {
            ['champSection','usaSection','closestSection'].forEach(id => document.getElementById(id).classList.add('hidden'));
        }

        buildBestTimesTable(scyEvents, lcmEvents, hasProfile);

        // Build progression dropdown from events that have history URLs
        const eventsWithHistory = data.events.filter(e => e.history_url);
        buildProgressDropdown(eventsWithHistory);

        reportCard.classList.remove('hidden');
    } catch (e) { hideLoading(); showError('Failed to load best times.'); }
}

// ===== CHAMPIONSHIP QUALIFICATION SECTION =====
function buildChampSection(resultsByCourse) {
    const champCards = document.getElementById('champCards');
    const champInfo = {
        'CT AG': { title: 'CT Age Group Champs', color: '#dc2626' },
        'EZ': { title: 'Eastern Zone Champs', color: '#9333ea' }
    };
    const courses = ['SCY', 'LCM'];
    let html = '';

    for (const course of courses) {
        const results = resultsByCourse[course];
        if (!results) continue;
        for (const [type, info] of Object.entries(champInfo)) {
            const r = results[type];
            if (!r) continue;
            const total = r.qual.length + r.close.length + r.far.length;
            if (total === 0) continue;

            const statusClass = r.qual.length > 0 ? 'qualified' : 'not-qualified';
            const statusBadge = r.qual.length > 0
                ? `<span class="champ-status yes">Qualified</span>`
                : r.close.length > 0
                    ? `<span class="champ-status partial">Close</span>`
                    : `<span class="champ-status no">Not Yet</span>`;

            html += `<div class="champ-card ${statusClass}">
                <div class="champ-card-header">
                    <span class="champ-card-title">${info.title} <span class="course-tag">${course}</span></span>
                    ${statusBadge}
                </div>
                <div class="champ-qualified-count">${r.qual.length} of ${total} events qualified</div>
                <div class="champ-events">`;
            r.qual.forEach(e => html += `<span class="champ-event-chip qual">${e.event}</span>`);
            r.close.forEach(e => html += `<span class="champ-event-chip close">${e.event} (-${e.gap.toFixed(1)}s)</span>`);
            r.far.slice(0, 5).forEach(e => html += `<span class="champ-event-chip far">${e.event}</span>`);
            html += `</div></div>`;
        }
    }

    champCards.innerHTML = html;
    document.getElementById('champSection').classList.toggle('hidden', html === '');
}

// ===== USA MOTIVATIONAL LADDER - split by course =====
function buildUSALadder(countsByCourse) {
    const levels = [
        { key: 'B', cls: 'level-b' }, { key: 'BB', cls: 'level-bb' },
        { key: 'A', cls: 'level-a' }, { key: 'AA', cls: 'level-aa' },
        { key: 'AAA', cls: 'level-aaa' }, { key: 'AAAA', cls: 'level-aaaa' },
    ];

    let html = '';
    let hasAny = false;
    for (const course of ['SCY', 'LCM']) {
        const counts = countsByCourse[course] || {};
        const totalEvents = Object.values(counts).reduce((a, b) => a + b, 0);
        if (totalEvents === 0) continue;
        hasAny = true;

        html += `<div class="usa-course-block">
            <div class="usa-course-label">${course === 'SCY' ? 'Short Course Yards' : 'Long Course Meters'} <span class="course-tag">${course}</span></div>
            <div class="usa-ladder-summary">`;
        levels.forEach(l => {
            const count = counts[l.key] || 0;
            const hasClass = count > 0 ? ' has-events' : '';
            html += `<div class="ladder-level ${l.cls}${hasClass}">
                <div class="level-name">${l.key}</div>
                <div class="level-count">${count}</div>
                <div class="level-label">events</div>
            </div>`;
        });
        html += `</div></div>`;
    }

    document.getElementById('usaLadder').innerHTML = html;
    document.getElementById('usaSection').classList.toggle('hidden', !hasAny);
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

// ===== EVENT HISTORY (standalone - for direct links) =====
async function loadEventHistory(encodedUrl, eventName) {
    const url = decodeURIComponent(encodedUrl);
    // Instead of navigating away, select this event in the progression dropdown
    const select = document.getElementById('progressEventSelect');
    for (let i = 0; i < select.options.length; i++) {
        if (decodeURIComponent(select.options[i].value) === url) {
            select.selectedIndex = i;
            loadProgressForEvent();
            // Scroll to the chart
            document.getElementById('progressSection').scrollIntoView({ behavior: 'smooth' });
            return;
        }
    }
    // Fallback: load directly if not in dropdown
    await loadProgressFromUrl(url, eventName);
    document.getElementById('progressSection').scrollIntoView({ behavior: 'smooth' });
}

// ===== PROGRESSION CHART ON REPORT CARD =====
let allSwimmerEvents = []; // Store events with history URLs

function buildProgressDropdown(events) {
    const select = document.getElementById('progressEventSelect');
    allSwimmerEvents = events.filter(e => e.history_url);

    if (allSwimmerEvents.length === 0) {
        document.getElementById('progressSection').classList.add('hidden');
        return;
    }

    select.innerHTML = '<option value="">-- Select an event --</option>' +
        allSwimmerEvents.map(e =>
            `<option value="${encodeURIComponent(e.history_url)}">${e.event}</option>`
        ).join('');

    document.getElementById('progressSection').classList.remove('hidden');
    // Clear previous chart/data
    document.getElementById('progressChartContainer').classList.add('hidden');
    document.getElementById('progressStats').innerHTML = '';
    document.getElementById('historyTable').innerHTML = '';
}

document.getElementById('progressEventSelect').addEventListener('change', loadProgressForEvent);

async function loadProgressForEvent() {
    const select = document.getElementById('progressEventSelect');
    const val = select.value;
    if (!val) {
        document.getElementById('progressChartContainer').classList.add('hidden');
        document.getElementById('progressStats').innerHTML = '';
        document.getElementById('historyTable').innerHTML = '';
        return;
    }
    const url = decodeURIComponent(val);
    const eventName = select.options[select.selectedIndex].text;
    await loadProgressFromUrl(url, eventName);
}

async function loadProgressFromUrl(url, eventName) {
    const pLoading = document.getElementById('progressLoading');
    pLoading.classList.remove('hidden');

    try {
        const resp = await fetch('/api/event_history', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ history_url: url })
        });
        const data = await resp.json();
        pLoading.classList.add('hidden');

        if (data.error || !data.history?.length) {
            document.getElementById('progressChartContainer').classList.add('hidden');
            document.getElementById('progressStats').innerHTML = '';
            document.getElementById('historyTable').innerHTML = '<p style="padding:1rem">No history found.</p>';
            return;
        }

        // Build chart
        buildProgressChart(data.history);

        // Build stats cards
        buildProgressStats(data.history);

        // Build history table
        buildHistoryTableInline(data.history);

    } catch (e) {
        pLoading.classList.add('hidden');
        document.getElementById('historyTable').innerHTML = '<p style="padding:1rem;color:#dc2626">Failed to load history.</p>';
    }
}

function buildProgressStats(history) {
    if (history.length < 2) { document.getElementById('progressStats').innerHTML = ''; return; }

    const firstSecs = timeToSeconds(history[history.length - 1].time);
    const bestSecs = timeToSeconds(history[0].time);
    const totalDrop = firstSecs - bestSecs;
    const numSwims = history.length;
    const avgDrop = totalDrop / (numSwims - 1);

    // Find biggest single drop
    let biggestDrop = 0;
    for (let i = 0; i < history.length - 1; i++) {
        const curr = timeToSeconds(history[i].time);
        const prev = timeToSeconds(history[i + 1].time);
        const drop = prev - curr;
        if (drop > biggestDrop) biggestDrop = drop;
    }

    const statsDiv = document.getElementById('progressStats');
    statsDiv.innerHTML = `
        <div class="pstat-card">
            <div class="pstat-value">${numSwims}</div>
            <div class="pstat-label">Total Swims</div>
        </div>
        <div class="pstat-card">
            <div class="pstat-value" style="color:#16a34a">-${totalDrop.toFixed(2)}s</div>
            <div class="pstat-label">Total Improved</div>
        </div>
        <div class="pstat-card">
            <div class="pstat-value">-${avgDrop.toFixed(2)}s</div>
            <div class="pstat-label">Avg per Swim</div>
        </div>
        <div class="pstat-card">
            <div class="pstat-value" style="color:#0055a4">-${biggestDrop.toFixed(2)}s</div>
            <div class="pstat-label">Biggest Drop</div>
        </div>
    `;
}

function buildHistoryTableInline(history) {
    let html = `<table><thead><tr><th>Time</th><th>Swim</th><th>Date</th><th>Improvement</th></tr></thead><tbody>`;
    for (let i = 0; i < history.length; i++) {
        const h = history[i];
        const currSecs = timeToSeconds(h.time);
        let improvement = '<span style="color:#94a3b8">First swim</span>';
        if (i < history.length - 1) {
            const prevSecs = timeToSeconds(history[i + 1].time);
            const diff = prevSecs - currSecs;
            if (diff > 0) improvement = `<span style="color:#16a34a;font-weight:600">-${diff.toFixed(2)}s</span>`;
            else if (diff < 0) improvement = `<span style="color:#dc2626">+${Math.abs(diff).toFixed(2)}s</span>`;
            else improvement = '<span style="color:#94a3b8">0.00s</span>';
        }
        html += `<tr><td class="time-cell">${h.time}</td><td>${h.meet}</td><td>${h.date}</td><td>${improvement}</td></tr>`;
    }
    if (history.length >= 2) {
        const firstSecs = timeToSeconds(history[history.length - 1].time);
        const lastSecs = timeToSeconds(history[0].time);
        const totalDrop = firstSecs - lastSecs;
        html += `<tr style="border-top:2px solid #003366;font-weight:700">
            <td colspan="3" style="text-align:right">Total Improvement:</td>
            <td><span style="color:${totalDrop > 0 ? '#16a34a' : '#dc2626'}">${totalDrop > 0 ? '-' : '+'}${Math.abs(totalDrop).toFixed(2)}s</span></td>
        </tr>`;
    }
    html += '</tbody></table>';
    document.getElementById('historyTable').innerHTML = html;
}

// Back buttons
document.getElementById('backToResults').addEventListener('click', () => {
    reportCard.classList.add('hidden'); searchResults.classList.remove('hidden');
});
document.getElementById('backToTimes').addEventListener('click', () => {
    document.getElementById('eventHistory').classList.add('hidden'); reportCard.classList.remove('hidden');
});
window.loadEventHistory = loadEventHistory;


// ===== PROGRESS CHART =====
let progressChartInstance = null;

function buildProgressChart(history) {
    const canvas = document.getElementById('progressChart');
    const container = document.getElementById('progressChartContainer');
    if (!history || history.length < 2) { container.classList.add('hidden'); return; }
    container.classList.remove('hidden');

    if (progressChartInstance) { progressChartInstance.destroy(); }

    // History comes newest-first; reverse for chronological
    const chronological = [...history].reverse();
    const labels = chronological.map(h => h.date);
    const data = chronological.map(h => timeToSeconds(h.time));

    progressChartInstance = new Chart(canvas, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Time',
                data: data,
                borderColor: '#0055a4',
                backgroundColor: 'rgba(0, 85, 164, 0.1)',
                borderWidth: 2.5,
                pointBackgroundColor: '#0055a4',
                pointRadius: 5,
                pointHoverRadius: 7,
                fill: true,
                tension: 0.3,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function(ctx) { return secondsToTime(ctx.parsed.y); }
                    }
                }
            },
            scales: {
                y: {
                    reverse: true,
                    ticks: {
                        callback: function(val) { return secondsToTime(val); }
                    },
                    title: { display: true, text: 'Time (lower is faster)', font: { size: 11 } }
                },
                x: {
                    ticks: { maxRotation: 45, font: { size: 10 } }
                }
            }
        }
    });
}


// ===== PDF REPORT CARD =====
document.getElementById('downloadPDF').addEventListener('click', generatePDF);

function generatePDF() {
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF();
    const name = document.getElementById('swimmerName').textContent;
    const meta = document.getElementById('swimmerMeta').textContent;

    // Header
    doc.setFillColor(0, 51, 102);
    doc.rect(0, 0, 210, 30, 'F');
    doc.setTextColor(255, 255, 255);
    doc.setFontSize(18);
    doc.setFont('helvetica', 'bold');
    doc.text('SwimProgression Report Card', 105, 13, { align: 'center' });
    doc.setFontSize(10);
    doc.setFont('helvetica', 'normal');
    doc.text(new Date().toLocaleDateString(), 105, 22, { align: 'center' });

    // Swimmer info
    doc.setTextColor(0, 51, 102);
    doc.setFontSize(16);
    doc.setFont('helvetica', 'bold');
    doc.text(name, 15, 42);
    doc.setFontSize(10);
    doc.setFont('helvetica', 'normal');
    doc.setTextColor(100, 116, 139);
    doc.text(meta, 15, 49);

    // Best times table
    const table = document.querySelector('#bestTimesTable table');
    if (!table) { doc.save(`${name}_Report.pdf`); return; }

    const rows = table.querySelectorAll('tbody tr');
    let y = 58;

    // Table header
    doc.setFillColor(0, 51, 102);
    doc.rect(15, y - 4, 180, 8, 'F');
    doc.setTextColor(255, 255, 255);
    doc.setFontSize(8);
    doc.setFont('helvetica', 'bold');
    doc.text('Event', 17, y + 1);
    doc.text('Best Time', 70, y + 1);
    doc.text('Date', 100, y + 1);
    doc.text('Level', 130, y + 1);
    doc.text('Next Target', 155, y + 1);
    y += 8;

    doc.setFont('helvetica', 'normal');
    doc.setFontSize(7.5);

    rows.forEach(row => {
        if (y > 275) { doc.addPage(); y = 20; }

        const cells = row.querySelectorAll('td');
        if (row.classList.contains('course-separator')) {
            doc.setFillColor(0, 51, 102);
            doc.rect(15, y - 4, 180, 7, 'F');
            doc.setTextColor(255, 255, 255);
            doc.setFont('helvetica', 'bold');
            doc.text(cells[0]?.textContent || '', 17, y);
            doc.setFont('helvetica', 'normal');
            y += 7;
            return;
        }
        if (cells.length < 3) return;

        // Alternate row bg
        const rowIdx = Array.from(rows).indexOf(row);
        if (rowIdx % 2 === 0) { doc.setFillColor(247, 250, 252); doc.rect(15, y - 4, 180, 6, 'F'); }

        doc.setTextColor(50, 50, 50);
        doc.text(cells[0]?.textContent?.trim() || '', 17, y);
        doc.text(cells[1]?.textContent?.trim() || '', 70, y);
        doc.text(cells[2]?.textContent?.trim() || '', 100, y);

        // Level badges (text extraction)
        const badges = cells[3]?.querySelectorAll('.badge');
        if (badges && badges.length > 0) {
            let bx = 130;
            badges.forEach(b => {
                const txt = b.textContent.trim();
                doc.setTextColor(0, 100, 0);
                doc.text(txt, bx, y);
                bx += doc.getTextWidth(txt) + 3;
            });
        }
        doc.setTextColor(50, 50, 50);

        // Next target
        const ntBadge = cells[4]?.querySelector('.badge');
        if (ntBadge) {
            doc.text(ntBadge.textContent.trim(), 155, y);
            const ntTime = cells[4]?.querySelector('.nt-time');
            if (ntTime) doc.text(ntTime.textContent.trim(), 168, y);
        }

        y += 6;
    });

    // Footer
    doc.setFontSize(7);
    doc.setTextColor(150, 150, 150);
    doc.text('Generated by SwimProgression.com', 105, 290, { align: 'center' });

    doc.save(`${name.replace(/[^a-zA-Z0-9]/g, '_')}_Report.pdf`);
}


// ===== SCY <-> LCM CONVERTER =====
function getTimeFromInputs(minId, secId, hunId) {
    const m = parseInt(document.getElementById(minId).value) || 0;
    const s = parseInt(document.getElementById(secId).value) || 0;
    const h = parseInt(document.getElementById(hunId).value) || 0;
    if (m === 0 && s === 0 && h === 0) return null;
    return m * 60 + s + h / 100;
}

document.getElementById('convertBtn').addEventListener('click', () => {
    const event = document.getElementById('convEvent').value;
    const direction = document.getElementById('convDirection').value;
    const timeSecs = getTimeFromInputs('convMin', 'convSec', 'convHun');

    if (!timeSecs) { return; }

    const factors = CONVERSION_FACTORS[event];
    if (!factors) { return; }

    const factor = factors[direction];
    const converted = timeSecs * factor;
    const fromCourse = direction === 'scy2lcm' ? 'SCY' : 'LCM';
    const toCourse = direction === 'scy2lcm' ? 'LCM' : 'SCY';

    const resultDiv = document.getElementById('convResult');
    resultDiv.classList.remove('hidden');
    resultDiv.innerHTML = `
        <div class="result-label">${event} | ${fromCourse} → ${toCourse}</div>
        <div class="result-time">${secondsToTime(converted)}</div>
        <div class="result-detail">Original: ${secondsToTime(timeSecs)} ${fromCourse} | Factor: ${factor.toFixed(4)}</div>
    `;

    // Check what standards the converted time meets
    const stdDiv = document.getElementById('convStandards');
    const gender = document.getElementById('gender').value;
    const ageGrp = getCurrentAgeGroup();

    if (ageGrp && gender) {
        // Build a fake eventInfo for the target course
        const strokeMap = { 'Free': 'FREE', 'Back': 'BACK', 'Breast': 'BREAST', 'Fly': 'FLY', 'IM': 'IM' };
        const parts = event.split(' ');
        const dist = parts[0].split('/')[0];
        const stroke = strokeMap[parts[parts.length - 1]] || parts[parts.length - 1].toUpperCase();
        const usaStrokeMap = { 'FREE': 'FR', 'BACK': 'BK', 'BREAST': 'BR', 'FLY': 'FL', 'IM': 'IM' };
        const eventInfo = {
            distance: dist, course: toCourse, stroke: stroke,
            standardName: `${dist} ${stroke}`, usaName: `${dist} ${usaStrokeMap[stroke] || stroke}`
        };

        const standards = lookupStandards(eventInfo, ageGrp, gender);
        const comparison = compareToStandards(converted, standards);

        let stdHtml = `<h4>Standards met at ${secondsToTime(converted)} (${toCourse}) — ${ageGrp} ${gender === 'F' ? 'Girls' : 'Boys'}</h4>`;
        stdHtml += '<div class="achieved-badges" style="justify-content:flex-start;gap:0.4rem;margin:0.5rem 0">';

        if (comparison.highestUSA) {
            comparison.usaAchieved.forEach(a => {
                stdHtml += `<span class="badge ${a.cssClass}">${a.type}</span>`;
            });
        }
        comparison.champAchieved.forEach(a => {
            stdHtml += `<span class="badge ${a.cssClass}">${a.type}</span>`;
        });
        if (!comparison.highestUSA && comparison.champAchieved.length === 0) {
            stdHtml += '<span style="color:#94a3b8;font-size:0.85rem">No standards met at this time</span>';
        }
        stdHtml += '</div>';

        if (comparison.usaNext) {
            const gap = converted - timeToSeconds(comparison.usaNext.time);
            stdHtml += `<div class="std-note">Next: <span class="badge ${comparison.usaNext.cssClass}">${comparison.usaNext.type}</span> ${comparison.usaNext.time} (need -${gap.toFixed(2)}s)</div>`;
        }

        stdDiv.innerHTML = stdHtml;
        stdDiv.classList.remove('hidden');
    } else {
        stdDiv.innerHTML = '<div class="std-note">Enter DOB & Gender in Swimmer Search tab to see standards for converted time</div>';
        stdDiv.classList.remove('hidden');
    }
});


// ===== WHAT IF GOAL PLANNER =====
const wiAgeGroup = document.getElementById('wiAgeGroup');
const wiEvent = document.getElementById('wiEvent');
const wiGender = document.getElementById('wiGender');
const wiCourse = document.getElementById('wiCourse');

function updateWiEvents() {
    const ag = wiAgeGroup.value;
    const events = WHATIF_EVENTS[ag] || [];
    wiEvent.innerHTML = events.map(e => `<option value="${e}">${e}</option>`).join('');
}

wiAgeGroup.addEventListener('change', updateWiEvents);
updateWiEvents();

document.getElementById('whatifBtn').addEventListener('click', () => {
    const timeSecs = getTimeFromInputs('wiMin', 'wiSec', 'wiHun');
    if (!timeSecs) return;

    const ag = wiAgeGroup.value;
    const gender = wiGender.value;
    const event = wiEvent.value;
    const course = wiCourse.value;

    // Build eventInfo
    const strokeMap = { 'Free': 'FREE', 'Back': 'BACK', 'Breast': 'BREAST', 'Fly': 'FLY', 'IM': 'IM' };
    const parts = event.split(' ');
    const dist = parts[0];
    const stroke = strokeMap[parts[parts.length - 1]] || parts[parts.length - 1].toUpperCase();
    const usaStrokeMap = { 'FREE': 'FR', 'BACK': 'BK', 'BREAST': 'BR', 'FLY': 'FL', 'IM': 'IM' };
    const eventInfo = {
        distance: dist, course: course, stroke: stroke,
        standardName: `${dist} ${stroke}`, usaName: `${dist} ${usaStrokeMap[stroke] || stroke}`
    };

    const standards = lookupStandards(eventInfo, ag, gender);
    const comparison = compareToStandards(timeSecs, standards);

    const resultDiv = document.getElementById('wiResult');
    let html = `<div class="wi-summary">
        <div class="wi-event">${event} | ${course} | ${ag} ${gender === 'F' ? 'Girls' : 'Boys'}</div>
        <div class="wi-time">${secondsToTime(timeSecs)}</div>
    </div>`;

    html += '<div class="wi-standards-list">';

    // USA standards (ordered B -> AAAA)
    standards.usa.forEach(std => {
        const stdSecs = timeToSeconds(std.time);
        const achieved = timeSecs <= stdSecs;
        const gap = timeSecs - stdSecs;
        const cls = achieved ? 'achieved' : 'missed';
        const gapText = achieved
            ? `<span class="wi-std-gap pass">Made it by ${Math.abs(gap).toFixed(2)}s</span>`
            : `<span class="wi-std-gap fail">Need -${gap.toFixed(2)}s</span>`;

        html += `<div class="wi-std-row ${cls}">
            <div class="wi-std-left">
                <span class="badge ${std.cssClass}">${std.type}</span>
                <span class="wi-std-cut">${std.time}</span>
            </div>
            ${gapText}
        </div>`;
    });

    // Championship standards
    standards.champ.forEach(std => {
        const stdSecs = timeToSeconds(std.time);
        const achieved = timeSecs <= stdSecs;
        const gap = timeSecs - stdSecs;
        const cls = achieved ? 'achieved' : 'missed';
        const gapText = achieved
            ? `<span class="wi-std-gap pass">Qualified by ${Math.abs(gap).toFixed(2)}s</span>`
            : `<span class="wi-std-gap fail">Need -${gap.toFixed(2)}s</span>`;

        html += `<div class="wi-std-row ${cls}">
            <div class="wi-std-left">
                <span class="badge ${std.cssClass}">${std.type}</span>
                <span class="wi-std-cut">${std.time}</span>
            </div>
            ${gapText}
        </div>`;
    });

    if (standards.usa.length === 0 && standards.champ.length === 0) {
        html += '<div style="text-align:center;color:#94a3b8;padding:1rem">No standards found for this event/age group/course combination</div>';
    }

    html += '</div>';
    resultDiv.innerHTML = html;
    resultDiv.classList.remove('hidden');
});
