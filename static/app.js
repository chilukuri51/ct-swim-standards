// ===== ROLE-BASED UI GATING =====
const PERMS = new Set(window.APP_PERMISSIONS || []);
function hasPerm(p) { return PERMS.has(p); }

// Apply disabled state to elements with data-requires
document.querySelectorAll('[data-requires]').forEach(el => {
    const need = el.dataset.requires;
    if (!hasPerm(need)) {
        el.disabled = true;
        el.classList.add('perm-disabled');
        el.title = 'Not available for your role';
    }
});

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

function getProgram() {
    return window.STANDARDS_DATA?.programs?.[standardType.value];
}

function populateProgramDropdown() {
    const programs = window.STANDARDS_DATA?.programs || {};
    const prev = standardType.value;
    standardType.innerHTML = Object.entries(programs)
        .map(([id, p]) => {
            const sub = p.subtitle ? ` (${p.subtitle})` : '';
            return `<option value="${id}">${p.display_name || id}${sub}</option>`;
        })
        .join('');
    if (prev && programs[prev]) standardType.value = prev;
}

function getAgeGroupsList() {
    const p = getProgram();
    if (!p) return [];
    const groupKeys = Object.keys(p.groups || {});
    if (p.multi_level) {
        // USA-style keys are "10 & under Girls SCY" — strip the gender + course
        const ages = new Set();
        groupKeys.forEach(g => {
            const m = g.match(/^(.+?)\s+(?:Girls|Boys|Women|Men)\s+(?:SCY|LCM|SCM)$/);
            ages.add(m ? m[1] : g);
        });
        return [...ages];
    }
    return groupKeys;
}

