// frontend/results.js — Results Intelligence Hub v2.0
// List-only view, Email, Phase-2 on-demand outreach (single + batch)

// ──────────────────────────────────────
// STATE
// ──────────────────────────────────────
let globalResults = [];
let filteredResults = [];
let currentSlideOverIndex = -1;

// outreachState[originalIndex]: 'idle' | 'loading' | 'done'
const outreachState = {};

const filters = {
    category: 'all',
    minScore: 0,
    wa: 'all',
    minRating: 0,
    city: '',
    outreach: 'all',
    sort: 'score_desc'
};

// ──────────────────────────────────────
// INIT
// ──────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
    const raw = sessionStorage.getItem('ois_results');
    const query = sessionStorage.getItem('ois_query');

    if (!raw) { window.location.href = '/'; return; }

    let data;
    try { data = JSON.parse(raw); } catch (e) { window.location.href = '/'; return; }

    globalResults = data.results || [];

    // Topbar stats
    document.getElementById('queryLabel').textContent = query || 'Campaign Results';
    document.getElementById('statTotal').textContent = globalResults.length;
    document.getElementById('statTime').textContent = `${Math.round(data.summary?.processing_time_sec || 0)}s`;
    document.getElementById('statHighScore').textContent = globalResults.filter(r => (r.business_profile?.lead_score || 0) >= 60).length;
    document.getElementById('statWhatsapp').textContent = globalResults.filter(r => r.business_profile?.whatsapp_status === 'DETECTED').length;
    document.getElementById('countTotal').textContent = globalResults.length;

    // Initialize outreach state
    globalResults.forEach((_, i) => { outreachState[i] = 'idle'; });

    applyFilters();
});

// Keyboard nav
document.addEventListener('keydown', (e) => {
    if (document.getElementById('slideOver').classList.contains('hidden')) return;
    if (e.key === 'Escape') closeSlideOver();
    if (e.key === 'ArrowLeft') navigateLead(-1);
    if (e.key === 'ArrowRight') navigateLead(1);
});

// ──────────────────────────────────────
// FILTER ENGINE
// ──────────────────────────────────────
function applyFilters() {
    let results = globalResults.map((r, i) => ({ ...r, _origIdx: i }));

    if (filters.category !== 'all') {
        results = results.filter(r => r.business_profile?.category === filters.category);
    }
    results = results.filter(r => (r.business_profile?.lead_score || 0) >= filters.minScore);

    if (filters.wa !== 'all') {
        results = results.filter(r => r.business_profile?.whatsapp_status === filters.wa);
    }
    if (filters.minRating > 0) {
        results = results.filter(r => (r.business_profile?.google_rating || 0) >= filters.minRating);
    }

    if (filters.city.trim()) {
        const q = filters.city.toLowerCase().trim();
        results = results.filter(r => {
            const p = r.business_profile || {};
            return (p.address || '').toLowerCase().includes(q) ||
                   (p.city || '').toLowerCase().includes(q) ||
                   (p.state || '').toLowerCase().includes(q);
        });
    }
    if (filters.outreach === 'ready') {
        results = results.filter(r => outreachState[r._origIdx] === 'done');
    } else if (filters.outreach === 'pending') {
        results = results.filter(r => outreachState[r._origIdx] === 'idle');
    }

    results = sortResults(results);
    filteredResults = results;

    document.getElementById('countShown').textContent = results.length;

    const emptyEl = document.getElementById('emptyFiltered');
    const listEl = document.getElementById('resultsList');

    if (results.length === 0) {
        emptyEl.classList.remove('hidden');
        listEl.classList.add('hidden');
    } else {
        emptyEl.classList.add('hidden');
        listEl.classList.remove('hidden');
        renderList(results);
    }
}

function sortResults(results) {
    const s = filters.sort;
    return [...results].sort((a, b) => {
        const ap = a.business_profile || {};
        const bp = b.business_profile || {};
        if (s === 'score_desc')   return (bp.lead_score || 0) - (ap.lead_score || 0);
        if (s === 'rating_desc')  return (bp.google_rating || 0) - (ap.google_rating || 0);
        if (s === 'reviews_desc') return (bp.google_review_count || 0) - (ap.google_review_count || 0);
        if (s === 'name_asc')     return (ap.name || '').localeCompare(bp.name || '');
        return 0;
    });
}

