// ===== STANDARDS DATA =====
// Data lives in data/standards.json on the server (editable via Standards Editor).
// The Flask template injects it as window.STANDARDS_DATA on every page load.
// This file ONLY contains the JS bindings + helper functions.

(function () {
    const D = window.STANDARDS_DATA || { programs: {}, conversion_factors: {}, whatif_events: {} };
    const programs = D.programs || {};

    // Backwards-compatible globals used by the rest of app.js
    window.CT_STANDARDS = (programs.ct_age_group && programs.ct_age_group.groups) || {};
    window.EZ_STANDARDS = (programs.eastern_zone && programs.eastern_zone.groups) || {};
    window.USA_STANDARDS = (programs.usa_motivational && programs.usa_motivational.groups) || {};
    window.CONVERSION_FACTORS = D.conversion_factors || {};
    window.WHATIF_EVENTS = D.whatif_events || {};
    // Surface program-level meet metadata (date/venue/etc) to the UI.
    window.PROGRAM_META = {
        ct_age_group: programs.ct_age_group || {},
        eastern_zone: programs.eastern_zone || {},
        usa_motivational: programs.usa_motivational || {},
    };
})();

// ===== HELPER FUNCTIONS =====

// World Aquatics (FINA) point scoring: P = 1000 × (B/T)^3
// where B = 2026 base time (1000-point reference, published annually
// by World Aquatics) and T = swimmer's time. Only LCM is supported —
// World Aquatics doesn't publish a Short Course Yards (SCY) table.
// Source: data/world_aquatics_points.json (committed, fetched once
// per page load via window.WA_POINTS).
function finaPoints(timeSecs, eventInfo, gender) {
    if (!isFinite(timeSecs) || timeSecs <= 0) return null;
    if (!eventInfo || eventInfo.course !== 'LCM') return null;
    if (gender !== 'F' && gender !== 'M') return null;
    const wa = (window.WA_POINTS && window.WA_POINTS.base_times_lcm) || {};
    const table = wa[gender] || {};
    // event key format: "<distance> <stroke>" e.g. "100 FREE"
    const key = `${eventInfo.distance} ${eventInfo.stroke}`;
    const base = table[key];
    if (!base) return null;
    const baseSecs = timeToSeconds(base);
    if (!isFinite(baseSecs) || baseSecs <= 0) return null;
    const pts = Math.round(1000 * Math.pow(baseSecs / timeSecs, 3));
    return pts >= 0 ? pts : 0;
}

function timeToSeconds(timeStr) {
    if (!timeStr || timeStr === 'N/A' || timeStr === 'NT') return Infinity;
    timeStr = timeStr.replace('*', '').trim();
    const parts = timeStr.split(':');
    if (parts.length === 3) return parseFloat(parts[0]) * 3600 + parseFloat(parts[1]) * 60 + parseFloat(parts[2]);
    if (parts.length === 2) return parseFloat(parts[0]) * 60 + parseFloat(parts[1]);
    return parseFloat(parts[0]);
}

function secondsToTime(secs) {
    if (secs <= 0 || !isFinite(secs)) return 'N/A';
    if (secs >= 3600) {
        const h = Math.floor(secs / 3600);
        const m = Math.floor((secs % 3600) / 60);
        const s = (secs % 60).toFixed(2);
        return `${h}:${m.toString().padStart(2,'0')}:${s.padStart(5,'0')}`;
    }
    if (secs >= 60) {
        const m = Math.floor(secs / 60);
        const s = (secs % 60).toFixed(2);
        return `${m}:${s.padStart(5,'0')}`;
    }
    return secs.toFixed(2);
}

function normalizeEvent(ctEvent) {
    const match = ctEvent.match(/^(\d+)(Y|L)\s+(.+)$/);
    if (!match) return null;
    const distance = match[1];
    const course = match[2] === 'Y' ? 'SCY' : 'LCM';
    const strokeMap = {
        'freestyle': 'FREE', 'backstroke': 'BACK', 'breaststroke': 'BREAST',
        'butterfly': 'FLY', 'individual medley': 'IM',
    };
    const stroke = strokeMap[match[3].toLowerCase()] || match[3].toUpperCase();
    const standardName = `${distance} ${stroke}`;
    const usaStroke = { 'FREE': 'FR', 'BACK': 'BK', 'BREAST': 'BR', 'FLY': 'FL', 'IM': 'IM' };
    const usaName = `${distance} ${usaStroke[stroke] || stroke}`;
    return { distance, course, stroke, standardName, usaName };
}

function getAgeGroup(dobStr) {
    if (!dobStr) return null;
    const dob = new Date(dobStr);
    const today = new Date();
    let age = today.getFullYear() - dob.getFullYear();
    const m = today.getMonth() - dob.getMonth();
    if (m < 0 || (m === 0 && today.getDate() < dob.getDate())) age--;
    if (age <= 10) return '10/Under';
    if (age <= 12) return '11/12';
    if (age <= 14) return '13/14';
    if (age <= 16) return '15/16';
    return '17/18';
}

function getAge(dobStr) {
    if (!dobStr) return null;
    const dob = new Date(dobStr);
    const today = new Date();
    let age = today.getFullYear() - dob.getFullYear();
    const m = today.getMonth() - dob.getMonth();
    if (m < 0 || (m === 0 && today.getDate() < dob.getDate())) age--;
    return age;
}