function getCoursesList() {
    const p = getProgram();
    if (!p) return ['SCY'];
    if (p.multi_level) {
        const age = ageGroupSel.value;
        const courses = new Set();
        Object.keys(p.groups || {}).forEach(g => {
            if (g.startsWith(age)) {
                const m = g.match(/(SCY|LCM|SCM)$/);
                if (m) courses.add(m[1]);
            }
        });
        return courses.size > 0 ? [...courses] : ['SCY'];
    }
    // For non-multi-level: enumerate courses present under the first gender_key
    const firstGroup = Object.values(p.groups || {})[0] || {};
    const gk = (p.gender_keys || ['girls'])[0];
    const courses = gk && firstGroup[gk] ? Object.keys(firstGroup[gk]) : ['SCY', 'LCM'];
    return courses.length ? courses : ['SCY', 'LCM'];
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

const GENDER_LABELS = { girls: 'Girls', boys: 'Boys', women: 'Women', men: 'Men' };

function renderStandards() {
    const p = getProgram();
    const container = document.getElementById('standardsTable');
    if (!p) { container.innerHTML = '<p style="padding:1rem">No standards loaded</p>'; return; }
    const age = ageGroupSel.value;
    const course = courseType.value;
    if (p.multi_level) renderMultiLevelTable(p, age, course, container);
    else renderGenderedTable(p, age, course, container);
}

function renderGenderedTable(p, age, course, container) {
    const data = p.groups?.[age];
    if (!data) { container.innerHTML = '<p style="padding:1rem">No data available</p>'; return; }
    const gks = p.gender_keys || ['girls', 'boys'];
    let html = `<table><thead><tr><th>Event</th>`;
    gks.forEach(gk => {
        html += `<th>${GENDER_LABELS[gk] || gk} ${course}</th>`;
    });
    html += `</tr></thead><tbody>`;
    (data.events || []).forEach((event, i) => {
        html += `<tr><td class="event-name">${event}</td>`;
        gks.forEach(gk => {
            const t = data[gk]?.[course]?.[i];
            html += `<td>${t || 'N/A'}</td>`;
        });
        html += '</tr>';
    });
    html += '</tbody></table>';
    container.innerHTML = html;
}

function renderMultiLevelTable(p, age, course, container) {
    let html = '';
    ['Girls', 'Boys'].forEach(g => {
        const key = `${age} ${g} ${course}`;
        const data = p.groups?.[key];
        if (!data) return;
        html += `<h3 style="padding:0.75rem 1rem;margin:0;color:#003366;${g === 'Boys' ? 'margin-top:1rem' : ''}">${age} ${g} - ${course}</h3>`;
        html += `<table><thead><tr><th>Event</th>`;
        (data.levels || []).forEach(l => { html += `<th>${l}</th>`; });
        html += `</tr></thead><tbody>`;
        (data.events || []).forEach((event, i) => {
            html += `<tr><td class="event-name">${event}</td>`;
            (data.times?.[i] || []).forEach(t => { html += `<td>${t}</td>`; });
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
populateProgramDropdown();
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
let currentSwimmerId = null;

async function loadBestTimes(swimmerId, name) {
    currentSwimmerId = swimmerId;
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
            const evLabel = count === 1 ? 'event' : 'events';
            html += `<div class="ladder-level ${l.cls}${hasClass}">
                <div class="level-name">${l.key}</div>
                <div class="level-count">${count}</div>
                <div class="level-label">${evLabel}</div>
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
            body: JSON.stringify({ history_url: url, swimmer_id: currentSwimmerId, event_name: eventName })
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
    doc.text('Best Time', 65, y + 1);
    doc.text('Date', 92, y + 1);
    doc.text('Level', 117, y + 1);
    doc.text('Next Target', 140, y + 1);
    doc.text('To Drop', 178, y + 1);
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
        doc.text(cells[1]?.textContent?.trim() || '', 65, y);
        doc.text(cells[2]?.textContent?.trim() || '', 92, y);

        // Level badges (text extraction)
        const badges = cells[3]?.querySelectorAll('.badge');
        if (badges && badges.length > 0) {
            let bx = 117;
            badges.forEach(b => {
                const txt = b.textContent.trim();
                doc.setTextColor(0, 100, 0);
                doc.text(txt, bx, y);
                bx += doc.getTextWidth(txt) + 3;
            });
        }
        doc.setTextColor(50, 50, 50);

        // Next target + seconds to drop (with color coding)
        const ntBadge = cells[4]?.querySelector('.badge');
        if (ntBadge) {
            doc.text(ntBadge.textContent.trim(), 140, y);
            const ntTime = cells[4]?.querySelector('.nt-time');
            if (ntTime) doc.text(ntTime.textContent.trim(), 153, y);
        }
        // Gap text from cells[5] (e.g. "-1.23s"). Color by magnitude.
        const gapTxt = cells[5]?.textContent?.trim() || '';
        const gapMatch = gapTxt.match(/-?(\d+(?:\.\d+)?)/);
        if (gapMatch) {
            const gapVal = parseFloat(gapMatch[1]);
            if (gapVal <= 1.0) doc.setTextColor(22, 163, 74);       // green
            else if (gapVal <= 3.0) doc.setTextColor(234, 88, 12);  // orange
            else doc.setTextColor(220, 38, 38);                     // red
            doc.setFont('helvetica', 'bold');
            doc.text(`${gapVal.toFixed(2)}s`, 178, y);
            doc.setFont('helvetica', 'normal');
            doc.setTextColor(50, 50, 50);
        } else if (gapTxt.toLowerCase().includes('done')) {
            doc.setTextColor(22, 163, 74);
            doc.setFont('helvetica', 'bold');
            doc.text('Done', 178, y);
            doc.setFont('helvetica', 'normal');
            doc.setTextColor(50, 50, 50);
        }

        y += 6;
    });

    // Footer
    doc.setFontSize(7);
    doc.setTextColor(150, 150, 150);
    doc.text('Generated by SwimProgression.com', 105, 290, { align: 'center' });

    doc.save(`${name.replace(/[^a-zA-Z0-9]/g, '_')}_Report.pdf`);
}


// Profile-modal version: generates the same report shape from cached
// member data (best_times + standards lookups) instead of the search-view
// DOM. Adds Coach Notes section. Reuses compareToStandards + lookupStandards
// + normalizeEvent which are defined globally elsewhere in this file.
function generatePDFForMember(member) {
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF();
    const fullName = `${member.first_name || ''} ${member.last_name || ''}`.trim()
                     || member.name || 'Swimmer';

    function ageGroupOf(age) {
        if (age == null) return null;
        if (age <= 10) return '10/Under';
        if (age <= 12) return '11/12';
        if (age <= 14) return '13/14';
        if (age <= 16) return '15/16';
        return '17/18';
    }

    const age = member.age;
    const gender = member.gender;
    const ageGrp = ageGroupOf(age);
    const hasProfile = !!(ageGrp && gender);

    // Header band
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
    doc.text(fullName, 15, 42);
    const metaParts = [];
    if (age != null) metaParts.push(`Age: ${age}`);
    if (ageGrp) metaParts.push(`Age Group: ${ageGrp}`);
    if (gender === 'F') metaParts.push('Female');
    else if (gender === 'M') metaParts.push('Male');
    if (member.roster) metaParts.push(member.roster);
    doc.setFontSize(10);
    doc.setFont('helvetica', 'normal');
    doc.setTextColor(100, 116, 139);
    doc.text(metaParts.join(' | '), 15, 49);

    // Best times table
    let y = 58;
    doc.setFillColor(0, 51, 102);
    doc.rect(15, y - 4, 180, 8, 'F');
    doc.setTextColor(255, 255, 255);
    doc.setFontSize(8);
    doc.setFont('helvetica', 'bold');
    doc.text('Event', 17, y + 1);
    doc.text('Best Time', 65, y + 1);
    doc.text('Date', 92, y + 1);
    doc.text('Level', 117, y + 1);
    doc.text('Next Target', 140, y + 1);
    doc.text('To Drop', 178, y + 1);
    y += 8;

    doc.setFont('helvetica', 'normal');
    doc.setFontSize(7.5);

    // Group by course
    const events = (member.best_times || []).map(ev => ({
        ev,
        info: normalizeEvent(ev.event),
    })).filter(x => x.info);
    const scy = events.filter(x => x.info.course === 'SCY');
    const lcm = events.filter(x => x.info.course === 'LCM');

    function renderCourse(label, group) {
        if (!group.length) return;
        if (y > 270) { doc.addPage(); y = 20; }
        // Course separator
        doc.setFillColor(0, 51, 102);
        doc.rect(15, y - 4, 180, 7, 'F');
        doc.setTextColor(255, 255, 255);
        doc.setFont('helvetica', 'bold');
        doc.text(label, 17, y);
        doc.setFont('helvetica', 'normal');
        y += 7;
        group.forEach((x, idx) => {
            if (y > 275) { doc.addPage(); y = 20; }
            const ev = x.ev, info = x.info;
            // Alt row bg
            if (idx % 2 === 0) { doc.setFillColor(247, 250, 252); doc.rect(15, y - 4, 180, 6, 'F'); }
            doc.setTextColor(50, 50, 50);
            doc.text(ev.event || '', 17, y);
            doc.text(ev.time || '', 65, y);
            doc.text(ev.date || '', 92, y);
            // Level + next target + seconds-to-drop via standards lookup
            if (hasProfile) {
                const std = lookupStandards(info, ageGrp, gender);
                const swSecs = timeToSeconds(ev.time);
                const cmp = compareToStandards(swSecs, std);
                if (cmp.highestUSA) {
                    doc.setTextColor(0, 100, 0);
                    doc.setFont('helvetica', 'bold');
                    doc.text(cmp.highestUSA.type, 117, y);
                    doc.setFont('helvetica', 'normal');
                    doc.setTextColor(50, 50, 50);
                }
                const next = cmp.usaNext || cmp.champNext;
                if (next) {
                    doc.text(next.type, 140, y);
                    doc.text(next.time, 153, y);
                    const gap = swSecs - timeToSeconds(next.time);
                    if (isFinite(gap) && gap > 0) {
                        if (gap <= 1.0) doc.setTextColor(22, 163, 74);
                        else if (gap <= 3.0) doc.setTextColor(234, 88, 12);
                        else doc.setTextColor(220, 38, 38);
                        doc.setFont('helvetica', 'bold');
                        doc.text(`${gap.toFixed(2)}s`, 178, y);
                        doc.setFont('helvetica', 'normal');
                        doc.setTextColor(50, 50, 50);
                    }
                } else if (cmp.highestUSA) {
                    // All cuts achieved for this event
                    doc.setTextColor(22, 163, 74);
                    doc.setFont('helvetica', 'bold');
                    doc.text('Done', 178, y);
                    doc.setFont('helvetica', 'normal');
                    doc.setTextColor(50, 50, 50);
                }
            }
            y += 6;
        });
    }
    renderCourse('Short Course Yards', scy);
    renderCourse('Long Course Meters', lcm);

    // Coach notes
    const notes = (member.notes || '').trim();
    if (notes) {
        if (y > 240) { doc.addPage(); y = 20; }
        y += 6;
        doc.setFillColor(0, 51, 102);
        doc.rect(15, y - 4, 180, 7, 'F');
        doc.setTextColor(255, 255, 255);
        doc.setFont('helvetica', 'bold');
        doc.text('Coach Notes', 17, y);
        y += 9;
        doc.setTextColor(50, 50, 50);
        doc.setFont('helvetica', 'normal');
        doc.setFontSize(9);
        // Wrap long lines to 175mm width
        const wrapped = doc.splitTextToSize(notes, 175);
        wrapped.forEach(line => {
            if (y > 280) { doc.addPage(); y = 20; }
            doc.text(line, 17, y);
            y += 5;
        });
    }

    // Footer on all pages
    const pages = doc.internal.getNumberOfPages();
    for (let p = 1; p <= pages; p++) {
        doc.setPage(p);
        doc.setFontSize(7);
        doc.setTextColor(150, 150, 150);
        doc.text('Generated by SwimProgression — for coaching use', 105, 290, { align: 'center' });
    }

    doc.save(`${fullName.replace(/[^a-zA-Z0-9]/g, '_')}_Report.pdf`);
}


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


// ===== REFRESH SWIMMER (admin only) =====
const refreshBtn = document.getElementById('refreshSwimmer');
if (refreshBtn) {
    refreshBtn.addEventListener('click', async () => {
        if (!currentSwimmerId) { showError('No swimmer selected'); return; }
        const original = refreshBtn.textContent;
        refreshBtn.disabled = true;
        refreshBtn.textContent = 'Fetching...';

        try {
            const resp = await fetch('/api/refresh_swimmer', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ swimmer_id: currentSwimmerId })
            });
            const data = await resp.json();
            if (data.error) {
                showError('Refresh failed: ' + data.error);
            } else {
                // Reload the swimmer with fresh cached data
                await loadBestTimes(currentSwimmerId, '');
            }
        } catch (e) {
            showError('Refresh failed: ' + e.message);
        } finally {
            refreshBtn.disabled = false;
            refreshBtn.textContent = original;
        }
    });
}


// ===== ADMIN TAB (batch fetch) =====
if (hasPerm('batch')) {
    const modeButtons = document.querySelectorAll('.batch-mode-btn');
    const panelLastNames = document.getElementById('batchLastNames');
    const panelTeamCode = document.getElementById('batchTeamCodePanel');
    let currentMode = 'team_roster';
    const panelTeamRoster = document.getElementById('batchTeamRoster');
    const rosterBatchPreview = document.getElementById('rosterBatchPreview');

    modeButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            modeButtons.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentMode = btn.dataset.mode;
            panelLastNames.classList.toggle('hidden', currentMode !== 'last_names');
            panelTeamCode.classList.toggle('hidden', currentMode !== 'team_code');
            panelTeamRoster.classList.toggle('hidden', currentMode !== 'team_roster');
            if (currentMode === 'team_roster') refreshRosterBatchPreview();
        });
    });

    document.getElementById('goToRosterLink').addEventListener('click', e => {
        e.preventDefault();
        document.querySelector('[data-tab="roster"]').click();
    });

    async function refreshRosterBatchPreview() {
        try {
            const [tmR, swR] = await Promise.all([
                fetch('/api/team_members'),
                fetch('/api/my_swimmers'),
            ]);
            const tm = await tmR.json();
            const sw = await swR.json();
            const linked = tm.members.filter(m => m.ct_id).length;
            const unlinked = tm.count - linked;
            // "Newly added" = linked but no cached times yet OR not linked yet at all
            const cachedIds = new Set((sw.members || [])
                .filter(m => (m.best_times || []).length > 0).map(m => m.ct_id));
            const newOnes = tm.members.filter(m =>
                !m.ct_id || (m.ct_id && !cachedIds.has(m.ct_id))
            ).length;
            rosterBatchPreview.innerHTML = `
                <span class="pdf-stat-pill total">${tm.count} swimmers in roster</span>
                <span class="pdf-stat-pill included">${linked} matched</span>
                <span class="pdf-stat-pill excluded">${unlinked} need matching</span>
                <span class="pdf-stat-pill" style="background:#fef3c7;color:#92400e">${newOnes} newly added (no times yet)</span>
            `;
        } catch (e) { /* ignore */ }
    }
    refreshRosterBatchPreview();

    const startBtn = document.getElementById('batchStartBtn');
    const cancelBtn = document.getElementById('batchCancelBtn');
    const progressBox = document.getElementById('batchProgress');
    let pollTimer = null;

    startBtn.addEventListener('click', async () => {
        let body = { mode: currentMode };
        if (currentMode === 'last_names') {
            body.last_names = document.getElementById('batchNames').value;
            body.team_filter = document.getElementById('batchTeamFilter').value.trim();
            if (!body.last_names.trim()) { alert('Enter at least one last name'); return; }
        } else if (currentMode === 'team_code') {
            body.team_code = document.getElementById('batchTeamCodeInput').value.trim();
            if (!body.team_code) { alert('Enter a team code'); return; }
        } else if (currentMode === 'team_roster') {
            const onlyNew = document.getElementById('batchOnlyNew');
            body.only_new = !!(onlyNew && onlyNew.checked);
        }

        startBtn.disabled = true;
        startBtn.textContent = 'Starting...';
        try {
            const r = await fetch('/api/batch/start', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const d = await r.json();
            if (d.error) { alert(d.error); startBtn.disabled = false; startBtn.textContent = 'Start Fetch'; return; }
            progressBox.classList.remove('hidden');
            cancelBtn.classList.remove('hidden');
            startPolling();
        } catch (e) {
            alert('Failed to start: ' + e.message);
            startBtn.disabled = false;
            startBtn.textContent = 'Start Fetch';
        }
    });

    cancelBtn.addEventListener('click', async () => {
        cancelBtn.disabled = true;
        cancelBtn.textContent = 'Cancelling...';
        await fetch('/api/batch/cancel', { method: 'POST' });
    });

    function startPolling() {
        if (pollTimer) clearInterval(pollTimer);
        pollStatus();
        pollTimer = setInterval(pollStatus, 2000);
    }

    async function pollStatus() {
        try {
            const r = await fetch('/api/batch/status');
            const s = await r.json();
            updateBatchUI(s);
            if (!s.running) {
                clearInterval(pollTimer);
                pollTimer = null;
                startBtn.disabled = false;
                startBtn.textContent = 'Start Fetch';
                cancelBtn.classList.add('hidden');
                cancelBtn.disabled = false;
                cancelBtn.textContent = 'Cancel';
                refreshAdminStats();
            }
        } catch (e) { /* ignore */ }
    }

    function updateBatchUI(s) {
        const pill = document.getElementById('batchStatusPill');
        if (s.running) { pill.textContent = 'Running'; pill.className = 'batch-status-pill running'; }
        else if (s.cancelled) { pill.textContent = 'Cancelled'; pill.className = 'batch-status-pill cancelled'; }
        else if (s.finished_at) { pill.textContent = 'Complete'; pill.className = 'batch-status-pill done'; }

        document.getElementById('batchBarFill').style.width = s.progress_pct + '%';

        const etaMin = Math.ceil(s.eta_seconds / 60);
        document.getElementById('batchEta').textContent = s.running
            ? `ETA: ${etaMin} min (${s.eta_seconds}s)`
            : '';

        document.getElementById('batchSearchProgress').textContent =
            `${s.search_done} / ${s.search_total}` + (s.current_search ? ` - ${s.current_search}` : '');
        document.getElementById('batchFetchProgress').textContent =
            `${s.fetch_done} / ${s.fetch_total}` + (s.current_fetch ? ` - ${s.current_fetch}` : '');
        document.getElementById('batchCurrent').textContent = s.current_fetch || s.current_search || '-';
        document.getElementById('batchCached').textContent = s.swimmers_cached;

        const errDiv = document.getElementById('batchErrors');
        if (s.errors && s.errors.length > 0) {
            errDiv.classList.remove('hidden');
            errDiv.innerHTML = '<strong>Errors:</strong><br>' + s.errors.map(e => '• ' + e).join('<br>');
        } else {
            errDiv.classList.add('hidden');
        }

        // Sync log
        const tbody = document.getElementById('syncLogBody');
        if (s.recent_log && s.recent_log.length > 0) {
            tbody.innerHTML = s.recent_log.map(row => {
                const statusClass = row.status === 'ok' ? 'log-status-ok' : 'log-status-error';
                const time = row.created_at ? row.created_at.replace('T', ' ').substring(5, 19) : '';
                return `<tr>
                    <td style="white-space:nowrap">${time}</td>
                    <td>${row.action}</td>
                    <td class="${statusClass}">${row.status}</td>
                    <td style="white-space:normal">${row.message || ''}</td>
                </tr>`;
            }).join('');
        }
    }

    async function refreshAdminStats() {
        try {
            const r = await fetch('/api/cache_stats');
            const s = await r.json();
            document.getElementById('statTotal').textContent = s.total_swimmers;
            document.getElementById('statCached').textContent = s.cached_swimmers;
        } catch (e) { /* ignore */ }
    }

    // Initial load
    refreshAdminStats();
    // Poll once to pick up a job already running
    (async function checkExisting() {
        try {
            const r = await fetch('/api/batch/status');
            const s = await r.json();
            if (s.running) {
                progressBox.classList.remove('hidden');
                cancelBtn.classList.remove('hidden');
                startBtn.disabled = true;
                startBtn.textContent = 'Running...';
                startPolling();
            } else {
                // Populate log even when idle
                updateBatchUI(s);
            }
        } catch (e) { /* ignore */ }
    })();
}


// ===== ROSTER TAB =====
if (hasPerm('roster')) {
    const canEdit = hasPerm('roster_edit');
    let rosterMembers = [];
    let editingMemberId = null;

    const tableContainer = document.getElementById('rosterTableContainer');
    const groupFilter = document.getElementById('rosterGroupFilter');
    const genderFilter = document.getElementById('rosterGenderFilter');
    const searchFilter = document.getElementById('rosterSearchFilter');
    const statsEl = document.getElementById('rosterStats');
    const groupsDataList = document.getElementById('rosterGroupsDataList');

    const modal = document.getElementById('rosterModal');
    const modalTitle = document.getElementById('rosterModalTitle');
    const errorBox = document.getElementById('rmError');
    const fFirst = document.getElementById('rmFirstName');
    const fLast = document.getElementById('rmLastName');
    const fRoster = document.getElementById('rmRoster');
    const fGender = document.getElementById('rmGender');
    const fBirthMonth = document.getElementById('rmBirthMonth');
    const fParentEmail = document.getElementById('rmParentEmail');
    const fNotes = document.getElementById('rmNotes');
    const fDobHint = document.getElementById('rmDobHint');
    const deleteBtn = document.getElementById('rosterDeleteBtn');

    document.getElementById('rosterAddBtn').addEventListener('click', openAddModal);
    document.getElementById('rosterModalClose').addEventListener('click', closeModal);
    document.getElementById('rosterCancelBtn').addEventListener('click', closeModal);
    document.getElementById('rosterSaveBtn').addEventListener('click', saveMember);
    deleteBtn.addEventListener('click', deleteMember);

    [groupFilter, genderFilter].forEach(el => el.addEventListener('change', renderRoster));
    searchFilter.addEventListener('input', renderRoster);

    // Hide add/edit if user lacks roster_edit (in case we add a view-only role later)
    if (!canEdit) {
        document.getElementById('rosterAddBtn').classList.add('hidden');
    }

    async function loadMembers() {
        try {
            const r = await fetch('/api/my_swimmers');
            const d = await r.json();
            rosterMembers = d.members || [];
            updateGroupOptions();
            renderRoster();
        } catch (e) {
            tableContainer.innerHTML = '<p style="padding:1rem;color:#dc2626">Failed to load roster.</p>';
        }
    }

    function updateGroupOptions() {
        const groups = [...new Set(rosterMembers.map(m => m.roster).filter(Boolean))].sort();
        groupFilter.innerHTML = '<option value="">All Groups</option>' +
            groups.map(g => `<option value="${g}">${g}</option>`).join('');
        groupsDataList.innerHTML = groups.map(g => `<option value="${g}">`).join('');
    }

    function renderRoster() {
        const groupVal = groupFilter.value;
        const genderVal = genderFilter.value;
        const search = searchFilter.value.trim().toLowerCase();
        const filtered = rosterMembers.filter(m => {
            if (groupVal && m.roster !== groupVal) return false;
            if (genderVal && m.gender !== genderVal) return false;
            if (search) {
                const fullName = `${m.first_name} ${m.last_name}`.toLowerCase();
                if (!fullName.includes(search)) return false;
            }
            return true;
        });

        // Update stats
        const total = rosterMembers.length;
        const linked = rosterMembers.filter(m => m.ct_id).length;
        statsEl.textContent = `${filtered.length} shown of ${total} total | ${linked} matched to CT Swim | ${total - linked} unmatched`;

        if (filtered.length === 0) {
            tableContainer.innerHTML = '<p style="padding:1rem;color:#94a3b8">No swimmers match the current filters.</p>';
            return;
        }

        // Group by roster
        const byGroup = {};
        filtered.forEach(m => {
            const g = m.roster || '(no group)';
            (byGroup[g] = byGroup[g] || []).push(m);
        });
        const orderedGroups = Object.keys(byGroup).sort();

        let html = '<table><thead><tr>'
            + '<th style="text-align:left">Name</th>'
            + '<th>Group</th><th>Gender</th><th>Age</th><th>CT Swim</th>'
            + (canEdit ? '<th>Actions</th>' : '')
            + '</tr></thead><tbody>';

        orderedGroups.forEach(g => {
            html += `<tr class="group-header"><td colspan="${canEdit ? 6 : 5}">${g} (${byGroup[g].length})</td></tr>`;
            byGroup[g].forEach(m => {
                const gender = m.gender ? (m.gender === 'F' ? 'F' : 'M') : '<span style="color:#cbd5e0">—</span>';
                const age = (m.age != null) ? m.age : '<span style="color:#cbd5e0">—</span>';
                const link = m.ct_id
                    ? `<span class="roster-link-status linked">${m.ct_team || 'matched'}</span>`
                    : '<span class="roster-link-status unlinked">not matched</span>';
                const actions = canEdit
                    ? `<td><div class="roster-actions">
                        <button class="roster-action-btn edit" data-id="${m.id}">Edit</button>
                       </div></td>`
                    : '';
                html += `<tr>
                    <td><a href="#" class="profile-link" data-mid="${m.id}">${m.first_name} ${m.last_name}</a></td>
                    <td><span class="roster-tag">${m.roster || '—'}</span></td>
                    <td class="num">${gender}</td>
                    <td class="num">${age}</td>
                    <td>${link}</td>
                    ${actions}
                </tr>`;
            });
        });

        html += '</tbody></table>';
        tableContainer.innerHTML = html;

        wireProfileLinks(tableContainer, id => rosterMembers.find(x => x.id === id));

        if (canEdit) {
            tableContainer.querySelectorAll('.roster-action-btn.edit').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    openEditModal(parseInt(btn.dataset.id));
                });
            });
        }
    }

    function openAddModal() {
        editingMemberId = null;
        modalTitle.textContent = 'Add Swimmer';
        deleteBtn.classList.add('hidden');
        fFirst.value = ''; fLast.value = ''; fRoster.value = '';
        fGender.value = ''; fBirthMonth.value = ''; fParentEmail.value = '';
        if (fNotes) fNotes.value = '';
        fDobHint.textContent = 'Year + month only. Or leave blank — age will auto-fill from CT Swim once linked.';
        errorBox.classList.add('hidden');
        modal.classList.remove('hidden');
        fFirst.focus();
    }

    function openEditModal(id) {
        const m = rosterMembers.find(x => x.id === id);
        if (!m) return;
        editingMemberId = id;
        modalTitle.textContent = `Edit: ${m.first_name} ${m.last_name}`;
        deleteBtn.classList.remove('hidden');
        fFirst.value = m.first_name;
        fLast.value = m.last_name;
        fRoster.value = m.roster || '';
        fGender.value = m.gender || '';
        fBirthMonth.value = ''; // never pre-fill — privacy
        fParentEmail.value = m.parent_email || '';
        if (fNotes) fNotes.value = m.notes || '';
        let hint;
        if (m.age != null && m.age_source === 'observed') {
            hint = `Age ${m.age} pulled from CT Swim. Enter year/month to override; leave blank to keep auto-updates.`;
        } else if (m.age != null && m.age_source === 'entered') {
            hint = `Currently age ${m.age} (from saved year/month). Leave blank to keep existing value.`;
        } else {
            hint = 'No age on file. Enter year/month — or leave blank and run a CT Swim fetch to auto-fill.';
        }
        fDobHint.textContent = hint;
        errorBox.classList.add('hidden');
        modal.classList.remove('hidden');
    }

    function closeModal() {
        modal.classList.add('hidden');
        editingMemberId = null;
    }

    async function saveMember() {
        const body = {
            first_name: fFirst.value.trim(),
            last_name: fLast.value.trim(),
            roster: fRoster.value.trim(),
            gender: fGender.value,
            birth_month_str: fBirthMonth.value, // 'YYYY-MM' or '' = don't change
            parent_email: fParentEmail.value.trim(),
            notes: fNotes ? fNotes.value : '',
        };
        if (!body.first_name || !body.last_name) {
            showModalError('First and last name are required.');
            return;
        }
        try {
            let r;
            if (editingMemberId) {
                r = await fetch(`/api/team_members/${editingMemberId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
            } else {
                r = await fetch('/api/team_members', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
            }
            const d = await r.json();
            if (d.error) { showModalError(d.error); return; }
            closeModal();
            await loadMembers();
        } catch (e) {
            showModalError('Save failed: ' + e.message);
        }
    }

    async function deleteMember() {
        if (!editingMemberId) return;
        const m = rosterMembers.find(x => x.id === editingMemberId);
        if (!confirm(`Delete ${m.first_name} ${m.last_name} from the roster?`)) return;
        try {
            const r = await fetch(`/api/team_members/${editingMemberId}`, { method: 'DELETE' });
            const d = await r.json();
            if (d.error) { showModalError(d.error); return; }
            closeModal();
            await loadMembers();
        } catch (e) {
            showModalError('Delete failed: ' + e.message);
        }
    }

    function showModalError(msg) {
        errorBox.textContent = msg;
        errorBox.classList.remove('hidden');
    }

    // Re-load when tab is opened (so admin batch -> roster shows fresh CT links)
    document.querySelector('[data-tab="roster"]').addEventListener('click', loadMembers);

    // Initial load
    loadMembers();
}


// ===== Auto-fill ages from CT Swim meet result PDFs (admin only) =====
if (hasPerm('batch')) {
    const afStartBtn = document.getElementById('afStartBtn');
    const afCancelBtn = document.getElementById('afCancelBtn');
    const afForceAll = document.getElementById('afForceAll');
    const afStatusEl = document.getElementById('afStatus');
    let afPollTimer = null;

    if (!afStartBtn || !afStatusEl) {
        // Element not present (e.g. user role lacks batch perm)
    } else {
        afStartBtn.addEventListener('click', async () => {
            afStartBtn.disabled = true;
            try {
                const r = await fetch('/api/team_members/autofill_ages/start', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({force_all: !!afForceAll.checked}),
                });
                const d = await r.json();
                if (!r.ok) {
                    afStatusEl.classList.remove('hidden');
                    afStatusEl.innerHTML = `<div class="af-row" style="color:#dc2626"><strong>${d.error || 'Failed to start'}</strong></div>`;
                    afStartBtn.disabled = false;
                    return;
                }
                renderAfStatus(d.status);
                startPolling();
            } catch (e) {
                afStartBtn.disabled = false;
                alert('Failed to start: ' + e.message);
            }
        });

        afCancelBtn.addEventListener('click', async () => {
            afCancelBtn.disabled = true;
            try { await fetch('/api/team_members/autofill_ages/cancel', {method: 'POST'}); }
            finally { afCancelBtn.disabled = false; }
        });

        // On load, check if a job is already running so refresh doesn't lose progress
        pollOnce();
    }

    async function pollOnce() {
        try {
            const r = await fetch('/api/team_members/autofill_ages/status');
            const s = await r.json();
            if (s.running || s.done > 0 || s.total > 0) {
                renderAfStatus(s);
                if (s.running) startPolling();
            }
        } catch (e) { /* ignore */ }
    }

    function startPolling() {
        if (afPollTimer) return;
        afPollTimer = setInterval(async () => {
            try {
                const r = await fetch('/api/team_members/autofill_ages/status');
                const s = await r.json();
                renderAfStatus(s);
                if (!s.running) {
                    clearInterval(afPollTimer);
                    afPollTimer = null;
                    afStartBtn.disabled = false;
                    // Reload roster table so newly computed ages show up
                    document.querySelector('[data-tab="roster"]')?.click();
                }
            } catch (e) {
                clearInterval(afPollTimer);
                afPollTimer = null;
                afStartBtn.disabled = false;
            }
        }, 2000);
    }

    function renderAfStatus(s) {
        afStatusEl.classList.remove('hidden');
        const pct = s.progress_pct || 0;
        const eta = s.eta_seconds || 0;
        const etaStr = eta > 60 ? `${Math.round(eta/60)}m` : `${eta}s`;
        const cur = s.current ? `Currently: ${s.current}` : (s.running ? 'Starting…' : 'Idle');
        afStartBtn.classList.toggle('hidden', !!s.running);
        afCancelBtn.classList.toggle('hidden', !s.running);
        const noWork = !s.running && s.total === 0 && s.finished_at;
        let html = `
            <div class="af-row">
                <strong>${s.done}/${s.total}</strong>
                <div class="af-bar-wrap"><div class="af-bar" style="width:${pct}%"></div></div>
                <span>${pct}%</span>
                ${s.running ? `<span style="color:#64748b">~${etaStr} left</span>` : ''}
            </div>
            <div class="af-counts">
                <span>Birth year/month: <strong>${s.updated || 0}</strong></span>
                <span>Gender filled: <strong>${s.gender_filled || 0}</strong></span>
                <span>Observed only: <strong>${s.observed_only || 0}</strong></span>
                <span>Not found: <strong>${s.not_found || 0}</strong></span>
                <span>Skipped: <strong>${s.skipped || 0}</strong></span>
                ${s.fully_resolved > 0 ? `<span style="color:#16a34a">Already locked-in: <strong>${s.fully_resolved}</strong></span>` : ''}
                ${s.cancelled ? '<span style="color:#dc2626"><strong>cancelled</strong></span>' : ''}
                ${(!s.running && s.finished_at && !noWork) ? '<span style="color:#16a34a"><strong>complete</strong></span>' : ''}
            </div>
            <div class="af-current">${noWork ? 'Everyone is up to date — birth year/month are saved permanently. Use "Force all" to override.' : cur}</div>
        `;
        if (s.errors && s.errors.length) {
            html += `<div class="af-errors">${s.errors.slice(-15).map(e => `<div>${escapeHtml(e)}</div>`).join('')}</div>`;
        }
        afStatusEl.innerHTML = html;
    }

    function escapeHtml(str) {
        return String(str).replace(/[&<>"']/g, c => ({
            '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
        }[c]));
    }
}


// ===== RESET ALL AGE DATA (admin escape hatch) =====
if (hasPerm('batch')) {
    const resetBtn = document.getElementById('resetAgeBtn');
    const resetKeep = document.getElementById('resetKeepUploaded');
    const resetStatus = document.getElementById('resetAgeStatus');
    if (resetBtn && resetStatus) {
        resetBtn.addEventListener('click', async () => {
            const keep = !!(resetKeep && resetKeep.checked);
            const desc = keep ? 'auto-discovered PDFs and all derived ages'
                              : 'every parsed PDF + every derived age';
            if (!confirm(`This will clear ${desc}, then re-run auto-fill from scratch.\n\nManual birth-year overrides in the roster modal will also be cleared. Continue?`)) {
                return;
            }
            resetBtn.disabled = true;
            resetStatus.innerHTML = '<span style="color:#64748b">Clearing data and starting auto-fill…</span>';
            try {
                const r = await fetch('/api/admin/reset_age_data', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({keep_uploaded: keep}),
                });
                const data = await r.json();
                if (!r.ok) {
                    resetStatus.innerHTML = `<span style="color:#dc2626">${data.error || 'Failed.'}</span>`;
                    resetBtn.disabled = false;
                    return;
                }
                resetStatus.innerHTML = `<span style="color:#16a34a">✓ Reset complete. Auto-fill ${data.auto_fill_started ? 'running now' : 'queued'}. Watch the Auto-fill panel for progress.</span>`;
            } catch (e) {
                resetStatus.innerHTML = `<span style="color:#dc2626">${e.message}</span>`;
                resetBtn.disabled = false;
            }
        });
    }
}


// ===== UNMATCHED MEETS PANEL (admin) =====
if (hasPerm('batch')) {
    const ummList = document.getElementById('ummList');
    const ummRefresh = document.getElementById('ummRefreshBtn');
    if (ummList && ummRefresh) {
        function escapeUmm(s) {
            return String(s ?? '').replace(/[&<>"']/g, c => ({
                '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
            }[c]));
        }

        async function loadUnmatched() {
            ummList.innerHTML = '<div class="umm-empty">Loading…</div>';
            try {
                const r = await fetch('/api/admin/unmatched_meets');
                if (r.status === 401) {
                    ummList.innerHTML = `<div class="umm-empty" style="color:#dc2626">Session expired. <a href="/login?next=/" style="color:#003366;text-decoration:underline">Re-login as admin</a> and click Refresh again.</div>`;
                    return;
                }
                if (r.status === 403) {
                    ummList.innerHTML = `<div class="umm-empty" style="color:#dc2626">This panel requires admin login (you're signed in as coach).</div>`;
                    return;
                }
                const data = await r.json();
                const meets = data.meets || [];
                if (meets.length === 0) {
                    ummList.innerHTML = '<div class="umm-empty" style="color:#16a34a">All meets have a matched PDF — nothing to register.</div>';
                    return;
                }
                ummList.innerHTML = meets.map(m => {
                    const name = escapeUmm(m.meet_name || '(no name)');
                    const date = escapeUmm(m.start_date || '?');
                    return `
                    <div class="umm-row" data-meet-id="${escapeUmm(m.ct_meet_id)}">
                        <div class="umm-meta">
                            <div class="umm-name">${name}</div>
                            <div class="umm-date">${date} <span class="umm-mid">• meet ${escapeUmm(m.ct_meet_id)}</span></div>
                        </div>
                        <div class="umm-form">
                            <input type="text" class="umm-url" placeholder="https://www.ctswim.org/Customer-Content/...pdf">
                            <button type="button" class="btn-primary umm-register" style="padding:0.4rem 0.8rem">Register</button>
                        </div>
                        <div class="umm-result"></div>
                    </div>`;
                }).join('');
            } catch (e) {
                ummList.innerHTML = `<div class="umm-empty" style="color:#dc2626">Error: ${escapeUmm(e.message)}</div>`;
            }
        }

        ummRefresh.addEventListener('click', loadUnmatched);

        ummList.addEventListener('click', async (ev) => {
            const btn = ev.target.closest('.umm-register');
            if (!btn) return;
            const row = btn.closest('.umm-row');
            const meetId = row.dataset.meetId;
            const urlInput = row.querySelector('.umm-url');
            const result = row.querySelector('.umm-result');
            const url = (urlInput.value || '').trim();
            if (!url) {
                result.innerHTML = '<span style="color:#dc2626">Paste a PDF URL first.</span>';
                return;
            }
            btn.disabled = true;
            result.innerHTML = '<span style="color:#64748b">Downloading + parsing…</span>';
            try {
                const r = await fetch('/api/admin/register_meet_pdf', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({meet_id: meetId, pdf_url: url}),
                });
                const data = await r.json();
                if (!r.ok) {
                    if (r.status === 401) {
                        result.innerHTML = `<span style="color:#dc2626">Session expired. <a href="/login?next=/" style="color:#003366;text-decoration:underline">Re-login as admin</a> and try again.</span>`;
                    } else if (r.status === 403) {
                        result.innerHTML = `<span style="color:#dc2626">Forbidden — this action requires the admin account, not coach.</span>`;
                    } else {
                        result.innerHTML = `<span style="color:#dc2626">${escapeUmm(data.error || 'failed')}</span>`;
                    }
                    btn.disabled = false;
                    return;
                }
                const sizeKb = (data.pdf_size/1024).toFixed(0);
                const n = data.parsed_rows || 0;
                const teamN = data.matched_team_members || 0;
                const af = data.auto_fill_started ? ' Auto-fill running now — refresh in 1-2 min.' : '';
                if (n === 0) {
                    result.innerHTML = `<span style="color:#d97706">⚠ Downloaded ${sizeKb} KB but parsed 0 swimmers — this PDF's layout isn't recognized. Enter birth year/month manually for affected swimmers in the Roster.</span>`;
                } else if (teamN === 0) {
                    result.innerHTML = `<span style="color:#d97706">⚠ Parsed ${n} swimmers but none match your roster. Likely the wrong meet's PDF.</span>`;
                    btn.textContent = 'Done';
                } else {
                    result.innerHTML = `<span style="color:#16a34a">✓ Parsed ${n} swimmers (${teamN} on your roster).${af}</span>`;
                    btn.textContent = 'Done';
                }
            } catch (e) {
                result.innerHTML = `<span style="color:#dc2626">${escapeUmm(e.message)}</span>`;
                btn.disabled = false;
            }
        });
    }
}