// ──────────────────────────────────────
// LIST RENDERER
// ──────────────────────────────────────
function renderList(results) {
    const listBody = document.getElementById('listBody');
    listBody.innerHTML = '';

    results.forEach((row, filteredIdx) => {
        const origIdx = row._origIdx;
        const profile = row.business_profile || {};
        let catTag = 'tag-dead', catLabel = 'No Website';
        if (profile.category === 'STATIC_WEBSITE') {
            catTag = 'tag-static';
            catLabel = (profile.website_tech_type && profile.website_tech_type.includes('Dynamic')) ? 'Dynamic Web' : 'Static Web';
            if (profile.has_generic_booking) {
                catTag = 'tag-auto';
                catLabel = 'Custom Booking';
            }
        }
        if (profile.category === 'FULLY_AUTOMATED') { catTag = 'tag-auto';   catLabel = 'Automated'; }

        const isWa = profile.whatsapp_status === 'DETECTED';
        const score = profile.lead_score || 0;
        const scoreClass = score >= 60 ? 'score-high' : score >= 30 ? 'score-mid' : 'score-low';
        const addressText = escHtml(profile.address || profile.city || profile.state || '—');
        const state = outreachState[origIdx] || 'idle';

        let websiteCell = `<div class="lr-website lr-website-none">No Link</div>`;
        if (profile.website_url) {
            const displayUrl = profile.website_url.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '');
            websiteCell = `<div class="lr-website"><a href="${escHtml(profile.website_url)}" target="_blank" onclick="event.stopPropagation()">${escHtml(displayUrl)}</a></div>`;
        }

        // Outreach status cell
        let outreachCell = '';
        if (state === 'idle') {
            outreachCell = `<button class="gen-outreach-btn" id="genBtn_${origIdx}"
                onclick="event.stopPropagation(); generateOutreach(${origIdx})">
                ✉ Generate →
            </button>`;
        } else if (state === 'loading') {
            outreachCell = `<span class="outreach-status-pill ops-loading">⟳ Generating...</span>`;
        } else if (state === 'done') {
            outreachCell = `<span class="outreach-status-pill ops-done">✓ Ready</span>`;
        }

        listBody.insertAdjacentHTML('beforeend', `
            <div class="list-row" id="row_${origIdx}" onclick="openSlideOver(${filteredIdx})">
                <div class="lr-score">
                    <span class="score-pill ${scoreClass}">${score}</span>
                </div>
                <div class="lr-name">
                    <div class="lr-business">${escHtml(profile.name || 'Unknown')}</div>
                </div>
                <div class="lr-address">${addressText}</div>
                ${websiteCell}
                <div class="lr-category">
                    <span class="category-tag ${catTag}">${catLabel}</span>
                </div>
                <div class="lr-contact">
                    <div class="lr-phone">${escHtml(profile.phone || '—')}</div>
                </div>
                <div class="lr-whatsapp ${isWa ? 'wa-detected' : 'wa-muted'}">${isWa ? '✓ Verified' : '—'}</div>
                <div class="lr-rating">
                    ${profile.google_rating ? `${profile.google_rating} ⭐` : '—'}
                    <span class="lr-reviews">(${profile.google_review_count || 0})</span>
                </div>
                <div class="lr-outreach">${outreachCell}</div>
                <div class="lr-action"><span class="open-arrow">→</span></div>
            </div>
        `);
    });
}

// ──────────────────────────────────────
// PHASE 2A: Single-Lead Outreach
// ──────────────────────────────────────
async function generateOutreach(origIdx) {
    if (outreachState[origIdx] === 'loading' || outreachState[origIdx] === 'done') return;

    outreachState[origIdx] = 'loading';
    reRenderRow(origIdx);

    // If slide-over is open for this lead, update it
    if (!document.getElementById('slideOver').classList.contains('hidden')) {
        const soLead = filteredResults[currentSlideOverIndex];
        if (soLead && soLead._origIdx === origIdx) {
            renderSlideOverOutreachSection(origIdx);
        }
    }

    const lead = globalResults[origIdx]?.business_profile;
    if (!lead) { outreachState[origIdx] = 'idle'; return; }

    try {
        const response = await fetch('/api/outreach/single', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ lead })
        });

        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();

        // Store outreach back into globalResults
        globalResults[origIdx].outreach = data.outreach;
        outreachState[origIdx] = 'done';

    } catch (err) {
        console.error('Outreach generation failed:', err);
        outreachState[origIdx] = 'idle';
    }

    reRenderRow(origIdx);

    // Refresh slide-over if still open
    if (!document.getElementById('slideOver').classList.contains('hidden')) {
        const soLead = filteredResults[currentSlideOverIndex];
        if (soLead && soLead._origIdx === origIdx) {
            renderSlideOver(currentSlideOverIndex);
        }
    }
}