function mapAgeGroupEZ(ag) {
    return { '10/Under': '10 & Under', '11/12': '11-12', '13/14': '13-14' }[ag] || null;
}

function mapAgeGroupUSA(ag, gender, course) {
    const genderStr = gender === 'F' ? 'Girls' : 'Boys';
    const mapped = { '10/Under': '10 & under', '11/12': '11-12', '13/14': '13-14', '15/16': '15-16', '17/18': '17-18' }[ag];
    return mapped ? `${mapped} ${genderStr} ${course}` : null;
}

// Ordered from easiest to hardest
const USA_LEVEL_ORDER = ['B', 'BB', 'A', 'AA', 'AAA', 'AAAA'];

function lookupStandards(eventInfo, ageGroup, gender) {
    if (!eventInfo || !ageGroup) return { usa: [], champ: [] };
    const course = eventInfo.course;
    const genderKey = gender === 'F' ? 'girls' : 'boys';
    const genderKeyEZ = gender === 'F' ? 'women' : 'men';

    const usaStandards = [];
    const champStandards = [];

    // CT AG cut
    const ctAg = CT_STANDARDS[ageGroup];
    if (ctAg && ctAg[genderKey] && ctAg[genderKey][course]) {
        const idx = findEventIndex(ctAg.events, eventInfo.standardName, eventInfo.distance, eventInfo.stroke);
        if (idx >= 0) {
            champStandards.push({ type: 'CT AG', time: ctAg[genderKey][course][idx], cssClass: 'badge-ct', label: 'CT Age Group Champs' });
        }
    }

    // EZ cut
    const ezAg = mapAgeGroupEZ(ageGroup);
    if (ezAg && EZ_STANDARDS[ezAg]) {
        const ezData = EZ_STANDARDS[ezAg];
        const genderData = ezData[genderKeyEZ];
        if (genderData && genderData[course]) {
            const idx = findEventIndex(ezData.events, eventInfo.standardName, eventInfo.distance, eventInfo.stroke);
            if (idx >= 0) {
                champStandards.push({ type: 'EZ', time: genderData[course][idx], cssClass: 'badge-ez', label: 'Eastern Zone Champs' });
            }
        }
    }

    // USA Motivational
    const usaKey = mapAgeGroupUSA(ageGroup, gender, course);
    if (usaKey && USA_STANDARDS[usaKey]) {
        const usaData = USA_STANDARDS[usaKey];
        const idx = findEventIndex(usaData.events, eventInfo.usaName, eventInfo.distance, eventInfo.stroke);
        if (idx >= 0) {
            // Return in order: B, BB, A, AA, AAA, AAAA (easiest to hardest)
            const levelsReversed = [...usaData.levels].reverse();
            const timesReversed = [...usaData.times[idx]].reverse();
            levelsReversed.forEach((level, li) => {
                usaStandards.push({
                    type: level,
                    time: timesReversed[li],
                    cssClass: `badge-${level.toLowerCase()}`,
                    order: USA_LEVEL_ORDER.indexOf(level)
                });
            });
        }
    }

    return { usa: usaStandards, champ: champStandards };
}

function findEventIndex(eventList, name, distance, stroke) {
    let idx = eventList.indexOf(name);
    if (idx >= 0) return idx;
    const strokeAbbrevs = {
        'FREE': ['FREE', 'FR'], 'BACK': ['BACK', 'BK'], 'BREAST': ['BREAST', 'BR'],
        'FLY': ['FLY', 'FL'], 'IM': ['IM']
    };
    const abbrevs = strokeAbbrevs[stroke] || [stroke];
    for (let i = 0; i < eventList.length; i++) {
        const ev = eventList[i].toUpperCase();
        const distances = ev.match(/\d+/g) || [];
        if (distances.includes(distance)) {
            for (const abbr of abbrevs) {
                if (ev.includes(abbr)) return i;
            }
        }
    }
    return -1;
}

// Returns { usaAchieved:[], usaNext:null, champAchieved:[], champNext:null, highestUSA:string|null }
function compareToStandards(swimmerTimeSecs, standards) {
    const { usa, champ } = standards;

    // USA: ordered B -> AAAA. Find highest achieved and next target
    const usaAchieved = [];
    let usaNext = null;
    // usa is already B, BB, A, AA, AAA, AAAA order
    for (const std of usa) {
        const stdSecs = timeToSeconds(std.time);
        if (!isFinite(stdSecs)) continue;
        if (swimmerTimeSecs <= stdSecs) {
            usaAchieved.push(std);
        } else if (!usaNext) {
            usaNext = std; // This is the NEXT one to achieve (easiest unachieved)
        }
    }

    // If all achieved, no next
    // highestUSA = the hardest one achieved
    const highestUSA = usaAchieved.length > 0 ? usaAchieved[usaAchieved.length - 1] : null;

    // Championships
    const champAchieved = [];
    let champNext = null;
    for (const std of champ) {
        const stdSecs = timeToSeconds(std.time);
        if (!isFinite(stdSecs)) continue;
        if (swimmerTimeSecs <= stdSecs) {
            champAchieved.push(std);
        } else if (!champNext) {
            champNext = std;
        }
    }

    return { usaAchieved, usaNext, champAchieved, champNext, highestUSA };
}