// ===== STANDARDS EDITOR =====
if (hasPerm('standards_edit')) {
    const editorState = {
        data: null,            // full STANDARDS_DATA loaded fresh from /api/standards
        programId: null,
        group: null,
        gender: null,
        course: null,
    };

    const seProgram = document.getElementById('seProgramSel');
    const seGroup = document.getElementById('seGroupSel');
    const seGender = document.getElementById('seGenderSel');
    const seCourse = document.getElementById('seCourseSel');
    const seGenderControl = document.getElementById('seGenderControl');
    const seCourseControl = document.getElementById('seCourseControl');
    const seGrid = document.getElementById('seGridContainer');
    const seAddForm = document.getElementById('seAddEventForm');
    const seSubtitle = document.getElementById('seSubtitle');
    const seSeason = document.getElementById('seSeason');
    const seEffective = document.getElementById('seEffective');

    async function loadEditorData() {
        try {
            const r = await fetch('/api/standards');
            editorState.data = await r.json();
            populateProgramSelector();
        } catch (e) {
            showSeError('Failed to load standards: ' + e.message);
        }
    }

    function populateProgramSelector() {
        const programs = editorState.data.programs || {};
        const ids = Object.keys(programs);
        seProgram.innerHTML = ids.map(id => {
            const p = programs[id];
            return `<option value="${id}">${p.display_name || id}</option>`;
        }).join('');
        editorState.programId = ids[0] || null;
        if (editorState.programId) onProgramChange();
    }

    function onProgramChange() {
        editorState.programId = seProgram.value;
        const prog = editorState.data.programs[editorState.programId];
        if (!prog) return;

        // Metadata fields
        seSubtitle.value = prog.subtitle || '';
        seSeason.value = prog.season || '';
        seEffective.value = prog.effective_date || '';

        // Groups dropdown
        const groups = Object.keys(prog.groups || {});
        seGroup.innerHTML = groups.map(g => `<option value="${g}">${g}</option>`).join('');
        editorState.group = groups[0] || null;

        // Show/hide gender + course controls based on program shape
        if (prog.multi_level) {
            seGenderControl.classList.add('hidden');
            seCourseControl.classList.add('hidden');
        } else {
            seGenderControl.classList.remove('hidden');
            seCourseControl.classList.remove('hidden');
            const genderKeys = prog.gender_keys || [];
            seGender.innerHTML = genderKeys.map(gk => {
                const label = (prog.gender_labels && prog.gender_labels[gk]) || gk;
                return `<option value="${gk}">${label}</option>`;
            }).join('');
            editorState.gender = genderKeys[0] || null;

            seCourse.innerHTML = ['SCY', 'LCM'].map(c => `<option value="${c}">${c}</option>`).join('');
            editorState.course = 'SCY';
        }

        renderGrid();
    }

    function onGroupChange() {
        editorState.group = seGroup.value;
        renderGrid();
    }
    function onGenderChange() {
        editorState.gender = seGender.value;
        renderGrid();
    }
    function onCourseChange() {
        editorState.course = seCourse.value;
        renderGrid();
    }

    function renderGrid() {
        const prog = editorState.data.programs[editorState.programId];
        if (!prog || !editorState.group) {
            seGrid.innerHTML = '';
            return;
        }
        const grp = prog.groups[editorState.group];
        if (!grp) {
            seGrid.innerHTML = '';
            return;
        }

        let html = '';
        if (prog.multi_level) {
            // USA-style: events x levels grid
            const levels = grp.levels || [];
            html = '<table><thead><tr><th>Event</th>';
            levels.forEach(l => { html += `<th>${l}</th>`; });
            html += '<th>Actions</th></tr></thead><tbody>';
            grp.events.forEach((event, idx) => {
                const row = grp.times[idx] || [];
                html += `<tr data-event-idx="${idx}">`;
                html += `<td class="event-name-cell">${event}</td>`;
                levels.forEach((_, li) => {
                    const v = row[li] || '';
                    html += `<td><input class="cell-input" data-li="${li}" value="${v}"></td>`;
                });
                html += `<td><div class="se-row-actions">
                    <button class="se-row-action save" data-action="save" disabled>Save</button>
                    <button class="se-row-action delete" data-action="delete">Delete</button>
                </div></td></tr>`;
            });
            html += '</tbody></table>';
        } else {
            // CT/EZ-style: single column for selected gender+course
            const arr = (grp[editorState.gender] && grp[editorState.gender][editorState.course]) || [];
            html = '<table><thead><tr><th>Event</th><th>Time</th><th>Actions</th></tr></thead><tbody>';
            grp.events.forEach((event, idx) => {
                const v = arr[idx] || '';
                html += `<tr data-event-idx="${idx}">
                    <td class="event-name-cell">${event}</td>
                    <td><input class="cell-input" value="${v}"></td>
                    <td><div class="se-row-actions">
                        <button class="se-row-action save" data-action="save" disabled>Save</button>
                        <button class="se-row-action delete" data-action="delete">Delete</button>
                    </div></td>
                </tr>`;
            });
            html += '</tbody></table>';
        }
        seGrid.innerHTML = html;
        attachGridHandlers();
        renderAddEventForm();
    }

    function attachGridHandlers() {
        seGrid.querySelectorAll('tr[data-event-idx]').forEach(tr => {
            const idx = parseInt(tr.dataset.eventIdx);
            const inputs = tr.querySelectorAll('input.cell-input');
            const saveBtn = tr.querySelector('[data-action="save"]');
            const delBtn = tr.querySelector('[data-action="delete"]');

            inputs.forEach(inp => {
                inp.addEventListener('input', () => {
                    inp.classList.add('dirty');
                    saveBtn.disabled = false;
                });
            });

            saveBtn.addEventListener('click', () => saveRow(idx, inputs, saveBtn));
            delBtn.addEventListener('click', () => deleteRow(idx, tr.querySelector('.event-name-cell').textContent));
        });
    }

    async function saveRow(idx, inputs, saveBtn) {
        const prog = editorState.data.programs[editorState.programId];
        const url = `/api/standards/program/${encodeURIComponent(editorState.programId)}/group/${encodeURIComponent(editorState.group)}/event/${idx}`;
        let body = {};

        if (prog.multi_level) {
            const grp = prog.groups[editorState.group];
            const times = grp.levels.map((_, li) => {
                const inp = Array.from(inputs).find(x => parseInt(x.dataset.li) === li);
                return inp ? inp.value.trim() : '';
            });
            body.times = times;
        } else {
            const inp = inputs[0];
            body[editorState.gender] = { [editorState.course]: inp.value.trim() };
        }

        saveBtn.disabled = true;
        saveBtn.textContent = 'Saving...';
        try {
            const r = await fetch(url, {
                method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const d = await r.json();
            if (d.error) { showSeError(d.error); saveBtn.disabled = false; saveBtn.textContent = 'Save'; return; }
            // Update local state with returned program
            editorState.data.programs[editorState.programId] = d.program;
            inputs.forEach(i => i.classList.remove('dirty'));
            saveBtn.textContent = 'Save';
            showSeToast('Saved');
        } catch (e) {
            showSeError('Save failed: ' + e.message);
            saveBtn.disabled = false;
            saveBtn.textContent = 'Save';
        }
    }

    async function deleteRow(idx, eventName) {
        if (!confirm(`Delete the "${eventName}" row from ${editorState.group}?`)) return;
        const url = `/api/standards/program/${encodeURIComponent(editorState.programId)}/group/${encodeURIComponent(editorState.group)}/event/${idx}`;
        try {
            const r = await fetch(url, { method: 'DELETE' });
            const d = await r.json();
            if (d.error) { showSeError(d.error); return; }
            editorState.data.programs[editorState.programId] = d.program;
            renderGrid();
            showSeToast('Deleted');
        } catch (e) {
            showSeError('Delete failed: ' + e.message);
        }
    }

    function renderAddEventForm() {
        const prog = editorState.data.programs[editorState.programId];
        if (!prog || !editorState.group) { seAddForm.innerHTML = ''; return; }
        const grp = prog.groups[editorState.group];
        let html = `<div class="ae-cell"><label>Event Name</label><input type="text" id="aeName" placeholder="e.g. 50 FREE"></div>`;

        if (prog.multi_level) {
            grp.levels.forEach((l, li) => {
                html += `<div class="ae-cell"><label>${l}</label><input type="text" data-level-idx="${li}" class="ae-time" placeholder="0:00.00"></div>`;
            });
        } else {
            (prog.gender_keys || []).forEach(gk => {
                const label = (prog.gender_labels && prog.gender_labels[gk]) || gk;
                ['SCY', 'LCM'].forEach(c => {
                    html += `<div class="ae-cell"><label>${label} ${c}</label><input type="text" data-gk="${gk}" data-course="${c}" class="ae-time" placeholder="0:00.00"></div>`;
                });
            });
        }
        html += `<button id="aeAddBtn" type="button">Add Event</button>`;
        seAddForm.innerHTML = html;

        document.getElementById('aeAddBtn').addEventListener('click', addNewEvent);
    }

    async function addNewEvent() {
        const name = document.getElementById('aeName').value.trim();
        if (!name) { showSeError('Event name is required'); return; }
        const prog = editorState.data.programs[editorState.programId];
        const url = `/api/standards/program/${encodeURIComponent(editorState.programId)}/group/${encodeURIComponent(editorState.group)}/event`;
        let body = { event_name: name };

        if (prog.multi_level) {
            const grp = prog.groups[editorState.group];
            const times = grp.levels.map((_, li) => {
                const el = seAddForm.querySelector(`input[data-level-idx="${li}"]`);
                return el ? el.value.trim() : '';
            });
            body.times = times;
        } else {
            (prog.gender_keys || []).forEach(gk => {
                body[gk] = {};
                ['SCY', 'LCM'].forEach(c => {
                    const el = seAddForm.querySelector(`input[data-gk="${gk}"][data-course="${c}"]`);
                    body[gk][c] = el ? el.value.trim() : '';
                });
            });
        }

        try {
            const r = await fetch(url, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const d = await r.json();
            if (d.error) { showSeError(d.error); return; }
            editorState.data.programs[editorState.programId] = d.program;
            renderGrid();
            showSeToast('Event added');
            // Clear inputs
            seAddForm.querySelectorAll('input').forEach(i => i.value = '');
        } catch (e) {
            showSeError('Add failed: ' + e.message);
        }
    }

    document.getElementById('seSaveMetaBtn').addEventListener('click', async () => {
        const url = `/api/standards/program/${encodeURIComponent(editorState.programId)}/metadata`;
        const body = {
            subtitle: seSubtitle.value.trim(),
            season: seSeason.value.trim(),
            effective_date: seEffective.value.trim(),
        };
        try {
            const r = await fetch(url, {
                method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const d = await r.json();
            if (d.error) { showSeError(d.error); return; }
            editorState.data.programs[editorState.programId] = d.program;
            showSeToast('Metadata saved');
        } catch (e) {
            showSeError('Save failed: ' + e.message);
        }
    });

    function showSeError(msg) {
        const e = document.getElementById('seError');
        e.textContent = msg;
        e.classList.remove('hidden');
        setTimeout(() => e.classList.add('hidden'), 5000);
    }
    function showSeToast(msg) {
        const t = document.getElementById('seToast');
        t.textContent = msg;
        t.classList.remove('hidden');
        setTimeout(() => t.classList.add('hidden'), 1800);
    }

    seProgram.addEventListener('change', onProgramChange);
    seGroup.addEventListener('change', onGroupChange);
    seGender.addEventListener('change', onGenderChange);
    seCourse.addEventListener('change', onCourseChange);

    // Lazy-load: only fetch when tab opens
    document.querySelector('[data-tab="stdedit"]').addEventListener('click', () => {
        if (!editorState.data) loadEditorData();
    });
}


// ===== STANDARDS EDITOR: Add Program / Group / Delete =====
if (hasPerm('standards_edit')) {
    const apModal = document.getElementById('addProgramModal');
    const apType = document.getElementById('apType');
    const apGenderRow = document.getElementById('apGenderRow');
    const apLevelsRow = document.getElementById('apLevelsRow');
    const apError = document.getElementById('apError');

    function openAddProgramModal() {
        ['apId', 'apName', 'apSubtitle', 'apSeason', 'apEffective', 'apGroups'].forEach(id => {
            document.getElementById(id).value = '';
        });
        document.getElementById('apLevels').value = 'AAAA,AAA,AA,A,BB,B';
        apType.value = 'single';
        apGenderRow.classList.remove('hidden');
        apLevelsRow.classList.add('hidden');
        apError.classList.add('hidden');
        apModal.classList.remove('hidden');
        document.getElementById('apId').focus();
    }
    function closeAddProgramModal() {
        apModal.classList.add('hidden');
    }

    apType.addEventListener('change', () => {
        const isMulti = apType.value === 'multi';
        apGenderRow.classList.toggle('hidden', isMulti);
        apLevelsRow.classList.toggle('hidden', !isMulti);
    });

    document.getElementById('seAddProgramBtn').addEventListener('click', openAddProgramModal);
    document.getElementById('apClose').addEventListener('click', closeAddProgramModal);
    document.getElementById('apCancel').addEventListener('click', closeAddProgramModal);

    document.getElementById('apSave').addEventListener('click', async () => {
        const programId = document.getElementById('apId').value.trim();
        const name = document.getElementById('apName').value.trim();
        if (!programId || !name) {
            apError.textContent = 'Program ID and Display Name are required.';
            apError.classList.remove('hidden');
            return;
        }
        const isMulti = apType.value === 'multi';
        const groupsRaw = document.getElementById('apGroups').value.trim();
        const groups = groupsRaw ? groupsRaw.split(',').map(s => s.trim()).filter(Boolean) : [];

        const body = {
            program_id: programId,
            display_name: name,
            subtitle: document.getElementById('apSubtitle').value.trim(),
            season: document.getElementById('apSeason').value.trim(),
            effective_date: document.getElementById('apEffective').value,
            multi_level: isMulti,
            groups,
        };
        if (isMulti) {
            const levelsRaw = document.getElementById('apLevels').value.trim();
            body.levels = levelsRaw ? levelsRaw.split(',').map(s => s.trim()).filter(Boolean) : [];
        } else {
            const [a, b] = document.getElementById('apGenderType').value.split(',');
            body.gender_keys = [a, b];
        }

        try {
            const r = await fetch('/api/standards/program', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const d = await r.json();
            if (d.error) { apError.textContent = d.error; apError.classList.remove('hidden'); return; }
            // Reload editor data
            const rr = await fetch('/api/standards');
            window.STANDARDS_DATA = await rr.json();
            // Re-init editor data
            location.reload(); // simplest: full reload to refresh everything everywhere
        } catch (e) {
            apError.textContent = 'Failed: ' + e.message;
            apError.classList.remove('hidden');
        }
    });

    // Delete program
    document.getElementById('seDeleteProgramBtn').addEventListener('click', async () => {
        const sel = document.getElementById('seProgramSel');
        const id = sel.value;
        const name = sel.options[sel.selectedIndex]?.text || id;
        if (!confirm(`Delete the entire "${name}" program? This cannot be undone.`)) return;
        try {
            const r = await fetch(`/api/standards/program/${encodeURIComponent(id)}`, { method: 'DELETE' });
            const d = await r.json();
            if (d.error) { alert(d.error); return; }
            location.reload();
        } catch (e) { alert('Delete failed: ' + e.message); }
    });

    // Add group
    document.getElementById('seAddGroupBtn').addEventListener('click', async () => {
        const programId = document.getElementById('seProgramSel').value;
        if (!programId) return;
        const name = (prompt('New age group name (e.g., 15-16):') || '').trim();
        if (!name) return;
        try {
            const r = await fetch(`/api/standards/program/${encodeURIComponent(programId)}/group`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ group_name: name }),
            });
            const d = await r.json();
            if (d.error) { alert(d.error); return; }
            location.reload();
        } catch (e) { alert('Add failed: ' + e.message); }
    });

    // Delete group
    document.getElementById('seDeleteGroupBtn').addEventListener('click', async () => {
        const programId = document.getElementById('seProgramSel').value;
        const group = document.getElementById('seGroupSel').value;
        if (!programId || !group) return;
        if (!confirm(`Delete group "${group}" from this program?`)) return;
        try {
            const r = await fetch(`/api/standards/program/${encodeURIComponent(programId)}/group/${encodeURIComponent(group)}`, { method: 'DELETE' });
            const d = await r.json();
            if (d.error) { alert(d.error); return; }
            location.reload();
        } catch (e) { alert('Delete failed: ' + e.message); }
    });
}


// ===== COACH DASHBOARD =====
if (hasPerm('dashboard')) {
    const dbState = {
        members: [], filtered: [], filters: {},
        matrix: { distances: null, levelFilter: 'all', compact: false, hideEmpty: true },
    };

    const dbAge = document.getElementById('dbAge');
    const dbGender = document.getElementById('dbGender');
    const dbRoster = document.getElementById('dbRoster');
    const dbStroke = document.getElementById('dbStroke');
    const dbCourse = document.getElementById('dbCourse');
    const dbStats = document.getElementById('dbStats');
    const dbMatrixDistPills = document.getElementById('dbMatrixDistPills');
    const dbMatrixLevelFilter = document.getElementById('dbMatrixLevelFilter');
    const dbMatrixCompact = document.getElementById('dbMatrixCompact');
    const dbMatrixHideEmpty = document.getElementById('dbMatrixHideEmpty');

    function ageToAgeGroup(age) {
        if (age == null) return null;
        if (age <= 10) return '10/Under';
        if (age <= 12) return '11/12';
        if (age <= 14) return '13/14';
        if (age <= 16) return '15/16';
        return '17/18';
    }

    async function loadDashboard() {
        try {
            const r = await fetch('/api/dashboard');
            const d = await r.json();
            dbState.members = d.members || [];
            // Populate roster groups
            const rosters = [...new Set(dbState.members.map(m => m.roster).filter(Boolean))].sort();
            dbRoster.innerHTML = '<option value="">All Groups</option>' +
                rosters.map(r => `<option value="${r}">${r}</option>`).join('');
            applyFilters();
        } catch (e) {
            dbStats.textContent = 'Failed to load dashboard data';
        }
    }

    function applyFilters() {
        const ag = dbAge.value;
        const g = dbGender.value;
        const ros = dbRoster.value;

        dbState.filters = {
            ageGroup: ag, gender: g, roster: ros,
            stroke: dbStroke.value, course: dbCourse.value || 'SCY',
        };
        dbState.filtered = dbState.members.filter(m => {
            if (g && m.gender !== g) return false;
            if (ros && m.roster !== ros) return false;
            if (ag) {
                const memAg = ageToAgeGroup(m.age);
                if (memAg !== ag) return false;
            }
            return true;
        });

        renderStats();
        renderUsaDistribution();
        renderTeamSpecialty();
        renderActionItems();
        renderEventLeaderboard();
        renderEventMatrix();
        renderClosestToCut();
        renderChampSpotlight();
    }

    function renderStats() {
        const total = dbState.filtered.length;
        const linked = dbState.filtered.filter(m => m.ct_id).length;
        const unmatched = total - linked;
        const withAge = dbState.filtered.filter(m => m.age != null).length;
        const withGender = dbState.filtered.filter(m => m.gender).length;
        let bits = [`${total} swimmers`];
        if (linked < total) bits.push(`${unmatched} not matched in CT Swim`);
        if (withGender < total) bits.push(`${total - withGender} missing gender`);
        if (withAge < total) bits.push(`${total - withAge} missing age`);
        dbStats.textContent = bits.join(' • ');
    }

    const USA_LEVEL_RANK = { B: 1, BB: 2, A: 3, AA: 4, AAA: 5, AAAA: 6 };

    // Returns the highest USA level (string, e.g. 'AA') achieved across all events for a swimmer
    // restricted to optional stroke filter and the selected course
    function highestLevelForSwimmer(member, options = {}) {
        if (!member.ct_id || !member.gender || !member.age) return null;
        const ag = ageToAgeGroup(member.age);
        const course = options.course || dbState.filters.course;
        const strokeFilter = options.stroke || null;
        let bestRank = 0, bestType = null;

        (member.best_times || []).forEach(ev => {
            const ei = normalizeEvent(ev.event);
            if (!ei || ei.course !== course) return;
            if (strokeFilter && ei.stroke !== strokeFilter) return;
            const std = lookupStandards(ei, ag, member.gender);
            const t = timeToSeconds(ev.time);
            std.usa.forEach(s => {
                if (t <= timeToSeconds(s.time)) {
                    const rank = USA_LEVEL_RANK[s.type] || 0;
                    if (rank > bestRank) { bestRank = rank; bestType = s.type; }
                }
            });
        });
        return bestType;
    }

    // Per-event USA distribution. Course + event are local controls (default 50 Free SCY).
    // Independent of dbState.filters.course so coach can compare quickly.
    const dbDistState = { course: 'SCY', evtIdx: 0 };

    function getDistEventList() {
        return EVENT_CATALOG[dbDistState.course] || EVENT_CATALOG.SCY;
    }

    function populateDistEventDropdown() {
        const sel = document.getElementById('dbDistEvent');
        if (!sel) return;
        const events = getDistEventList();
        const prevLabel = events[dbDistState.evtIdx]?.label;
        sel.innerHTML = events.map((e, i) => `<option value="${i}">${e.label}</option>`).join('');
        // Try to keep the same event after course switch
        const matchIdx = events.findIndex(e => e.label === prevLabel);
        if (matchIdx >= 0) {
            sel.value = String(matchIdx);
            dbDistState.evtIdx = matchIdx;
        } else {
            // Default to "50 Free" (first entry in both courses) on first paint
            sel.value = '0';
            dbDistState.evtIdx = 0;
        }
    }

    function renderUsaDistribution() {
        const els = document.getElementById('dbUsaDist');
        const hint = document.getElementById('dbDistHint');
        if (!els) return;
        populateDistEventDropdown();
        const cat = getDistEventList()[dbDistState.evtIdx];
        if (!cat) { els.innerHTML = ''; if (hint) hint.textContent = ''; return; }

        // Bucket every eligible swimmer by USA level FOR THIS EVENT.
        // 'belowB' = has a time but no cut hit yet.
        const counts = { belowB: 0, B: 0, BB: 0, A: 0, AA: 0, AAA: 0, AAAA: 0 };
        let withTime = 0;
        const eligible = dbState.filtered.filter(m => m.gender && m.age && m.ct_id);
        // Temporarily flip the filter course so findBestForCatalog/levelForSwimmerEvent
        // resolve against dbDistState.course (those helpers read dbState.filters.course).
        const origCourse = dbState.filters.course;
        dbState.filters.course = dbDistState.course;
        try {
            eligible.forEach(m => {
                const found = findBestForCatalog(m, cat);
                if (!found) return;
                withTime++;
                const lv = levelForSwimmerEvent(m, cat);
                if (lv && lv.level) counts[lv.level]++;
                else counts.belowB++;
            });
        } finally {
            dbState.filters.course = origCourse;
        }

        if (hint) {
            const noTime = eligible.length - withTime;
            hint.textContent = `${withTime}/${eligible.length} have a time` + (noTime ? ` · ${noTime} no time yet` : '');
        }

        const buckets = [
            { k: 'belowB', label: '&lt;B', cls: 'level-below-b', drillKind: 'below-b' },
            { k: 'B',      label: 'B',    cls: 'level-b',       drillKind: 'level' },
            { k: 'BB',     label: 'BB',   cls: 'level-bb',      drillKind: 'level' },
            { k: 'A',      label: 'A',    cls: 'level-a',       drillKind: 'level' },
            { k: 'AA',     label: 'AA',   cls: 'level-aa',      drillKind: 'level' },
            { k: 'AAA',    label: 'AAA',  cls: 'level-aaa',     drillKind: 'level' },
            { k: 'AAAA',   label: 'AAAA', cls: 'level-aaaa',    drillKind: 'level' },
        ];

        if (withTime === 0) {
            els.innerHTML = '<div style="padding:1rem;color:#94a3b8">No swimmers have a time recorded for this event yet.</div>';
            return;
        }

        els.innerHTML = buckets.map(b => {
            const n = counts[b.k];
            const clickable = n > 0;
            return `<div class="ladder-level ${b.cls}${n > 0 ? ' has-events' : ''}${clickable ? ' clickable' : ''}"
                          ${clickable ? `data-bucket="${b.k}" data-drill="${b.drillKind}"` : ''}>
                <div class="level-name">${b.label}</div>
                <div class="level-count">${n}</div>
                <div class="level-label">${n === 1 ? 'swimmer' : 'swimmers'}</div>
            </div>`;
        }).join('');

        // Wire clicks to the existing drill-down modal
        els.querySelectorAll('.ladder-level.clickable').forEach(node => {
            node.addEventListener('click', () => {
                const bucket = node.getAttribute('data-bucket');
                const drillKind = node.getAttribute('data-drill');
                // Need to flip course so the drill modal pulls times for the right course
                const origC = dbState.filters.course;
                dbState.filters.course = dbDistState.course;
                try {
                    if (drillKind === 'below-b') {
                        openDrillModal({ kind: 'below-b', cat, level: null });
                    } else {
                        openDrillModal({ kind: 'level', cat, level: bucket });
                    }
                } finally {
                    dbState.filters.course = origC;
                }
            });
        });
    }

    function levelToCls(level) {
        if (!level) return 'evt-empty';
        return 'evt-' + level.toLowerCase();
    }

    // ===== Event catalog: events that can show up in matrix/leaderboard =====
    const EVENT_CATALOG = {
        SCY: [
            { dist: '50',  stroke: 'FREE',   label: '50 Free' },
            { dist: '100', stroke: 'FREE',   label: '100 Free' },
            { dist: '200', stroke: 'FREE',   label: '200 Free' },
            { dist: '500', stroke: 'FREE',   label: '500 Free' },
            { dist: '1000',stroke: 'FREE',   label: '1000 Free' },
            { dist: '1650',stroke: 'FREE',   label: '1650 Free' },
            { dist: '50',  stroke: 'BACK',   label: '50 Back' },
            { dist: '100', stroke: 'BACK',   label: '100 Back' },
            { dist: '200', stroke: 'BACK',   label: '200 Back' },
            { dist: '50',  stroke: 'BREAST', label: '50 Breast' },
            { dist: '100', stroke: 'BREAST', label: '100 Breast' },
            { dist: '200', stroke: 'BREAST', label: '200 Breast' },
            { dist: '50',  stroke: 'FLY',    label: '50 Fly' },
            { dist: '100', stroke: 'FLY',    label: '100 Fly' },
            { dist: '200', stroke: 'FLY',    label: '200 Fly' },
            { dist: '100', stroke: 'IM',     label: '100 IM' },
            { dist: '200', stroke: 'IM',     label: '200 IM' },
            { dist: '400', stroke: 'IM',     label: '400 IM' },
        ],
        LCM: [
            { dist: '50',  stroke: 'FREE',   label: '50 Free' },
            { dist: '100', stroke: 'FREE',   label: '100 Free' },
            { dist: '200', stroke: 'FREE',   label: '200 Free' },
            { dist: '400', stroke: 'FREE',   label: '400 Free' },
            { dist: '800', stroke: 'FREE',   label: '800 Free' },
            { dist: '1500',stroke: 'FREE',   label: '1500 Free' },
            { dist: '50',  stroke: 'BACK',   label: '50 Back' },
            { dist: '100', stroke: 'BACK',   label: '100 Back' },
            { dist: '200', stroke: 'BACK',   label: '200 Back' },
            { dist: '50',  stroke: 'BREAST', label: '50 Breast' },
            { dist: '100', stroke: 'BREAST', label: '100 Breast' },
            { dist: '200', stroke: 'BREAST', label: '200 Breast' },
            { dist: '50',  stroke: 'FLY',    label: '50 Fly' },
            { dist: '100', stroke: 'FLY',    label: '100 Fly' },
            { dist: '200', stroke: 'FLY',    label: '200 Fly' },
            { dist: '200', stroke: 'IM',     label: '200 IM' },
            { dist: '400', stroke: 'IM',     label: '400 IM' },
        ],
    };

    function getColumnEvents() {
        const course = dbState.filters.course || 'SCY';
        const stroke = dbState.filters.stroke || null;
        let cat = EVENT_CATALOG[course] || EVENT_CATALOG.SCY;
        if (stroke) cat = cat.filter(e => e.stroke === stroke);
        return cat;
    }

    // Find a swimmer's best time matching a catalog event (course-aware)
    function findBestForCatalog(member, catEvent) {
        const want = `${catEvent.dist} ${catEvent.stroke}`;
        const course = dbState.filters.course || 'SCY';
        for (const ev of (member.best_times || [])) {
            const ei = normalizeEvent(ev.event);
            if (!ei || ei.course !== course) continue;
            if (ei.distance === catEvent.dist && ei.stroke === catEvent.stroke) {
                return { time: ev.time, eventInfo: ei, raw: ev.event };
            }
        }
        return null;
    }

    // Compute the highest USA level achieved for a swimmer + event
    function levelForSwimmerEvent(member, catEvent) {
        if (!member.gender || !member.age) return null;
        const found = findBestForCatalog(member, catEvent);
        if (!found) return null;
        const ag = ageToAgeGroup(member.age);
        const std = lookupStandards(found.eventInfo, ag, member.gender);
        const swSecs = timeToSeconds(found.time);
        let bestType = null, bestRank = 0;
        std.usa.forEach(s => {
            if (swSecs <= timeToSeconds(s.time)) {
                const rank = USA_LEVEL_RANK[s.type] || 0;
                if (rank > bestRank) { bestRank = rank; bestType = s.type; }
            }
        });
        return { time: found.time, level: bestType };
    }

    function renderMatrixDistPills() {
        const cols = getColumnEvents();
        const distances = [...new Set(cols.map(c => c.dist))];
        // Initialize selection on first render or when course/stroke changes
        if (!dbState.matrix.distances) {
            dbState.matrix.distances = new Set(distances);
        } else {
            // Drop distances no longer applicable
            [...dbState.matrix.distances].forEach(d => {
                if (!distances.includes(d)) dbState.matrix.distances.delete(d);
            });
            // If everything got cleared by filter change, default to all
            if (dbState.matrix.distances.size === 0) {
                dbState.matrix.distances = new Set(distances);
            }
        }
        if (!dbMatrixDistPills) return;
        dbMatrixDistPills.innerHTML = distances.map(d => {
            const on = dbState.matrix.distances.has(d);
            return `<button type="button" class="mc-pill${on ? ' on' : ''}" data-dist="${d}">${d}</button>`;
        }).join('') +
        `<button type="button" class="mc-pill mc-pill-all" data-dist="__all">All</button>` +
        `<button type="button" class="mc-pill mc-pill-all" data-dist="__none">None</button>`;
    }

    // Compare a level against a filter token
    function passesLevelFilter(cell, filter) {
        if (filter === 'all') return true;
        if (filter === 'any') return !!cell;
        const r = cell && cell.level ? (USA_LEVEL_RANK[cell.level] || 0) : 0;
        const hasTime = !!cell;
        switch (filter) {
            case 'below-bb': return hasTime && r < 2;
            case 'b-bb':    return r >= 1 && r <= 2;
            case 'a-plus':  return r >= 3;
            case 'aa-plus': return r >= 4;
            case 'aaa-plus':return r >= 5;
            default: return true;
        }
    }

    function renderEventMatrix() {
        const container = document.getElementById('dbEventMatrix');
        if (dbState.filtered.length === 0) {
            container.innerHTML = '<p style="padding:1rem;color:#94a3b8">No swimmers match.</p>';
            return;
        }
        renderMatrixDistPills();
        const allCols = getColumnEvents();
        const distSel = dbState.matrix.distances;
        const cols = allCols.filter(c => distSel.has(c.dist));
        if (cols.length === 0) {
            container.innerHTML = '<p style="padding:1rem;color:#94a3b8">No events match the distance filter. Pick at least one.</p>';
            return;
        }

        const levelFilter = dbState.matrix.levelFilter;
        const compact = dbState.matrix.compact;
        const hideEmpty = dbState.matrix.hideEmpty;

        // Build per-swimmer cells; sort by overall achievement score
        let rows = dbState.filtered.map(m => {
            const cells = cols.map(ev => levelForSwimmerEvent(m, ev));
            const score = cells.reduce((s, c) => s + (c && c.level ? (USA_LEVEL_RANK[c.level] || 0) : (c ? 0.5 : 0)), 0);
            const anyMatch = cells.some(c => passesLevelFilter(c, levelFilter));
            const anyTime = cells.some(c => !!c);
            return { member: m, cells, score, anyMatch, anyTime };
        });

        // Apply level filter — drop rows with no matching cell
        if (levelFilter !== 'all') {
            rows = rows.filter(r => r.anyMatch);
        }
        if (hideEmpty) {
            rows = rows.filter(r => r.anyTime);
        }
        rows.sort((a, b) => b.score - a.score);

        if (rows.length === 0) {
            container.innerHTML = '<p style="padding:1rem;color:#94a3b8">No swimmers match the matrix filters. Loosen the level filter or include more distances.</p>';
            return;
        }

        let html = '<table><thead><tr><th class="swimmer-col">Swimmer</th><th>Group</th>';
        cols.forEach(c => { html += `<th>${c.label}</th>`; });
        html += '</tr></thead><tbody>';

        rows.forEach(({ member, cells }) => {
            const ageStr = member.age != null ? `<span class="age-tag">${member.age}</span>` : '';
            const genderStr = member.gender ? `<span class="age-tag">${member.gender}</span>` : '';
            html += `<tr>
                <td class="swimmer-cell"><a href="#" class="profile-link" data-mid="${member.id}">${member.first_name} ${member.last_name}</a>${ageStr}${genderStr}</td>
                <td><span class="roster-tag">${member.roster || '—'}</span></td>`;
            cells.forEach(c => {
                if (!c) {
                    html += `<td class="evt-cell evt-empty">—</td>`;
                } else {
                    const cls = c.level ? levelToCls(c.level) : 'evt-no-level';
                    if (compact) {
                        const lbl = c.level || '·';
                        html += `<td class="evt-cell evt-compact ${cls}" title="${c.time}">${lbl}</td>`;
                    } else {
                        const badge = c.level ? `<div style="font-size:0.65rem;opacity:0.85">${c.level}</div>` : '';
                        html += `<td class="evt-cell ${cls}">${c.time}${badge}</td>`;
                    }
                }
            });
            html += '</tr>';
        });
        html += '</tbody></table>';
        container.innerHTML = html;
        wireProfileLinks(container, id => dbState.filtered.find(m => m.id === id));
    }

    function renderEventLeaderboard() {
        const sel = document.getElementById('dbLeaderEvent');
        const board = document.getElementById('dbLeaderboard');
        const hint = document.getElementById('dbLeaderHint');

        // Populate dropdown from getColumnEvents (preserves selection if still valid)
        const events = getColumnEvents();
        const prev = sel.value;
        sel.innerHTML = events.map((e, i) =>
            `<option value="${i}">${e.label} (${dbState.filters.course || 'SCY'})</option>`
        ).join('');
        if (prev && prev < events.length) sel.value = prev;

        const evIdx = parseInt(sel.value || '0');
        const cat = events[evIdx];
        if (!cat) { board.innerHTML = ''; hint.textContent = ''; return; }

        // Collect every swimmer with a time in this event
        const rows = [];
        dbState.filtered.forEach(m => {
            const found = findBestForCatalog(m, cat);
            if (!found) return;
            const lvl = levelForSwimmerEvent(m, cat);
            const ag = m.age != null ? ageToAgeGroup(m.age) : null;
            let nextCut = null, nextGap = null;
            if (m.gender && ag) {
                const std = lookupStandards(found.eventInfo, ag, m.gender);
                const cmp = compareToStandards(timeToSeconds(found.time), std);
                if (cmp.usaNext) {
                    nextCut = cmp.usaNext;
                    nextGap = timeToSeconds(found.time) - timeToSeconds(cmp.usaNext.time);
                }
            }
            rows.push({
                member: m,
                time: found.time,
                secs: timeToSeconds(found.time),
                level: lvl ? lvl.level : null,
                nextCut, nextGap,
            });
        });

        rows.sort((a, b) => a.secs - b.secs);
        hint.textContent = `${rows.length} swimmer${rows.length === 1 ? '' : 's'} have a time in this event`;

        if (rows.length === 0) {
            board.innerHTML = '<p style="padding:1rem;color:#94a3b8">No times recorded for this event in the current filter.</p>';
            return;
        }

        let html = '<table><thead><tr><th>#</th><th>Swimmer</th><th>Group</th><th>Age</th><th>Time</th><th>Level</th><th>Next Cut</th></tr></thead><tbody>';
        rows.forEach((r, i) => {
            const rankCls = i < 3 ? `lead-rank-${i + 1}` : '';
            const ageStr = r.member.age != null ? r.member.age : '—';
            const lvlBadge = r.level ? `<span class="badge badge-${r.level.toLowerCase()}">${r.level}</span>` : '<span style="color:#cbd5e0">—</span>';
            let nextStr = '<span style="color:#cbd5e0">—</span>';
            if (r.nextCut) {
                nextStr = `<span class="badge badge-${r.nextCut.cssClass.split('-').pop()}">${r.nextCut.type}</span> <span class="lead-gap">${r.nextCut.time} (-${r.nextGap.toFixed(2)}s)</span>`;
            } else if (r.level === 'AAAA') {
                nextStr = '<span style="color:#16a34a;font-weight:600">All cuts!</span>';
            }
            html += `<tr class="${rankCls}">
                <td class="rank-cell">${i + 1}</td>
                <td><a href="#" class="profile-link" data-mid="${r.member.id}"><strong>${r.member.first_name} ${r.member.last_name}</strong></a></td>
                <td><span class="roster-tag">${r.member.roster || '—'}</span></td>
                <td>${ageStr}</td>
                <td class="lead-time">${r.time}</td>
                <td>${lvlBadge}</td>
                <td>${nextStr}</td>
            </tr>`;
        });
        html += '</tbody></table>';
        board.innerHTML = html;
        wireProfileLinks(board, id => dbState.filtered.find(m => m.id === id));
    }

    // Returns true if the swimmer has any best time in any event of this stroke
    // (for the currently selected course).
    function hasAnyTimeInStroke(m, stroke) {
        const course = dbState.filters.course || 'SCY';
        return (m.best_times || []).some(ev => {
            const ei = normalizeEvent(ev.event);
            return ei && ei.course === course && ei.stroke === stroke;
        });
    }

    // Specialty rank scale (1..7) — extends standard USA_LEVEL_RANK by adding
    // a "<B" band so beginners with a time but no cut still appear on the radar.
    //   <B = 1, B = 2, BB = 3, A = 4, AA = 5, AAA = 6, AAAA = 7
    function specialtyRankFor(m, stroke) {
        if (!m.gender || !m.age) return null;
        const lvl = highestLevelForSwimmer(m, { stroke });
        if (lvl) return (USA_LEVEL_RANK[lvl] || 0) + 1;
        if (hasAnyTimeInStroke(m, stroke)) return 1; // <B
        return null; // no time at all → exclude from average
    }

    function computeTeamSpecialty() {
        const strokes = ['FREE', 'BACK', 'BREAST', 'FLY', 'IM'];
        const result = {};
        strokes.forEach(stroke => {
            const scores = [];
            dbState.filtered.forEach(m => {
                const r = specialtyRankFor(m, stroke);
                if (r != null) scores.push(r);
            });
            if (scores.length === 0) {
                result[stroke] = { avg: 0, count: 0 };
            } else {
                const avg = scores.reduce((a, b) => a + b, 0) / scores.length;
                result[stroke] = { avg, count: scores.length };
            }
        });
        return result;
    }

    // Per-event breakdown for a stroke: coverage, avg level, top swimmer
    function computeStrokeEvents(stroke) {
        const course = dbState.filters.course || 'SCY';
        const cats = (EVENT_CATALOG[course] || []).filter(e => e.stroke === stroke);
        const eligible = dbState.filtered.filter(m => m.gender && m.age);
        return cats.map(cat => {
            let withTime = 0;
            const ranks = [];
            const levelCounts = { B: 0, BB: 0, A: 0, AA: 0, AAA: 0, AAAA: 0, none: 0 };
            let bestRow = null;
            eligible.forEach(m => {
                const found = findBestForCatalog(m, cat);
                if (!found) return;
                withTime++;
                const lvlInfo = levelForSwimmerEvent(m, cat);
                if (lvlInfo && lvlInfo.level) {
                    levelCounts[lvlInfo.level]++;
                    ranks.push(USA_LEVEL_RANK[lvlInfo.level]);
                } else {
                    levelCounts.none++;
                    ranks.push(0);
                }
                const secs = timeToSeconds(found.time);
                if (!bestRow || secs < bestRow.secs) {
                    bestRow = {
                        name: `${m.first_name} ${m.last_name}`,
                        time: found.time, secs,
                        level: lvlInfo ? lvlInfo.level : null,
                    };
                }
            });
            const avgRank = ranks.length ? ranks.reduce((s, x) => s + x, 0) / ranks.length : 0;
            return {
                cat, total: eligible.length, withTime, missing: eligible.length - withTime,
                levelCounts, avgRank, best: bestRow,
            };
        });
    }

    let specialtyChartInst = null;
    const dbExpandedStrokes = new Set();
    let dbRadarMetric = 'avg';

    // Build radar dataset based on the selected metric. All metrics map to a
    // 0-7 scale so the axis ticks (<B / B / BB / A / AA / AAA / AAAA) work
    // uniformly. specialtyRankFor includes the <B band.
    function radarDataForMetric(metric) {
        const strokes = ['FREE', 'BACK', 'BREAST', 'FLY', 'IM'];
        const out = {};
        const SPECIALTY_NAMES = ['', '<B', 'B', 'BB', 'A', 'AA', 'AAA', 'AAAA'];
        strokes.forEach(stroke => {
            const ranks = [];
            dbState.filtered.forEach(m => {
                const r = specialtyRankFor(m, stroke);
                if (r != null) ranks.push(r);
            });
            const n = ranks.length;
            let value = 0, raw = 0, label = 'no data';
            if (n) {
                if (metric === 'avg') {
                    raw = ranks.reduce((s, x) => s + x, 0) / n;
                    value = raw;
                    label = `${raw.toFixed(2)} avg`;
                } else if (metric === 'pct_a') {
                    // A or higher → rank >= 4 in the shifted scale
                    const hits = ranks.filter(r => r >= 4).length;
                    raw = (hits / n) * 100;
                    value = (hits / n) * 7;
                    label = `${hits}/${n} (${raw.toFixed(0)}%)`;
                } else if (metric === 'pct_aa') {
                    const hits = ranks.filter(r => r >= 5).length;
                    raw = (hits / n) * 100;
                    value = (hits / n) * 7;
                    label = `${hits}/${n} (${raw.toFixed(0)}%)`;
                } else if (metric === 'max') {
                    raw = Math.max(...ranks);
                    value = raw;
                    label = raw > 0 ? SPECIALTY_NAMES[raw] : 'none';
                }
            }
            out[stroke] = { value, raw, label, count: n };
        });
        return out;
    }

    function expandStrokeAndScroll(stroke) {
        dbExpandedStrokes.add(stroke);
        renderTeamSpecialty();
        // Defer scroll until DOM is updated
        setTimeout(() => {
            const row = document.querySelector(`.spec-row[data-stroke="${stroke}"]`);
            if (row) row.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }, 30);
    }
    function renderTeamSpecialty() {
        const data = computeTeamSpecialty();
        const strokes = ['FREE', 'BACK', 'BREAST', 'FLY', 'IM'];

        // Bar list
        const stats = document.getElementById('dbSpecialtyStats');
        // Specialty (radar/bar) scale: 1=<B, 2=B, 3=BB, 4=A, 5=AA, 6=AAA, 7=AAAA
        const specialtyNames = { 1: '<B', 2: 'B', 3: 'BB', 4: 'A', 5: 'AA', 6: 'AAA', 7: 'AAAA' };
        // Per-event chip levels still use the standard USA scale (1=B..6=AAAA)
        const usaNames = { 1: 'B', 2: 'BB', 3: 'A', 4: 'AA', 5: 'AAA', 6: 'AAAA' };
        const colors = { FREE: '#2563eb', BACK: '#16a34a', BREAST: '#ca8a04', FLY: '#ea580c', IM: '#7c3aed' };
        stats.innerHTML = strokes.map(s => {
            const d = data[s] || { avg: 0, count: 0 };
            const pct = (d.avg / 7) * 100;
            const hasData = d.count > 0;
            const avgLabel = hasData ? `${d.avg.toFixed(1)} (≈ ${specialtyNames[Math.round(d.avg)] || '<B'})` : 'no data';
            const isOpen = dbExpandedStrokes.has(s);
            const caret = isOpen ? '▾' : '▸';
            let detailHtml = '';
            if (isOpen) {
                const events = computeStrokeEvents(s);
                if (events.length === 0) {
                    detailHtml = `<div class="spec-detail-empty">No events for this stroke in ${dbState.filters.course || 'SCY'}.</div>`;
                } else {
                    detailHtml = '<div class="spec-detail">' + events.map(ev => {
                        const cov = ev.total > 0 ? Math.round((ev.withTime / ev.total) * 100) : 0;
                        const avgLbl = ev.withTime > 0
                            ? `<span class="spec-evt-avg">avg ${ev.avgRank.toFixed(1)} (${usaNames[Math.round(ev.avgRank)] || '—'})</span>`
                            : '<span class="spec-evt-avg spec-evt-nodata">no times</span>';
                        const bestStr = ev.best
                            ? `<span class="spec-evt-best">Best: <strong>${ev.best.name}</strong> ${ev.best.time}${ev.best.level ? ` <span class="badge badge-${ev.best.level.toLowerCase()}">${ev.best.level}</span>` : ''}</span>`
                            : '';
                        const evKey = `${ev.cat.dist}|${ev.cat.stroke}|${ev.cat.label}`;
                        const dist = ['B', 'BB', 'A', 'AA', 'AAA', 'AAAA'].map(L => {
                            const n = ev.levelCounts[L];
                            return n > 0 ? `<button type="button" class="spec-chip badge-${L.toLowerCase()}" data-drill="level" data-level="${L}" data-evt="${evKey}">${L}:${n}</button>` : '';
                        }).join('');
                        const noLvl = ev.levelCounts.none > 0
                            ? `<button type="button" class="spec-chip spec-chip-none" data-drill="below-b" data-evt="${evKey}">below B:${ev.levelCounts.none}</button>` : '';
                        const missingChip = ev.missing > 0
                            ? `<button type="button" class="spec-chip spec-chip-missing" data-drill="missing" data-evt="${evKey}">no time:${ev.missing}</button>` : '';
                        return `<div class="spec-evt-row">
                            <div class="spec-evt-head">
                                <span class="spec-evt-label">${ev.cat.label}</span>
                                <span class="spec-evt-cov">${ev.withTime}/${ev.total} have times (${cov}%)</span>
                                ${avgLbl}
                            </div>
                            <div class="spec-evt-body">
                                <div class="spec-evt-chips">${dist}${noLvl}${missingChip}</div>
                                ${bestStr}
                            </div>
                        </div>`;
                    }).join('') + '</div>';
                }
            }
            return `<div class="spec-row${hasData ? ' has-data' : ''}${isOpen ? ' is-open' : ''}" data-stroke="${s}" style="color:${colors[s]}">
                <div class="spec-row-head">
                    <span class="spec-caret">${caret}</span>
                    <span class="spec-stroke stroke-${s}">${s}</span>
                    <div class="spec-bar-wrap"><div class="spec-bar" style="width:${pct}%;background:${colors[s]}"></div></div>
                    <span class="spec-value" style="color:#1e293b">${avgLabel}</span>
                </div>
                ${detailHtml}
            </div>`;
        }).join('');
        // Wire row clicks to toggle expansion
        stats.querySelectorAll('.spec-row').forEach(row => {
            const head = row.querySelector('.spec-row-head');
            head.addEventListener('click', () => {
                const s = row.getAttribute('data-stroke');
                if (dbExpandedStrokes.has(s)) dbExpandedStrokes.delete(s);
                else dbExpandedStrokes.add(s);
                renderTeamSpecialty();
            });
        });
        // Wire chip clicks to open drill-down
        stats.querySelectorAll('.spec-chip[data-drill]').forEach(chip => {
            chip.addEventListener('click', (e) => {
                e.stopPropagation();
                const drill = chip.getAttribute('data-drill');
                const evKey = chip.getAttribute('data-evt');
                const [dist, stroke, label] = evKey.split('|');
                const cat = { dist, stroke, label };
                const level = chip.getAttribute('data-level');
                openDrillModal({ kind: drill, cat, level });
            });
        });

        // Radar
        const ctx = document.getElementById('dbSpecialtyChart');
        if (specialtyChartInst) specialtyChartInst.destroy();

        const metricData = radarDataForMetric(dbRadarMetric);
        const metricTitle = {
            avg: 'Average USA Level',
            pct_a: '% of swimmers at A or higher',
            pct_aa: '% of swimmers at AA or higher',
            max: 'Highest level achieved (any swimmer)',
        }[dbRadarMetric];

        specialtyChartInst = new Chart(ctx, {
            type: 'radar',
            data: {
                labels: strokes,
                datasets: [{
                    label: metricTitle,
                    data: strokes.map(s => metricData[s].value),
                    backgroundColor: 'rgba(0,85,164,0.18)',
                    borderColor: '#0055a4',
                    borderWidth: 2.5,
                    pointBackgroundColor: '#0055a4',
                    pointRadius: 6,
                    pointHoverRadius: 9,
                    pointHoverBackgroundColor: '#fff',
                    pointHoverBorderColor: '#0055a4',
                    pointHoverBorderWidth: 3,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                onClick: (evt, elements, chart) => {
                    // Click on a point → expand that stroke
                    const points = chart.getElementsAtEventForMode(evt, 'nearest', { intersect: false }, true);
                    if (points.length) {
                        const idx = points[0].index;
                        expandStrokeAndScroll(strokes[idx]);
                        return;
                    }
                    // Click anywhere else: detect which axis label was nearest by angle
                    const rect = chart.canvas.getBoundingClientRect();
                    const x = evt.x - rect.left - rect.width / 2;
                    const y = evt.y - rect.top - rect.height / 2;
                    let angle = Math.atan2(x, -y) * 180 / Math.PI;
                    if (angle < 0) angle += 360;
                    const segment = 360 / strokes.length;
                    const idx = Math.round(angle / segment) % strokes.length;
                    expandStrokeAndScroll(strokes[idx]);
                },
                onHover: (e, els) => {
                    e.native.target.style.cursor = els.length ? 'pointer' : 'pointer';
                },
                plugins: {
                    legend: { display: false },
                    title: {
                        display: true,
                        text: metricTitle,
                        color: '#003366',
                        font: { size: 13, weight: '700' },
                        padding: { top: 4, bottom: 8 },
                    },
                    tooltip: {
                        backgroundColor: 'rgba(15,23,42,0.95)',
                        padding: 10,
                        titleFont: { size: 13, weight: '700' },
                        bodyFont: { size: 12 },
                        callbacks: {
                            title: items => items[0].label,
                            label: item => {
                                const s = strokes[item.dataIndex];
                                const d = metricData[s];
                                return [`${d.label}`, `${d.count} eligible swimmer${d.count === 1 ? '' : 's'}`];
                            },
                            afterBody: () => 'Click to drill into this stroke',
                        },
                    },
                },
                scales: {
                    r: {
                        suggestedMin: 0, suggestedMax: 7,
                        ticks: {
                            stepSize: 1,
                            color: '#94a3b8',
                            backdropColor: 'rgba(255,255,255,0.85)',
                            callback: v => {
                                if (dbRadarMetric === 'avg' || dbRadarMetric === 'max') {
                                    return specialtyNames[v] || (v === 0 ? '' : v);
                                }
                                // For percentage metrics, show 0/25/50/75/100
                                return v === 0 ? '0%' : `${Math.round((v / 7) * 100)}%`;
                            },
                        },
                        grid: { color: 'rgba(100,116,139,0.2)' },
                        angleLines: { color: 'rgba(100,116,139,0.25)' },
                        pointLabels: {
                            font: { size: 14, weight: '700' },
                            color: '#003366',
                        },
                    },
                },
            },
        });
    }

    // ===== Drill-down modal (event/level swimmer detail) =====
    const dbDrillModal = document.getElementById('dbDrillModal');
    const dbDrillTitle = document.getElementById('dbDrillTitle');
    const dbDrillBody = document.getElementById('dbDrillBody');
    function closeDrillModal() { if (dbDrillModal) dbDrillModal.classList.add('hidden'); }
    if (dbDrillModal) {
        document.getElementById('dbDrillClose').addEventListener('click', closeDrillModal);
        document.getElementById('dbDrillDone').addEventListener('click', closeDrillModal);
        dbDrillModal.addEventListener('click', (e) => {
            if (e.target === dbDrillModal) closeDrillModal();
        });
    }

    function openDrillModal({ kind, cat, level }) {
        if (!dbDrillModal) return;
        const course = dbState.filters.course || 'SCY';
        const eligible = dbState.filtered.filter(m => m.gender && m.age);
        const rows = [];

        if (kind === 'missing') {
            eligible.forEach(m => {
                if (!findBestForCatalog(m, cat)) {
                    rows.push({ member: m, time: null, level: null, gap: null, nextCut: null });
                }
            });
            rows.sort((a, b) => (a.member.last_name || '').localeCompare(b.member.last_name || ''));
            dbDrillTitle.textContent = `${cat.label} (${course}) — no recorded time`;
        } else {
            eligible.forEach(m => {
                const found = findBestForCatalog(m, cat);
                if (!found) return;
                const lvlInfo = levelForSwimmerEvent(m, cat);
                const swSecs = timeToSeconds(found.time);
                const ag = ageToAgeGroup(m.age);
                const std = lookupStandards(found.eventInfo, ag, m.gender);
                const cmp = compareToStandards(swSecs, std);
                const r = lvlInfo && lvlInfo.level ? (USA_LEVEL_RANK[lvlInfo.level] || 0) : 0;

                let include = false;
                if (kind === 'level' && lvlInfo && lvlInfo.level === level) include = true;
                else if (kind === 'below-b' && (!lvlInfo || !lvlInfo.level)) include = true;
                if (!include) return;

                const nextCut = cmp.usaNext;
                const gap = nextCut ? swSecs - timeToSeconds(nextCut.time) : null;
                rows.push({
                    member: m, time: found.time, secs: swSecs,
                    level: lvlInfo ? lvlInfo.level : null,
                    nextCut, gap, rank: r,
                });
            });
            rows.sort((a, b) => (a.secs || 0) - (b.secs || 0));
            const titleSuffix = kind === 'level' ? `${level} swimmers` : 'below B (have time, no cut yet)';
            dbDrillTitle.textContent = `${cat.label} (${course}) — ${titleSuffix}`;
        }

        if (rows.length === 0) {
            dbDrillBody.innerHTML = '<p style="color:#94a3b8">No swimmers match.</p>';
        } else {
            let html = '<table class="drill-table"><thead><tr><th>Swimmer</th><th>Group</th><th>Age</th><th>Time</th><th>Level</th><th>Next Cut</th></tr></thead><tbody>';
            rows.forEach(r => {
                const m = r.member;
                const ageStr = m.age != null ? m.age : '—';
                const timeStr = r.time || '<span style="color:#cbd5e0">—</span>';
                const lvlBadge = r.level
                    ? `<span class="badge badge-${r.level.toLowerCase()}">${r.level}</span>`
                    : '<span style="color:#cbd5e0">—</span>';
                let nextStr = '<span style="color:#cbd5e0">—</span>';
                if (r.nextCut) {
                    nextStr = `<span class="badge badge-${r.nextCut.type.toLowerCase()}">${r.nextCut.type}</span> <span class="drill-gap">${r.nextCut.time} (-${r.gap.toFixed(2)}s)</span>`;
                } else if (r.level === 'AAAA') {
                    nextStr = '<span style="color:#16a34a;font-weight:600">All cuts!</span>';
                }
                html += `<tr>
                    <td><a href="#" class="profile-link" data-mid="${m.id}"><strong>${m.first_name} ${m.last_name}</strong></a></td>
                    <td><span class="roster-tag">${m.roster || '—'}</span></td>
                    <td>${ageStr}</td>
                    <td class="drill-time">${timeStr}</td>
                    <td>${lvlBadge}</td>
                    <td>${nextStr}</td>
                </tr>`;
            });
            html += '</tbody></table>';
            html += `<div class="drill-summary">${rows.length} swimmer${rows.length === 1 ? '' : 's'}</div>`;
            dbDrillBody.innerHTML = html;
            wireProfileLinks(dbDrillBody, id => dbState.filtered.find(m => m.id === id));
        }
        dbDrillModal.classList.remove('hidden');
    }

    // ===== Action Items =====
    // Surface concrete recommendations a coach can act on this week.
    function computeActionItems() {
        const items = [];
        const strokes = ['FREE', 'BACK', 'BREAST', 'FLY', 'IM'];
        const course = dbState.filters.course || 'SCY';
        const eligible = dbState.filtered.filter(m => m.gender && m.age);
        if (eligible.length < 2) return items;

        const data = computeTeamSpecialty();
        const withData = strokes.filter(s => data[s].count > 0);

        // 1) Weakest stroke — focus area for training
        if (withData.length) {
            const weakest = withData.reduce((a, b) => data[a].avg < data[b].avg ? a : b);
            const subBB = eligible.filter(m => {
                const lvl = highestLevelForSwimmer(m, { stroke: weakest });
                return !lvl || USA_LEVEL_RANK[lvl] < 2;
            });
            const events = computeStrokeEvents(weakest);
            const worstEv = events
                .filter(e => e.withTime > 0)
                .sort((a, b) => a.avgRank - b.avgRank)[0];
            const detailEv = worstEv ? ` Lowest event: ${worstEv.cat.label}.` : '';
            items.push({
                tag: 'FOCUS', tone: 'warn',
                title: `Weakest stroke: ${weakest}`,
                detail: `${subBB.length} of ${eligible.length} swimmers below BB level here.${detailEv} Add focused ${weakest.toLowerCase()} sets and drill work.`,
            });
        }

        // 2) Strongest stroke — celebrate + double down
        if (withData.length) {
            const strongest = withData.reduce((a, b) => data[a].avg > data[b].avg ? a : b);
            if (strongest !== (withData[0] && withData.length > 1 ? null : null) && data[strongest].avg >= 3) {
                items.push({
                    tag: 'STRENGTH', tone: 'good',
                    title: `Strongest stroke: ${strongest}`,
                    detail: `Avg ≈ ${data[strongest].avg.toFixed(1)}. Lean into ${strongest.toLowerCase()} for relays and championship lineups.`,
                });
            }
        }

        // 3) Coverage gap — events that need a baseline time trial
        const allEvents = EVENT_CATALOG[course] || [];
        let biggestGap = null;
        allEvents.forEach(cat => {
            const missing = eligible.filter(m => !findBestForCatalog(m, cat)).length;
            if (!biggestGap || missing > biggestGap.missing) biggestGap = { cat, missing };
        });
        if (biggestGap && biggestGap.missing >= Math.max(3, Math.floor(eligible.length * 0.4))) {
            items.push({
                tag: 'GAP', tone: 'info',
                title: `Coverage gap: ${biggestGap.cat.label}`,
                detail: `${biggestGap.missing} of ${eligible.length} swimmers have no recorded ${course} time. Run a time trial to set baselines.`,
            });
        }

        // 4) Quick wins — within 2s of next USA cut
        let quickWins = 0;
        const winsByEvent = {};
        const winNames = [];
        eligible.forEach(m => {
            const ag = ageToAgeGroup(m.age);
            (m.best_times || []).forEach(ev => {
                const ei = normalizeEvent(ev.event);
                if (!ei || ei.course !== course) return;
                const std = lookupStandards(ei, ag, m.gender);
                const cmp = compareToStandards(timeToSeconds(ev.time), std);
                if (cmp.usaNext) {
                    const gap = timeToSeconds(ev.time) - timeToSeconds(cmp.usaNext.time);
                    if (gap > 0 && gap <= 2) {
                        quickWins++;
                        winsByEvent[ev.event] = (winsByEvent[ev.event] || 0) + 1;
                        winNames.push(`${m.first_name} ${m.last_name}`);
                    }
                }
            });
        });
        if (quickWins > 0) {
            const top = Object.entries(winsByEvent).sort((a, b) => b[1] - a[1])[0];
            items.push({
                tag: 'WIN', tone: 'good',
                title: `${quickWins} quick win${quickWins === 1 ? '' : 's'} possible`,
                detail: `Within 2s of next USA cut. Top opportunity: ${top[0]} (${top[1]} swimmer${top[1] === 1 ? '' : 's'}). Taper or race them next meet.`,
            });
        }

        // 5) Untapped potential — strong in a stroke but missing a key event
        const candidatesByEvent = {};
        let hiddenCount = 0;
        eligible.forEach(m => {
            strokes.forEach(stroke => {
                const lvl = highestLevelForSwimmer(m, { stroke });
                if (lvl && USA_LEVEL_RANK[lvl] >= 3) {
                    const strokeEvents = (EVENT_CATALOG[course] || []).filter(e => e.stroke === stroke);
                    strokeEvents.forEach(cat => {
                        if (!findBestForCatalog(m, cat)) {
                            hiddenCount++;
                            candidatesByEvent[cat.label] = (candidatesByEvent[cat.label] || 0) + 1;
                        }
                    });
                }
            });
        });
        if (hiddenCount > 0) {
            const top = Object.entries(candidatesByEvent).sort((a, b) => b[1] - a[1])[0];
            items.push({
                tag: 'TARGET', tone: 'info',
                title: `${hiddenCount} untapped event entries`,
                detail: `Swimmers at A-or-above in a stroke who haven't raced every event of it. Top: ${top[0]} (${top[1]} candidates). Try them at the next dual meet.`,
            });
        }

        // 6) IM development gap — Free strong but IM weak
        if (data.FREE.count > 0 && data.IM.count > 0 && data.FREE.avg - data.IM.avg >= 1.5) {
            items.push({
                tag: 'IM', tone: 'warn',
                title: 'IM lags behind Free',
                detail: `Free avg ${data.FREE.avg.toFixed(1)} vs IM ${data.IM.avg.toFixed(1)}. IM scores reward all-around development — add IM sets and stroke-transition drills.`,
            });
        }

        // 7) Missing data nudge
        const missingGenderOrAge = dbState.filtered.filter(m => !m.gender || !m.age).length;
        if (missingGenderOrAge > 0) {
            items.push({
                tag: 'DATA', tone: 'info',
                title: `${missingGenderOrAge} swimmer${missingGenderOrAge === 1 ? '' : 's'} missing gender or DOB`,
                detail: 'Levels can\'t be computed without both. Update in the Roster tab to include them in dashboard insights.',
            });
        }

        return items;
    }

    function renderActionItems() {
        const el = document.getElementById('dbActionItems');
        if (!el) return;
        const items = computeActionItems();
        if (items.length === 0) {
            el.innerHTML = '<div class="action-empty">Add gender + DOB and at least one CT Swim time to see recommendations.</div>';
            return;
        }
        el.innerHTML = items.map(it => `
            <div class="action-card action-${it.tone}">
                <div class="action-tag">${it.tag}</div>
                <div class="action-body">
                    <div class="action-title">${it.title}</div>
                    <div class="action-detail">${it.detail}</div>
                </div>
            </div>
        `).join('');
    }

    function renderClosestToCut() {
        const els = document.getElementById('dbClosestCards');
        const items = [];
        const course = dbState.filters.course;
        const strokeFilter = dbState.filters.stroke || null;

        dbState.filtered.forEach(m => {
            if (!m.ct_id || !m.gender || !m.age) return;
            const ag = ageToAgeGroup(m.age);
            (m.best_times || []).forEach(ev => {
                const ei = normalizeEvent(ev.event);
                if (!ei || ei.course !== course) return;
                if (strokeFilter && ei.stroke !== strokeFilter) return;
                const std = lookupStandards(ei, ag, m.gender);
                const swSecs = timeToSeconds(ev.time);
                const cmp = compareToStandards(swSecs, std);
                if (cmp.usaNext) {
                    const gap = swSecs - timeToSeconds(cmp.usaNext.time);
                    if (gap > 0 && gap < 5) {
                        items.push({
                            member: m, event: ev.event, current: ev.time,
                            target: cmp.usaNext, gap,
                        });
                    }
                }
            });
        });

        items.sort((a, b) => a.gap - b.gap);
        const top = items.slice(0, 8);

        if (top.length === 0) {
            els.innerHTML = '<div style="padding:1rem;color:#94a3b8;grid-column:1/-1">No swimmers within 5 seconds of next cut for the current filter.</div>';
            return;
        }
        const colors = { b:'#ea580c', bb:'#ca8a04', a:'#16a34a', aa:'#0891b2', aaa:'#2563eb', aaaa:'#7c3aed' };
        els.innerHTML = top.map(it => {
            const gapClass = it.gap <= 1 ? 'very-close' : it.gap <= 3 ? 'close' : '';
            const barCls = it.target.cssClass.replace('badge-', '');
            const pct = Math.max(20, Math.min(100, (1 - it.gap / 5) * 100));
            return `<div class="closest-card">
                <div class="cc-event">${it.member.first_name} ${it.member.last_name} <span style="color:#64748b;font-weight:500">· ${it.event}</span></div>
                <div class="cc-times">
                    <span class="cc-current">${it.current}</span>
                    <span class="cc-target"><span class="badge ${it.target.cssClass}">${it.target.type}</span> ${it.target.time}</span>
                </div>
                <div class="cc-gap ${gapClass}">-${it.gap.toFixed(2)}s to go</div>
                <div class="cc-progress"><div class="cc-progress-bar" style="width:${pct}%;background:${colors[barCls] || '#64748b'}"></div></div>
            </div>`;
        }).join('');
    }

    function renderChampSpotlight() {
        const els = document.getElementById('dbChampCards');
        const champTypes = [
            { type: 'CT AG', title: 'CT Age Group Champs', cls: 'badge-ct' },
            { type: 'EZ',    title: 'Eastern Zone Champs', cls: 'badge-ez' },
        ];
        const course = dbState.filters.course;
        const strokeFilter = dbState.filters.stroke || null;

        let html = '';
        champTypes.forEach(({ type, title, cls }) => {
            const qualified = [];     // [{member, events: [...]}]
            const close = [];

            dbState.filtered.forEach(m => {
                if (!m.ct_id || !m.gender || !m.age) return;
                const ag = ageToAgeGroup(m.age);
                let qualEvents = [], closeEvents = [];
                (m.best_times || []).forEach(ev => {
                    const ei = normalizeEvent(ev.event);
                    if (!ei || ei.course !== course) return;
                    if (strokeFilter && ei.stroke !== strokeFilter) return;
                    const std = lookupStandards(ei, ag, m.gender);
                    const swSecs = timeToSeconds(ev.time);
                    std.champ.forEach(c => {
                        if (c.type !== type) return;
                        const stdSecs = timeToSeconds(c.time);
                        const gap = swSecs - stdSecs;
                        if (gap <= 0) qualEvents.push(ev.event);
                        else if (gap <= 2) closeEvents.push({ event: ev.event, gap });
                    });
                });
                if (qualEvents.length > 0) qualified.push({ member: m, events: qualEvents });
                else if (closeEvents.length > 0) close.push({ member: m, events: closeEvents });
            });

            const total = qualified.length + close.length;
            if (total === 0) return;
            const statusBadge = qualified.length > 0
                ? `<span class="champ-status yes">${qualified.length} Qualified</span>`
                : `<span class="champ-status partial">${close.length} Close</span>`;
            const statusClass = qualified.length > 0 ? 'qualified' : 'not-qualified';

            html += `<div class="champ-card ${statusClass}">
                <div class="champ-card-header">
                    <span class="champ-card-title"><span class="badge ${cls}">${type}</span> ${title}</span>
                    ${statusBadge}
                </div>`;

            if (qualified.length > 0) {
                html += '<div class="champ-qualified-count">Qualified Swimmers</div><div class="champ-events">';
                qualified.forEach(q => {
                    html += `<span class="champ-event-chip qual">${q.member.first_name} ${q.member.last_name} (${q.events.length})</span>`;
                });
                html += '</div>';
            }
            if (close.length > 0) {
                html += '<div class="champ-qualified-count" style="margin-top:0.5rem">Within 2 seconds</div><div class="champ-events">';
                close.forEach(c => {
                    const minGap = Math.min(...c.events.map(e => e.gap));
                    html += `<span class="champ-event-chip close">${c.member.first_name} ${c.member.last_name} (-${minGap.toFixed(1)}s)</span>`;
                });
                html += '</div>';
            }
            html += '</div>';
        });

        if (!html) {
            html = '<div style="padding:1rem;color:#94a3b8;grid-column:1/-1">No qualifying or close swimmers for the current filter. Adjust filters or run "Refresh from CT Swim" in Admin to fetch latest times.</div>';
        }
        els.innerHTML = html;
    }

    // Filter listeners
    [dbAge, dbGender, dbRoster, dbStroke, dbCourse].forEach(el => {
        el.addEventListener('change', () => {
            // When course or stroke changes, the available distances change too
            dbState.matrix.distances = null;
            applyFilters();
        });
    });
    document.getElementById('dbLeaderEvent').addEventListener('change', renderEventLeaderboard);
    document.getElementById('dbReloadBtn').addEventListener('click', loadDashboard);

    // USA Distribution local controls
    const dbDistCourseEl = document.getElementById('dbDistCourse');
    const dbDistEventEl = document.getElementById('dbDistEvent');
    if (dbDistCourseEl) {
        dbDistCourseEl.addEventListener('change', () => {
            dbDistState.course = dbDistCourseEl.value;
            // Reset selected event to first one when course changes
            dbDistState.evtIdx = 0;
            renderUsaDistribution();
        });
    }
    if (dbDistEventEl) {
        dbDistEventEl.addEventListener('change', () => {
            dbDistState.evtIdx = parseInt(dbDistEventEl.value || '0');
            renderUsaDistribution();
        });
    }

    // Radar metric pills
    const dbRadarMetricEl = document.getElementById('dbRadarMetric');
    if (dbRadarMetricEl) {
        dbRadarMetricEl.addEventListener('click', (e) => {
            const btn = e.target.closest('.radar-pill');
            if (!btn) return;
            dbRadarMetric = btn.getAttribute('data-metric');
            dbRadarMetricEl.querySelectorAll('.radar-pill').forEach(p => {
                p.classList.toggle('on', p === btn);
            });
            renderTeamSpecialty();
        });
    }

    // Matrix sub-controls
    if (dbMatrixDistPills) {
        dbMatrixDistPills.addEventListener('click', (e) => {
            const btn = e.target.closest('button.mc-pill');
            if (!btn) return;
            const d = btn.getAttribute('data-dist');
            const allDists = [...new Set(getColumnEvents().map(c => c.dist))];
            if (d === '__all') dbState.matrix.distances = new Set(allDists);
            else if (d === '__none') dbState.matrix.distances = new Set();
            else {
                if (dbState.matrix.distances.has(d)) dbState.matrix.distances.delete(d);
                else dbState.matrix.distances.add(d);
            }
            renderEventMatrix();
        });
    }
    if (dbMatrixLevelFilter) {
        dbMatrixLevelFilter.addEventListener('change', () => {
            dbState.matrix.levelFilter = dbMatrixLevelFilter.value;
            renderEventMatrix();
        });
    }
    if (dbMatrixCompact) {
        dbMatrixCompact.addEventListener('change', () => {
            dbState.matrix.compact = dbMatrixCompact.checked;
            renderEventMatrix();
        });
    }
    if (dbMatrixHideEmpty) {
        dbMatrixHideEmpty.addEventListener('change', () => {
            dbState.matrix.hideEmpty = dbMatrixHideEmpty.checked;
            renderEventMatrix();
        });
    }

    // Lazy load when tab clicked
    document.querySelector('[data-tab="dashboard"]').addEventListener('click', () => {
        if (dbState.members.length === 0) loadDashboard();
    });
}


// Helper to attach profile-link click handlers within a container.
function wireProfileLinks(container, lookup) {
    if (!container || !window.openSwimmerProfile) return;
    container.querySelectorAll('a.profile-link').forEach(el => {
        el.addEventListener('click', e => {
            e.preventDefault();
            const id = parseInt(el.dataset.mid);
            const m = lookup(id);
            if (m) window.openSwimmerProfile(m);
        });
    });
}


// ===== SWIMMER PROFILE MODAL (radar + strengths + targets) =====
// Globally accessible — used by the dashboard, roster, and parent view.
(function () {
    const STROKES = ['FREE', 'BACK', 'BREAST', 'FLY', 'IM'];
    const LEVEL_RANK = { B: 1, BB: 2, A: 3, AA: 4, AAA: 5, AAAA: 6 };
    const LEVEL_NAMES = { 1: 'B', 2: 'BB', 3: 'A', 4: 'AA', 5: 'AAA', 6: 'AAAA' };
    const SP_EVENTS = {
        SCY: [
            { dist: '50', stroke: 'FREE',  label: '50 Free' },
            { dist: '100', stroke: 'FREE', label: '100 Free' },
            { dist: '200', stroke: 'FREE', label: '200 Free' },
            { dist: '500', stroke: 'FREE', label: '500 Free' },
            { dist: '1000', stroke: 'FREE', label: '1000 Free' },
            { dist: '1650', stroke: 'FREE', label: '1650 Free' },
            { dist: '50', stroke: 'BACK',  label: '50 Back' },
            { dist: '100', stroke: 'BACK', label: '100 Back' },
            { dist: '200', stroke: 'BACK', label: '200 Back' },
            { dist: '50', stroke: 'BREAST', label: '50 Breast' },
            { dist: '100', stroke: 'BREAST', label: '100 Breast' },
            { dist: '200', stroke: 'BREAST', label: '200 Breast' },
            { dist: '50', stroke: 'FLY', label: '50 Fly' },
            { dist: '100', stroke: 'FLY', label: '100 Fly' },
            { dist: '200', stroke: 'FLY', label: '200 Fly' },
            { dist: '100', stroke: 'IM', label: '100 IM' },
            { dist: '200', stroke: 'IM', label: '200 IM' },
            { dist: '400', stroke: 'IM', label: '400 IM' },
        ],
        LCM: [
            { dist: '50', stroke: 'FREE', label: '50 Free' },
            { dist: '100', stroke: 'FREE', label: '100 Free' },
            { dist: '200', stroke: 'FREE', label: '200 Free' },
            { dist: '400', stroke: 'FREE', label: '400 Free' },
            { dist: '800', stroke: 'FREE', label: '800 Free' },
            { dist: '1500', stroke: 'FREE', label: '1500 Free' },
            { dist: '50', stroke: 'BACK', label: '50 Back' },
            { dist: '100', stroke: 'BACK', label: '100 Back' },
            { dist: '200', stroke: 'BACK', label: '200 Back' },
            { dist: '50', stroke: 'BREAST', label: '50 Breast' },
            { dist: '100', stroke: 'BREAST', label: '100 Breast' },
            { dist: '200', stroke: 'BREAST', label: '200 Breast' },
            { dist: '50', stroke: 'FLY', label: '50 Fly' },
            { dist: '100', stroke: 'FLY', label: '100 Fly' },
            { dist: '200', stroke: 'FLY', label: '200 Fly' },
            { dist: '200', stroke: 'IM', label: '200 IM' },
            { dist: '400', stroke: 'IM', label: '400 IM' },
        ],
    };

    const modal = document.getElementById('swimmerProfileModal');
    if (!modal) return; // page didn't include the modal markup
    const elName = document.getElementById('spName');
    const elMeta = document.getElementById('spMeta');
    const elStrengths = document.getElementById('spStrengths');
    const elTargets = document.getElementById('spTargets');
    const elBest = document.getElementById('spBestTimes');
    const radarCanvas = document.getElementById('spRadar');
    const radarEmpty = document.getElementById('spRadarEmpty');
    const elProgEvent = document.getElementById('spProgressEvent');
    const elProgLoading = document.getElementById('spProgressLoading');
    const elProgStats = document.getElementById('spProgressStats');
    const elProgChartWrap = document.getElementById('spProgressChartWrap');
    const elProgChart = document.getElementById('spProgressChart');
    const elProgHistory = document.getElementById('spProgressHistory');
    let radarChart = null;
    let progressChart = null;
    let currentMember = null;
    let currentCourse = 'SCY';

    if (elProgEvent) elProgEvent.addEventListener('change', loadProgress);

    document.getElementById('spClose').addEventListener('click', closeProfile);
    document.getElementById('spDone').addEventListener('click', closeProfile);
    const spPdfBtn = document.getElementById('spDownloadPdf');
    if (spPdfBtn) spPdfBtn.addEventListener('click', () => {
        if (currentMember) generatePDFForMember(currentMember);
    });
    modal.addEventListener('click', e => { if (e.target === modal) closeProfile(); });
    document.querySelectorAll('.sp-course-pill').forEach(btn => {
        btn.addEventListener('click', () => {
            currentCourse = btn.getAttribute('data-course');
            document.querySelectorAll('.sp-course-pill').forEach(b => {
                b.classList.toggle('on', b === btn);
            });
            renderProfile();
        });
    });

    function closeProfile() {
        modal.classList.add('hidden');
        if (radarChart) { radarChart.destroy(); radarChart = null; }
        if (progressChart) { progressChart.destroy(); progressChart = null; }
    }

    function populateProgressDropdown() {
        if (!elProgEvent || !currentMember) return;
        const opts = (currentMember.best_times || [])
            .filter(ev => ev.history_url)
            .map(ev => `<option value="${encodeURIComponent(ev.history_url)}|${encodeURIComponent(ev.event)}">${ev.event}</option>`);
        elProgEvent.innerHTML = '<option value="">— Select an event —</option>' + opts.join('');
        // Reset visual state
        elProgStats.innerHTML = '';
        elProgHistory.innerHTML = '';
        elProgChartWrap.classList.add('hidden');
        if (progressChart) { progressChart.destroy(); progressChart = null; }
    }

    async function loadProgress() {
        if (!elProgEvent) return;
        const val = elProgEvent.value;
        if (!val) {
            elProgStats.innerHTML = '';
            elProgHistory.innerHTML = '';
            elProgChartWrap.classList.add('hidden');
            return;
        }
        const [encUrl, encName] = val.split('|');
        const url = decodeURIComponent(encUrl);
        const eventName = decodeURIComponent(encName);
        elProgLoading.classList.remove('hidden');
        try {
            const r = await fetch('/api/event_history', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    history_url: url,
                    swimmer_id: currentMember.ct_id,
                    event_name: eventName,
                }),
            });
            const d = await r.json();
            elProgLoading.classList.add('hidden');
            if (d.error || !d.history?.length) {
                elProgStats.innerHTML = '';
                elProgHistory.innerHTML = '<p style="padding:0.5rem;color:#94a3b8">No history found.</p>';
                elProgChartWrap.classList.add('hidden');
                return;
            }
            renderProgressStats(d.history);
            renderProgressChart(d.history);
            renderProgressHistory(d.history);
        } catch (e) {
            elProgLoading.classList.add('hidden');
            elProgHistory.innerHTML = '<p style="padding:0.5rem;color:#dc2626">Failed to load history.</p>';
            elProgChartWrap.classList.add('hidden');
        }
    }

    function renderProgressStats(history) {
        if (history.length < 2) { elProgStats.innerHTML = ''; return; }
        const chrono = [...history].reverse();
        const first = timeToSeconds(chrono[0].time);
        const latest = timeToSeconds(chrono[chrono.length - 1].time);
        const best = Math.min(...chrono.map(h => timeToSeconds(h.time)));
        const dropFromFirst = first - latest;
        const isImprove = dropFromFirst > 0;
        const dropClass = dropFromFirst === 0 ? '' : (isImprove ? 'improve' : 'worse');
        elProgStats.innerHTML = `
            <div class="sp-stat"><span class="lbl">Swims</span><span class="val">${history.length}</span></div>
            <div class="sp-stat"><span class="lbl">First</span><span class="val">${chrono[0].time}</span></div>
            <div class="sp-stat"><span class="lbl">Latest</span><span class="val">${chrono[chrono.length - 1].time}</span></div>
            <div class="sp-stat"><span class="lbl">Best</span><span class="val">${secondsToTime(best)}</span></div>
            <div class="sp-stat ${dropClass}">
                <span class="lbl">${isImprove ? 'Drop' : 'Add'}</span>
                <span class="val">${isImprove ? '-' : '+'}${Math.abs(dropFromFirst).toFixed(2)}s</span>
            </div>
        `;
    }

    function renderProgressChart(history) {
        if (!elProgChart) return;
        elProgChartWrap.classList.remove('hidden');
        if (progressChart) { progressChart.destroy(); progressChart = null; }
        const chrono = [...history].reverse();
        progressChart = new Chart(elProgChart, {
            type: 'line',
            data: {
                labels: chrono.map(h => h.date),
                datasets: [{
                    label: 'Time',
                    data: chrono.map(h => timeToSeconds(h.time)),
                    borderColor: '#0055a4',
                    backgroundColor: 'rgba(0,85,164,0.1)',
                    borderWidth: 2.5,
                    pointBackgroundColor: '#0055a4',
                    pointRadius: 4,
                    pointHoverRadius: 6,
                    fill: true, tension: 0.3,
                }],
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { label: ctx => secondsToTime(ctx.parsed.y) } },
                },
                scales: {
                    y: {
                        reverse: true,
                        ticks: { callback: v => secondsToTime(v) },
                        title: { display: true, text: 'Time (lower is faster)', font: { size: 10 } },
                    },
                    x: { ticks: { font: { size: 10 } } },
                },
            },
        });
    }

    function renderProgressHistory(history) {
        const chrono = [...history].reverse();
        const bestSecs = Math.min(...chrono.map(h => timeToSeconds(h.time)));
        let html = '<table><thead><tr><th>Date</th><th>Time</th><th>Meet</th></tr></thead><tbody>';
        // Newest-first reads better
        history.forEach(h => {
            const isBest = timeToSeconds(h.time) === bestSecs;
            html += `<tr>
                <td>${h.date || '—'}</td>
                <td class="${isBest ? 'tbest' : ''}">${h.time}${isBest ? ' ★' : ''}</td>
                <td>${h.meet || '—'}</td>
            </tr>`;
        });
        html += '</tbody></table>';
        elProgHistory.innerHTML = html;
    }

    function ageGroupOf(age) {
        if (age == null) return null;
        if (age <= 10) return '10/Under';
        if (age <= 12) return '11/12';
        if (age <= 14) return '13/14';
        if (age <= 16) return '15/16';
        return '17/18';
    }

    function findBest(member, cat) {
        for (const ev of (member.best_times || [])) {
            const ei = normalizeEvent(ev.event);
            if (!ei || ei.course !== currentCourse) continue;
            if (ei.distance === cat.dist && ei.stroke === cat.stroke) {
                return { time: ev.time, eventInfo: ei };
            }
        }
        return null;
    }

    function levelForEvent(member, cat) {
        if (!member.gender || !member.age) return null;
        const found = findBest(member, cat);
        if (!found) return null;
        const ag = ageGroupOf(member.age);
        const std = lookupStandards(found.eventInfo, ag, member.gender);
        const swSecs = timeToSeconds(found.time);
        let bestRank = 0, bestType = null;
        std.usa.forEach(s => {
            if (swSecs <= timeToSeconds(s.time)) {
                const r = LEVEL_RANK[s.type] || 0;
                if (r > bestRank) { bestRank = r; bestType = s.type; }
            }
        });
        const cmp = compareToStandards(swSecs, std);
        return {
            time: found.time, secs: swSecs, level: bestType, rank: bestRank,
            nextCut: cmp.usaNext,
            gap: cmp.usaNext ? swSecs - timeToSeconds(cmp.usaNext.time) : null,
        };
    }

    function highestPerStroke(member) {
        const out = {};
        STROKES.forEach(stroke => {
            const events = SP_EVENTS[currentCourse].filter(e => e.stroke === stroke);
            let bestRank = 0;
            events.forEach(cat => {
                const lv = levelForEvent(member, cat);
                if (lv && lv.rank > bestRank) bestRank = lv.rank;
            });
            out[stroke] = bestRank;
        });
        return out;
    }

    function renderProfile() {
        if (!currentMember) return;
        const m = currentMember;
        const events = SP_EVENTS[currentCourse];

        // Compute all event-level stats once
        const evtStats = events.map(cat => ({ cat, info: levelForEvent(m, cat) }));

        // Strengths: top 3 by rank (only events with a level)
        const strengths = evtStats
            .filter(e => e.info && e.info.level)
            .sort((a, b) => b.info.rank - a.info.rank || a.info.secs - b.info.secs)
            .slice(0, 3);

        elStrengths.innerHTML = strengths.length === 0
            ? '<div class="sp-empty-list">No USA cuts hit yet in this course.</div>'
            : strengths.map(e => `
                <div class="sp-row">
                    <span class="sp-evt">${e.cat.label}</span>
                    <span class="sp-time">${e.info.time}</span>
                    <span class="badge badge-${e.info.level.toLowerCase()}">${e.info.level}</span>
                </div>`).join('');

        // Targets: 3 events with smallest positive gap to next cut
        const targets = evtStats
            .filter(e => e.info && e.info.gap != null && e.info.gap > 0)
            .sort((a, b) => a.info.gap - b.info.gap)
            .slice(0, 3);

        elTargets.innerHTML = targets.length === 0
            ? '<div class="sp-empty-list">No close-target events in this course yet.</div>'
            : targets.map(e => `
                <div class="sp-row">
                    <span class="sp-evt">${e.cat.label}</span>
                    <span class="sp-time">${e.info.time}</span>
                    <span class="badge badge-${e.info.nextCut.type.toLowerCase()}">${e.info.nextCut.type}</span>
                    <span class="sp-gap">-${e.info.gap.toFixed(2)}s</span>
                </div>`).join('');

        // Best Times table
        const allTimes = (m.best_times || [])
            .map(ev => ({ ev, ei: normalizeEvent(ev.event) }))
            .filter(x => x.ei && x.ei.course === currentCourse);
        if (allTimes.length === 0) {
            elBest.innerHTML = '<p style="color:#94a3b8;padding:0.5rem">No best times in this course yet.</p>';
        } else {
            allTimes.sort((a, b) => {
                const o = ['FREE', 'BACK', 'BREAST', 'FLY', 'IM'];
                const so = o.indexOf(a.ei.stroke) - o.indexOf(b.ei.stroke);
                if (so !== 0) return so;
                return parseInt(a.ei.distance) - parseInt(b.ei.distance);
            });
            let html = '<table><thead><tr><th>Event</th><th>Time</th><th>Date</th><th>Level</th></tr></thead><tbody>';
            allTimes.forEach(({ ev, ei }) => {
                const cat = { dist: ei.distance, stroke: ei.stroke, label: '' };
                const lv = levelForEvent(m, cat);
                const lvlBadge = lv && lv.level ? `<span class="badge badge-${lv.level.toLowerCase()}">${lv.level}</span>` : '<span style="color:#cbd5e0">—</span>';
                html += `<tr>
                    <td>${ei.distance} ${ei.stroke[0]}${ei.stroke.slice(1).toLowerCase()}</td>
                    <td class="lead-time">${ev.time}</td>
                    <td>${ev.date || '—'}</td>
                    <td>${lvlBadge}</td>
                </tr>`;
            });
            html += '</tbody></table>';
            elBest.innerHTML = html;
        }

        // Radar — always render, even when every stroke is below B. Center
        // ring is labeled '<B' so a swimmer just starting out still gets a
        // shape (a flat circle near the inner ring) instead of an empty
        // canvas with a 'no times' message.
        const peakRanks = highestPerStroke(m);
        const hasTimes = (m.best_times || []).length > 0;
        if (radarChart) { radarChart.destroy(); radarChart = null; }
        if (!hasTimes) {
            // Truly nothing to show — no swims at all
            radarCanvas.style.display = 'none';
            radarEmpty.classList.remove('hidden');
            return;
        }
        radarCanvas.style.display = '';
        radarEmpty.classList.add('hidden');
        radarChart = new Chart(radarCanvas, {
            type: 'radar',
            data: {
                labels: STROKES,
                datasets: [{
                    label: `Highest USA level (${currentCourse})`,
                    data: STROKES.map(s => peakRanks[s]),
                    backgroundColor: 'rgba(0,85,164,0.18)',
                    borderColor: '#0055a4',
                    borderWidth: 2.5,
                    pointBackgroundColor: '#0055a4',
                    pointRadius: 5,
                    pointHoverRadius: 8,
                }],
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: ctx => {
                                const v = ctx.raw;
                                return v > 0 ? `${LEVEL_NAMES[v] || v}` : '< B standard';
                            },
                        },
                    },
                },
                scales: {
                    r: {
                        suggestedMin: 0, suggestedMax: 6,
                        ticks: {
                            stepSize: 1,
                            callback: v => v === 0 ? '<B' : (LEVEL_NAMES[v] || ''),
                        },
                        pointLabels: { font: { size: 13, weight: '700' }, color: '#003366' },
                    },
                },
            },
        });
    }

    window.openSwimmerProfile = function (member) {
        if (!member) return;
        currentMember = member;
        const m = member;
        elName.textContent = `${m.first_name} ${m.last_name}`;
        const bits = [];
        if (m.age != null) bits.push(`Age ${m.age}`);
        if (m.gender) bits.push(m.gender === 'F' ? 'Female' : 'Male');
        if (m.roster) bits.push(m.roster);
        if (m.ct_team) bits.push(`CT: ${m.ct_team}`);
        elMeta.textContent = bits.join(' · ');
        modal.classList.remove('hidden');
        renderProfile();
        populateProgressDropdown();
    };
})();