function reRenderRow(origIdx) {
    // Re-render only the outreach cell without re-rendering the whole list
    const outreachCell = document.querySelector(`#row_${origIdx} .lr-outreach`);
    if (!outreachCell) { applyFilters(); return; } // fallback: full re-render

    const state = outreachState[origIdx] || 'idle';
    let html = '';
    if (state === 'idle') {
        html = `<button class="gen-outreach-btn" id="genBtn_${origIdx}"
            onclick="event.stopPropagation(); generateOutreach(${origIdx})">
            ✉ Generate →
        </button>`;
    } else if (state === 'loading') {
        html = `<span class="outreach-status-pill ops-loading">⟳ Generating...</span>`;
    } else if (state === 'done') {
        html = `<span class="outreach-status-pill ops-done">✓ Ready</span>`;
    }
    outreachCell.innerHTML = html;
}
// ──────────────────────────────────────
// FILTER DRAWER TOGGLE
// ──────────────────────────────────────
function toggleFilterPanel(forceState) {
    const sidebar = document.getElementById('filterSidebar');
    const backdrop = document.getElementById('filterBackdrop');
    
    if (sidebar) {
        if (typeof forceState === 'boolean') {
            if (forceState) {
                sidebar.classList.add('open');
                if (backdrop) backdrop.classList.remove('hidden');
            } else {
                sidebar.classList.remove('open');
                if (backdrop) backdrop.classList.add('hidden');
            }
        } else {
            sidebar.classList.toggle('open');
            if (backdrop) backdrop.classList.toggle('hidden');
        }
    }
}

// ──────────────────────────────────────
// PHASE 2B: Batch Outreach (Process All)
// ──────────────────────────────────────
async function processAllOutreach() {
    const btn = document.getElementById('processAllBtn');
    const progressEl = document.getElementById('paProgress');

    // Find all idle leads in the CURRENT filtered set
    const toProcess = filteredResults
        .map(r => r._origIdx)
        .filter(i => outreachState[i] === 'idle');

    if (toProcess.length === 0) {
        btn.classList.add('done');
        btn.querySelector('.pa-label').textContent = '✓ All Ready';
        return;
    }

    // Mark all as loading immediately
    toProcess.forEach(i => { outreachState[i] = 'loading'; reRenderRow(i); });

    btn.classList.add('loading');
    btn.disabled = true;
    progressEl.classList.remove('hidden');
    progressEl.textContent = `0 / ${toProcess.length}`;

    const leads = toProcess.map(i => globalResults[i]?.business_profile).filter(Boolean);

    try {
        const response = await fetch('/api/outreach/batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ leads })
        });

        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();

        // Map results back
        (data.results || []).forEach((res, idx) => {
            const origIdx = toProcess[idx];
            if (origIdx === undefined) return;
            if (res.status === 'success' && res.outreach) {
                globalResults[origIdx].outreach = res.outreach;
                outreachState[origIdx] = 'done';
            } else {
                outreachState[origIdx] = 'idle';
            }
            reRenderRow(origIdx);
            progressEl.textContent = `${idx + 1} / ${toProcess.length}`;
        });

        btn.classList.remove('loading');
        btn.classList.add('done');
        btn.querySelector('.pa-label').textContent = '✓ All Processed';
        btn.disabled = false;
        progressEl.textContent = `${toProcess.length} / ${toProcess.length}`;

    } catch (err) {
        console.error('Batch outreach failed:', err);
        toProcess.forEach(i => { outreachState[i] = 'idle'; reRenderRow(i); });
        btn.classList.remove('loading');
        btn.disabled = false;
        progressEl.classList.add('hidden');
        alert('Batch outreach failed: ' + err.message);
    }
}

// ──────────────────────────────────────
// SLIDE-OVER
// ──────────────────────────────────────
function openSlideOver(filteredIdx) {
    currentSlideOverIndex = filteredIdx;
    renderSlideOver(filteredIdx);
    document.getElementById('slideOver').classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    setTimeout(() => document.getElementById('slideOverPanel').classList.add('open'), 10);
}

