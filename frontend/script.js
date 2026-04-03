// frontend/script.js

let currentMode = 'mining';
let selectedFile = null;

function switchMode(mode) {
    currentMode = mode;
    
    // Update tabs
    document.querySelectorAll('.cmd-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.mode === mode);
    });

    // Update visibility
    if (mode === 'mining') {
        document.getElementById('miningInput').classList.remove('hidden');
        document.getElementById('enrichingInput').classList.add('hidden');
    } else {
        document.getElementById('miningInput').classList.add('hidden');
        document.getElementById('enrichingInput').classList.remove('hidden');
    }
}

function handleFileSelect(input) {
    const file = input.files[0];
    if (file) {
        selectedFile = file;
        document.getElementById('fileNameDisplay').innerText = `Selected: ${file.name}`;
        document.getElementById('dropZone').style.borderColor = 'var(--accent-primary)';
    }
}

// Drag & Drop functionality
const dropZone = document.getElementById('dropZone');
if (dropZone) {
    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });
    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('dragover');
    });
    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        if (e.dataTransfer.files.length) {
            document.getElementById('fileInput').files = e.dataTransfer.files;
            handleFileSelect(document.getElementById('fileInput'));
        }
    });
}

async function runCampaign() {
    const promptText = document.getElementById('promptText').value.trim();
    
    let formData = new FormData();
    
    if (currentMode === 'mining') {
        if (!promptText) {
            alert("Please enter a discovery prompt.");
            return;
        }
        formData.append("prompt", promptText);
        formData.append("limit", document.getElementById('limitMiner').value || 10);
    } else {
        if (!selectedFile) {
            alert("Please upload a file to enrich.");
            return;
        }
        formData.append("file", selectedFile);
        formData.append("limit", document.getElementById('limitEnricher').value || 10);
    }

    // UI Updates - loading
    document.getElementById('emptyState').classList.add('hidden');
    document.getElementById('resultsGrid').classList.add('hidden');
    document.getElementById('loader').classList.remove('hidden');
    document.getElementById('statusIndicator').classList.add('loading');
    document.getElementById('statusText').innerText = "Analyzing Leads (Layers 1-4)...";
    
    const startTime = Date.now();
    let durationInterval = setInterval(() => {
        const secs = Math.floor((Date.now() - startTime) / 1000);
        document.getElementById('statLatency').innerText = `${secs}s`;
    }, 1000);

    try {
        const response = await fetch('/api/campaign', {
            method: 'POST',
            body: formData
        });

        clearInterval(durationInterval);

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || "Processing failed");
        }

        const data = await response.json();
        
        // Update Stats
        document.getElementById('statTotal').innerText = data.summary.total_rows;
        
        let waCount = 0;
        data.results.forEach(r => {
            if (r.business_profile?.whatsapp_status === "DETECTED") waCount++;
        });
        document.getElementById('statSuccess').innerText = waCount;
        
        document.getElementById('statLatency').innerText = `${Math.round(data.summary.processing_time_sec)}s`;

        renderGrid(data.results);

        document.getElementById('statusText').innerText = "System Ready";
        document.getElementById('statusIndicator').classList.remove('loading');
        document.getElementById('loader').classList.add('hidden');
        document.getElementById('resultsGrid').classList.remove('hidden');

    } catch (err) {
        clearInterval(durationInterval);
        alert(`Error: ${err.message}`);
        document.getElementById('statusText').innerText = "System Error";
        document.getElementById('statusIndicator').classList.remove('loading');
        document.getElementById('loader').classList.add('hidden');
        document.getElementById('emptyState').classList.remove('hidden');
    }
}

// Store results globally to open modal
let globalResults = [];