// ===== Admin: Auto-link cached swimmers to team_members =====
if (hasPerm('batch')) {
    const linkBtn = document.getElementById('autoLinkBtn');
    const linkRes = document.getElementById('autoLinkResult');
    if (linkBtn) {
        linkBtn.addEventListener('click', async () => {
            linkBtn.disabled = true;
            linkBtn.textContent = 'Linking...';
            linkRes.textContent = '';
            try {
                const r = await fetch('/api/team_members/auto_link', { method: 'POST' });
                const d = await r.json();
                if (d.error) { linkRes.textContent = 'Error: ' + d.error; return; }
                const parts = [`Linked ${d.linked}`];
                if (d.unmatched.length) parts.push(`${d.unmatched.length} unmatched`);
                if (d.conflicts.length) parts.push(`${d.conflicts.length} conflicts`);
                linkRes.textContent = parts.join(' • ');
            } catch (e) {
                linkRes.textContent = 'Failed: ' + e.message;
            } finally {
                linkBtn.disabled = false;
                linkBtn.textContent = 'Link Cached Times to Roster';
            }
        });
    }
}


// ===== CHAMPIONSHIP QUALIFICATION TAB (admin & coach) =====
if (hasPerm('dashboard')) {
    const cqProgram = document.getElementById('cqProgram');
    const cqCourse = document.getElementById('cqCourse');
    const cqRoster = document.getElementById('cqRoster');
    const cqQualified = document.getElementById('cqQualified');
    const cqClosest = document.getElementById('cqClosest');
    let cqMembers = [];
    let cqLoaded = false;

    const CQ_AGE_GROUP_OF = (age) => {
        if (age == null) return null;
        if (age <= 10) return '10/Under';
        if (age <= 12) return '11/12';
        if (age <= 14) return '13/14';
        if (age <= 16) return '15/16';
        return '17/18';
    };

    function cqPopulatePrograms() {
        const programs = window.STANDARDS_DATA?.programs || {};
        // Only championship-style programs (gendered cuts; not multi-level USA)
        const champPrograms = Object.entries(programs).filter(([, p]) => !p.multi_level);
        cqProgram.innerHTML = champPrograms
            .map(([id, p]) => {
                const sub = p.subtitle ? ` (${p.subtitle})` : '';
                return `<option value="${id}">${p.display_name || id}${sub}</option>`;
            })
            .join('');
    }

    function cqPopulateRosters() {
        const groups = [...new Set(cqMembers.map(m => m.roster).filter(Boolean))].sort();
        cqRoster.innerHTML = '<option value="">All Groups</option>' +
            groups.map(g => `<option value="${g}">${g}</option>`).join('');
    }

    async function cqLoad() {
        if (cqLoaded) { cqRender(); return; }
        try {
            const r = await fetch('/api/my_swimmers');
            const d = await r.json();
            cqMembers = (d.members || []).filter(m => m.gender && m.age && m.ct_id);
            cqLoaded = true;
            cqPopulatePrograms();
            cqPopulateRosters();
            cqRender();
        } catch (e) {
            cqQualified.innerHTML = '<div class="cq-empty">Failed to load swimmers.</div>';
        }
    }

    // Map our internal age-group to a program's group key. CT-style programs
    // use '10/Under' / '11/12'; EZ-style use '10 & Under' / '11-12'. Try a few.
    function cqResolveGroupKey(prog, ageGroup) {
        const groups = prog.groups || {};
        if (groups[ageGroup]) return ageGroup;
        const aliases = {
            '10/Under': ['10/Under', '10 & Under', '10 and Under', '10U', '10/U'],
            '11/12':    ['11/12', '11-12'],
            '13/14':    ['13/14', '13-14'],
            '15/16':    ['15/16', '15-16'],
            '17/18':    ['17/18', '17-18'],
        };
        for (const k of (aliases[ageGroup] || [])) {
            if (groups[k]) return k;
        }
        return null;
    }

    function cqEventCutSeconds(prog, ageGroup, gender, course, eventInfo) {
        const groupKey = cqResolveGroupKey(prog, ageGroup);
        if (!groupKey) return null;
        const group = prog.groups[groupKey];
        const gKeys = prog.gender_keys || ['girls', 'boys'];
        const genderKey = (gender === 'F')
            ? (gKeys.includes('girls') ? 'girls' : 'women')
            : (gKeys.includes('boys')  ? 'boys'  : 'men');
        const courseTimes = group[genderKey]?.[course];
        if (!courseTimes) return null;
        const idx = findEventIndex(group.events, eventInfo.standardName, eventInfo.distance, eventInfo.stroke);
        if (idx < 0) return null;
        const t = courseTimes[idx];
        const secs = t ? timeToSeconds(t) : Infinity;
        return isFinite(secs) ? { secs, time: t, eventLabel: group.events[idx] } : null;
    }

    function cqRender() {
        const programs = window.STANDARDS_DATA?.programs || {};
        const progId = cqProgram.value;
        const prog = programs[progId];
        if (!prog) {
            cqQualified.innerHTML = '<div class="cq-empty">Pick a championship.</div>';
            cqClosest.innerHTML = '';
            return;
        }
        const course = cqCourse.value;
        const rosterFilter = cqRoster.value;

        const filtered = cqMembers.filter(m => !rosterFilter || m.roster === rosterFilter);

        const qualifiedBy = new Map(); // member.id → { member, events:[{label, time, cutTime}] }
        const closeRows = [];

        filtered.forEach(m => {
            const ag = CQ_AGE_GROUP_OF(m.age);
            (m.best_times || []).forEach(ev => {
                const ei = normalizeEvent(ev.event);
                if (!ei || ei.course !== course) return;
                const cut = cqEventCutSeconds(prog, ag, m.gender, course, ei);
                if (!cut) return;
                const swSecs = timeToSeconds(ev.time);
                if (!isFinite(swSecs)) return;
                if (swSecs <= cut.secs) {
                    const entry = qualifiedBy.get(m.id) || { member: m, events: [] };
                    entry.events.push({ label: cut.eventLabel, time: ev.time, cutTime: cut.time });
                    qualifiedBy.set(m.id, entry);
                } else {
                    const gapSecs = swSecs - cut.secs;
                    const gapPct = (gapSecs / cut.secs) * 100;
                    closeRows.push({
                        member: m, ageGroup: ag, eventLabel: cut.eventLabel,
                        time: ev.time, secs: swSecs,
                        cutTime: cut.time, cutSecs: cut.secs,
                        gapSecs, gapPct,
                    });
                }
            });
        });

        // Render Qualified
        if (qualifiedBy.size === 0) {
            cqQualified.innerHTML = '<div class="cq-empty">No swimmers have hit a cut for this championship + course yet.</div>';
        } else {
            const cards = [...qualifiedBy.values()]
                .sort((a, b) => b.events.length - a.events.length || a.member.last_name.localeCompare(b.member.last_name))
                .map(({ member, events }) => `
                    <div class="cq-qual-card">
                        <div class="cq-name"><a href="#" class="profile-link" data-mid="${member.id}">${member.first_name} ${member.last_name}</a></div>
                        <div class="cq-meta">Age ${member.age} · ${member.gender} · ${member.roster || '—'} · <strong>${events.length}</strong> cut${events.length === 1 ? '' : 's'}</div>
                        <div class="cq-events">
                            ${events.map(e => `<span class="cq-evt-pill" title="Best: ${e.time} | Cut: ${e.cutTime}">${e.label}</span>`).join('')}
                        </div>
                    </div>
                `);
            cqQualified.innerHTML = cards.join('');
            wireProfileLinks(cqQualified, id => filtered.find(m => m.id === id));
        }

        // Render Closest — sorted by gapPct ascending (smallest % gap = closest)
        closeRows.sort((a, b) => a.gapPct - b.gapPct);
        const top = closeRows.slice(0, 50);
        if (top.length === 0) {
            cqClosest.innerHTML = '<p style="padding:1rem;color:#94a3b8">Nothing close yet.</p>';
        } else {
            const maxPct = Math.max(...top.map(r => r.gapPct));
            let html = '<table><thead><tr><th>#</th><th>Swimmer</th><th>Group</th><th>Age</th><th>Event</th><th>Best</th><th>Cut</th><th>Gap %</th><th>Gap (s)</th></tr></thead><tbody>';
            top.forEach((r, i) => {
                const fillPct = Math.min(100, (r.gapPct / Math.max(maxPct, 0.01)) * 100);
                html += `<tr>
                    <td>${i + 1}</td>
                    <td><a href="#" class="profile-link" data-mid="${r.member.id}"><strong>${r.member.first_name} ${r.member.last_name}</strong></a></td>
                    <td><span class="roster-tag">${r.member.roster || '—'}</span></td>
                    <td>${r.member.age}</td>
                    <td>${r.eventLabel}</td>
                    <td>${r.time}</td>
                    <td>${r.cutTime}</td>
                    <td><span class="cq-gap-pct">${r.gapPct.toFixed(2)}%</span><span class="cq-bar-wrap"><span class="cq-bar" style="width:${fillPct}%"></span></span></td>
                    <td><span class="cq-gap-secs">-${r.gapSecs.toFixed(2)}s</span></td>
                </tr>`;
            });
            html += '</tbody></table>';
            cqClosest.innerHTML = html;
            wireProfileLinks(cqClosest, id => filtered.find(m => m.id === id));
        }
    }

    [cqProgram, cqCourse, cqRoster].forEach(el => el && el.addEventListener('change', cqRender));
    document.querySelector('[data-tab="champqual"]')?.addEventListener('click', cqLoad);
}