function renderSlideOver(filteredIdx) {
    const row = filteredResults[filteredIdx];
    if (!row) return;
    const origIdx = row._origIdx;
    const profile = row.business_profile || {};
    const insights = profile.ai_research_insights || {};

    document.getElementById('leadNavLabel').textContent = `${filteredIdx + 1} / ${filteredResults.length}`;
    document.getElementById('prevLeadBtn').disabled = filteredIdx === 0;
    document.getElementById('nextLeadBtn').disabled = filteredIdx === filteredResults.length - 1;

    // Links
    let linksHtml = '';
    if (profile.website_url)     linksHtml += `<a href="${profile.website_url}" target="_blank" class="so-link">🌐 Website</a>`;
    if (profile.instagram_url)   linksHtml += `<a href="${profile.instagram_url}" target="_blank" class="so-link">📸 Instagram</a>`;
    if (profile.facebook_url)    linksHtml += `<a href="${profile.facebook_url}" target="_blank" class="so-link">📘 Facebook</a>`;
    if (profile.whatsapp_number) {
        const num = profile.whatsapp_number.replace(/\D/g, '');
        linksHtml += `<a href="https://wa.me/${num}" target="_blank" class="so-link wa-link">💬 WhatsApp</a>`;
    }
    if (profile.google_maps_url) linksHtml += `<a href="${profile.google_maps_url}" target="_blank" class="so-link">📍 Maps</a>`;
    if (!linksHtml) linksHtml = '<span class="muted-text">No external links found</span>';

    // Reviews
    let reviewsHtml = '';
    if (profile.reviews?.length) {
        profile.reviews.slice(0, 3).forEach(r => {
            if (!r?.text) return;
            reviewsHtml += `<div class="so-review">${r.stars || 5}⭐ <span>"${escHtml(r.text)}"</span></div>`;
        });
    }
    if (!reviewsHtml) reviewsHtml = '<p class="muted-text">No review text available.</p>';

    // AI Insights
    let insightsHtml = '';
    if (insights.pain_points?.length) {
        insightsHtml += `<div class="insight-section"><h5>Pain Points</h5><ul>${insights.pain_points.map(p => `<li>${escHtml(p)}</li>`).join('')}</ul></div>`;
    }
    if (insights.sparks?.length) {
        insightsHtml += `<div class="insight-section"><h5>Strengths</h5><ul class="sparks">${insights.sparks.map(s => `<li>${escHtml(s)}</li>`).join('')}</ul></div>`;
    }
    if (insights.personalization_hook) {
        insightsHtml += `<div class="insight-hook"><h5>Personalization Hook</h5><p>${escHtml(insights.personalization_hook)}</p></div>`;
    }
    if (!insightsHtml) insightsHtml = '<p class="muted-text">No AI insights available.</p>';

    const score = profile.lead_score || 0;
    const scoreClass = score >= 60 ? 'score-high' : score >= 30 ? 'score-mid' : 'score-low';

    document.getElementById('slideoverBody').innerHTML = `
        <div class="so-lead-header">
            <div class="so-title-row">
                <h2 class="so-title">${escHtml(profile.name || 'Unknown')}</h2>
                <span class="score-badge ${scoreClass}">${score}/100</span>
            </div>
            <p class="so-address">${escHtml(profile.address || '—')}</p>
            <div class="so-links">${linksHtml}</div>
        </div>

        <div class="so-grid">
            <div class="so-col">
                <div class="so-section">
                    <h4 class="so-section-title">AI Research Insights</h4>
                    ${insightsHtml}
                </div>
                <div class="so-section" style="margin-top:18px">
                    <h4 class="so-section-title">Top Reviews</h4>
                    <div class="so-reviews">${reviewsHtml}</div>
                </div>
                <div class="so-section" style="margin-top:16px">
                    <h4 class="so-section-title">Metadata</h4>
                    <div style="font-size:0.8rem; color:#94A3B8; display:flex; flex-direction:column; gap:4px; background:rgba(0,0,0,0.18); padding:11px; border-radius:7px">
                        <span><strong>Category:</strong> ${profile.category || '—'}</span>
                        <span><strong>Tech Stack:</strong> ${profile.website_tech_type || 'Unknown'}</span>
                        <span><strong>Booking Tech:</strong> ${profile.booking_system || (profile.has_generic_booking ? 'Custom Setup (' + (profile.generic_booking_buttons || []).join(', ') + ')' : 'None Detected')}</span>
                        <span><strong>Web Status:</strong> ${profile.website_status || '—'}</span>
                        <div style="display:flex; flex-direction:column; gap:2px">
                            <strong>Phone Numbers:</strong>
                            ${(profile.phone || '').split(',').map(p => {
                                const trimmed = p.trim();
                                const info = (insights.phone_consensus || {})[trimmed];
                                if (info) {
                                    const sourcesHtml = info.sources.map(s => {
                                        if (!s.url) return `<span title="${escHtml(s.type)}">${escHtml(s.type)}</span>`;
                                        const shortSource = s.url.replace(/^https?:\/\/(www\.)?/, '').split('/')[0] || s.type;
                                        return `<a href="${escHtml(s.url)}" target="_blank" title="${escHtml(s.type)}" style="color:var(--accent-primary); text-decoration:underline;">${escHtml(shortSource)}</a>`;
                                    }).join(', ');
                                    
                                    return `<div style="display:flex; flex-direction:column; gap:4px; margin-bottom:6px; padding-left:8px; border-left: 2px solid var(--accent-primary);">
                                        <span style="font-weight:600; font-size:1.05em; color:#E2E8F0;">${escHtml(trimmed)}</span>
                                        <span style="font-size:0.75rem; color:#94A3B8;">
                                            Score: <strong style="color:${info.score > 40 ? '#10B981' : '#F59E0B'}">${info.score}</strong> | Sources: ${sourcesHtml}
                                        </span>
                                    </div>`;
                                }
                                return `<span>${escHtml(trimmed)}</span>`;
                            }).join('') || '—'}
                        </div>
                    </div>
                </div>
            </div>
            <div class="so-col" id="outreachCol_${origIdx}">
                ${buildOutreachSection(origIdx)}
            </div>
        </div>
    `;
}