function renderGrid(results) {
    globalResults = results;
    const grid = document.getElementById('resultsGrid');
    grid.innerHTML = '';

    results.forEach((row, index) => {
        const profile = row.business_profile || {};
        
        // Category styling
        let catTag = "tag-dead";
        let emoji = "🔴";
        if (profile.category === "STATIC_WEBSITE") { catTag = "tag-static"; emoji = "🟡"; }
        if (profile.category === "FULLY_AUTOMATED") { catTag = "tag-auto"; emoji = "🔵"; }

        // WhatsApp styling
        const isWa = profile.whatsapp_status === "DETECTED";
        
        const cardHtml = `
            <div class="lead-card glass-panel" onclick="openModal(${index})">
                <div class="card-header">
                    <div class="company-info">
                        <h3>${emoji} ${profile.name || "Unknown"}</h3>
                        <div class="address-info">${profile.address || "No address"}</div>
                    </div>
                    <div class="score-badge">${profile.lead_score || 0}/100</div>
                </div>
                
                <div>
                    <span class="category-tag ${catTag}">${profile.category ? profile.category.replace('_', ' ') : 'NO WEBSITE'}</span>
                </div>
                
                <div class="card-body">
                    <div class="data-row">
                        <span class="data-label">Phone</span>
                        <span class="data-value">${profile.phone || 'N/A'}</span>
                    </div>
                    <div class="data-row">
                        <span class="data-label">WhatsApp</span>
                        <span class="data-value ${isWa ? 'wa-detected' : 'wa-none'}">${isWa ? 'VERIFIED ✓' : 'unverified'}</span>
                    </div>
                    <div class="data-row">
                        <span class="data-label">Google Rating</span>
                        <span class="data-value">${profile.google_rating || '0'}⭐ (${profile.google_review_count || 0})</span>
                    </div>
                </div>
            </div>
        `;
        grid.insertAdjacentHTML('beforeend', cardHtml);
    });
}

function openModal(index) {
    const row = globalResults[index];
    const profile = row.business_profile || {};
    const outreach = row.outreach || {};
    
    let linksHtml = '';
    if (profile.website_url) linksHtml += `<a href="${profile.website_url}" target="_blank">🌐 Website</a>`;
    if (profile.instagram_url) linksHtml += `<a href="${profile.instagram_url}" target="_blank">📸 Instagram</a>`;
    if (profile.facebook_url) linksHtml += `<a href="${profile.facebook_url}" target="_blank">📘 Facebook</a>`;
    if (profile.whatsapp_number) linksHtml += `<a href="https://wa.me/${profile.whatsapp_number.replace(/\D/g,'')}" target="_blank">💬 WhatsApp</a>`;

    let reviewsHtml = '';
    if (profile.reviews && profile.reviews.length > 0) {
        profile.reviews.slice(0,3).forEach(r => {
            if (!r) return;
            reviewsHtml += `<div class="review-item">${r.stars || 5}⭐ "${r.text || ''}"</div>`;
        });
    } else {
        reviewsHtml = '<p style="color:var(--text-muted);font-size:0.85rem">No detailed reviews available.</p>';
    }

    const modalBodyHtml = `
        <div class="modal-header">
            <h2 class="modal-title">${profile.name || "Unknown"}</h2>
            <div class="modal-links">
                ${linksHtml}
            </div>
        </div>
        
        <div class="modal-grid">
            <div class="left-col">
                <div class="info-section">
                    <h4>Lead Intelligence Payload</h4>
                    <div class="score-breakdown-code">${JSON.stringify(profile.lead_score_breakdown || {}, null, 2)}</div>
                </div>
                
                <div class="info-section" style="margin-top:24px">
                    <h4>Recent Top Reviews</h4>
                    <div class="review-list">
                        ${reviewsHtml}
                    </div>
                </div>
            </div>
            
            <div class="right-col">
                <div class="info-section">
                    <h4>Generated AI Outreach</h4>
                    <div class="outreach-tabs">
                        <div class="outreach-box">
                            <small style="color:var(--accent-primary);text-transform:uppercase;font-weight:bold;display:block;margin-bottom:8px">💬 WhatsApp Draft</small>
                            ${(outreach.whatsapp || "No WhatsApp draft generated.").replace(/\n/g, '<br>')}
                        </div>
                        <div class="outreach-box" style="margin-top:12px">
                            <small style="color:var(--accent-secondary);text-transform:uppercase;font-weight:bold;display:block;margin-bottom:8px">📸 Instagram Draft</small>
                            ${(outreach.instagram || "No Instagram draft generated.").replace(/\n/g, '<br>')}
                        </div>
                    </div>
                </div>
                
                <div class="info-section" style="margin-top:24px">
                    <h4>Internal Metadata</h4>
                    <div style="font-size:0.85rem;color:var(--text-muted);display:flex;flex-direction:column;gap:4px">
                        <span><strong>Phone Raw:</strong> ${profile.phone_unformatted || 'N/A'}</span>
                        <span><strong>Booking Tech:</strong> ${profile.booking_system || 'None Detected'}</span>
                        <span><strong>Web Status:</strong> ${profile.website_status || 'UNKNOWN'}</span>
                    </div>
                </div>
            </div>
        </div>
    `;

    document.getElementById('modalBody').innerHTML = modalBodyHtml;
    document.getElementById('detailsModal').classList.remove('hidden');
}

function closeModal() {
    document.getElementById('detailsModal').classList.add('hidden');
}