function buildOutreachSection(origIdx) {
    const state = outreachState[origIdx] || 'idle';
    const outreach = globalResults[origIdx]?.outreach;

    if (state === 'idle') {
        return `
            <div class="so-outreach-pending">
                <p>Outreach drafts not yet generated for this lead.</p>
                <button class="so-gen-btn" id="soGenBtn_${origIdx}" onclick="generateOutreach(${origIdx})">
                    ✉ Generate Outreach Drafts
                </button>
            </div>
        `;
    }

    if (state === 'loading') {
        return `
            <div class="so-outreach-pending">
                <p class="muted-text">⟳ Generating personalized outreach...</p>
                <div style="margin-top:10px; font-size:0.85rem; color: #F59E0B; animation: pulseFade 1s ease-in-out infinite alternate;">
                    Analyzing pain points &amp; crafting drafts...
                </div>
            </div>
        `;
    }

    if (state === 'done' && outreach) {
        const waMsg = (outreach.whatsapp || '—').replace(/\n/g, '<br>');
        const igMsg = (outreach.instagram || '—').replace(/\n/g, '<br>');
        return `
            <div class="so-section">
                <div class="so-outreach-header">
                    <h4 class="so-section-title">💬 WhatsApp Draft</h4>
                    <button class="copy-btn" onclick="copyText('soWa_${origIdx}', this)">Copy</button>
                </div>
                <div class="so-outreach-box" id="soWa_${origIdx}">${waMsg}</div>
            </div>
            <div class="so-section" style="margin-top:14px">
                <div class="so-outreach-header">
                    <h4 class="so-section-title">📸 Instagram Draft</h4>
                    <button class="copy-btn" onclick="copyText('soIg_${origIdx}', this)">Copy</button>
                </div>
                <div class="so-outreach-box so-ig" id="soIg_${origIdx}">${igMsg}</div>
            </div>
        `;
    }

    return `<p class="muted-text">Outreach could not be generated. Please try again.</p>`;
}

function renderSlideOverOutreachSection(origIdx) {
    const col = document.getElementById(`outreachCol_${origIdx}`);
    if (col) col.innerHTML = buildOutreachSection(origIdx);
}

function closeSlideOver() {
    document.getElementById('slideOverPanel').classList.remove('open');
    setTimeout(() => {
        document.getElementById('slideOver').classList.add('hidden');
        document.body.style.overflow = '';
    }, 300);
}

function navigateLead(direction) {
    const newIdx = currentSlideOverIndex + direction;
    if (newIdx < 0 || newIdx >= filteredResults.length) return;
    currentSlideOverIndex = newIdx;
    renderSlideOver(newIdx);
}

// ──────────────────────────────────────
// FILTER CHIP HANDLERS
// ──────────────────────────────────────
function handleChipClick(filterType, btn) {
    const parent = btn.closest('.filter-chips');
    parent.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    const val = btn.dataset.value;
    if (filterType === 'category') filters.category = val;
    else if (filterType === 'wa')      filters.wa = val;
    else if (filterType === 'rating')  filters.minRating = parseFloat(val);
    else if (filterType === 'outreach') filters.outreach = val;
    applyFilters();
}

function onScoreChange(input) {
    filters.minScore = parseInt(input.value);
    document.getElementById('scoreVal').textContent = input.value;
    input.style.background = `linear-gradient(to right, var(--accent-primary) ${input.value}%, rgba(255,255,255,0.12) ${input.value}%)`;
    applyFilters();
}

function sortByColumn(col) {
    const map = { score: 'score_desc', name: 'name_asc', rating: 'rating_desc' };
    filters.sort = map[col] || 'score_desc';
    document.getElementById('sortFilter').value = filters.sort;
    applyFilters();
}

function resetFilters() {
    filters.category = 'all'; filters.minScore = 0; filters.wa = 'all';
    filters.minRating = 0; filters.city = '';
    filters.outreach = 'all'; filters.sort = 'score_desc';

    const range = document.getElementById('scoreFilter');
    range.value = 0;
    range.style.background = 'rgba(255,255,255,0.12)';
    document.getElementById('scoreVal').textContent = '0';
    document.getElementById('cityFilter').value = '';
    document.getElementById('sortFilter').value = 'score_desc';

    ['categoryChips','waChips','ratingChips','outreachChips'].forEach(id => {
        document.getElementById(id).querySelectorAll('.filter-chip')
            .forEach((c, i) => c.classList.toggle('active', i === 0));
    });
    applyFilters();
}

// ──────────────────────────────────────
// CSV EXPORT
// ──────────────────────────────────────

/** Strip HTML tags from a string (e.g. <br> injected for display) */
function stripHtml(str) {
    return String(str || '').replace(/<[^>]*>/g, ' ').replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&#39;/g,"'").trim();
}

/** Sanitise a single CSV cell value:
 *  1. Strip any HTML tags (outreach drafts can contain <br>)
 *  2. Replace embedded newlines with a space so a single lead stays on one CSV row
 *  3. Escape double-quotes by doubling them
 *  4. Wrap in double-quotes
 */
function csvCell(val) {
    const clean = stripHtml(val)
        .replace(/\r\n/g, ' ')   // Windows line endings → space
        .replace(/\n/g, ' ')     // Unix line endings   → space
        .replace(/\r/g, ' ');    // Old Mac line endings→ space
    return `"${clean.replace(/"/g, '""')}"`;
}

function exportCSV() {
    if (!filteredResults.length) return;

    const headers = [
        'Place ID', 'Name', 'Category', 'Lead Score', 'Phone', 'WhatsApp Status',
        'Google Rating', 'Review Count', 'Address', 'City', 'State',
        'Website', 'Instagram', 'Facebook', 'WhatsApp Number',
        'Booking System', 'Website Status', 'Outreach Status',
        'WA Draft', 'IG Draft'
    ];

    const rows = filteredResults.map(r => {
        const p = r.business_profile || {};
        const o = globalResults[r._origIdx]?.outreach || {};
        const state = outreachState[r._origIdx] || 'idle';

        return [
            p.place_id || '',
            p.name || '',
            p.category || '',
            p.lead_score || 0,
            p.phone_unformatted || p.phone || '',
            p.whatsapp_status || '',
            p.google_rating || '',
            p.google_review_count || 0,
            p.address || '',
            p.city || '',
            p.state || '',
            p.website_url || '',
            p.instagram_url || '',
            p.facebook_url || '',
            p.whatsapp_number || '',
            p.booking_system || '',
            p.website_status || '',
            state,
            o.whatsapp || '',
            o.instagram || ''
        ].map(csvCell).join(',');
    });

    // BOM (\uFEFF) is prepended so Excel on Windows opens UTF-8 correctly
    const csv = '\uFEFF' + [headers.map(csvCell).join(','), ...rows].join('\r\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `outreach_leads_${Date.now()}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// ──────────────────────────────────────
// CLIPBOARD + UTILS
// ──────────────────────────────────────
function copyText(elementId, btn) {
    const el = document.getElementById(elementId);
    if (!el) return;
    navigator.clipboard.writeText(el.innerText).then(() => {
        btn.textContent = 'Copied!'; btn.classList.add('copied');
        setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 2000);
    }).catch(() => {
        const range = document.createRange();
        range.selectNode(el);
        window.getSelection().removeAllRanges();
        window.getSelection().addRange(range);
        document.execCommand('copy');
        window.getSelection().removeAllRanges();
    });
}

function escHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
