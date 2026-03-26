async function uploadFile() {
    const fileInput = document.getElementById("fileInput");
    const statusText = document.getElementById("statusText");
    const statusIndicator = document.getElementById("statusIndicator");
    const resultsBody = document.getElementById("resultsBody");
    const loader = document.getElementById("loader");
    const emptyState = document.getElementById("emptyState");
    
    const file = fileInput.files[0];
    if (!file) {
        alert("Please select a file");
        return;
    }

    const formData = new FormData();
    formData.append("file", file);

    // Initial UI State
    statusText.innerText = "Analyzing Leads...";
    statusIndicator.classList.add("loading");
    resultsBody.innerHTML = "";
    emptyState.classList.add("hidden");
    loader.classList.remove("hidden");

    try {
        const response = await fetch("/api/upload", {
            method: "POST",
            body: formData
        });

        if (!response.ok) throw new Error("Processing failed");

        const data = await response.json();
        
        // Update Stats
        document.getElementById("statTotal").innerText = data.summary.total_rows;
        document.getElementById("statSuccess").innerText = data.summary.success;
        document.getElementById("statLatency").innerText = Math.round(data.summary.processing_time_sec) + "s";

        renderResults(data.results);

        statusText.innerText = "System Ready";
        statusIndicator.classList.remove("loading");
        loader.classList.add("hidden");

    } catch (error) {
        statusText.innerText = "Processing Error";
        statusIndicator.classList.remove("loading");
        loader.classList.add("hidden");
        emptyState.classList.remove("hidden");
        alert(error.message);
    }
}

function renderResults(results) {
    const body = document.getElementById("resultsBody");
    body.innerHTML = "";

    results.forEach(row => {
        const tr = document.createElement("tr");
        
        // Handle potential null/undefined from BusinessProfile
        const profile = row.business_profile || {};
        const contact = row.contact || {};
        const outreach = row.outreach || {};

        tr.innerHTML = `
            <td>
                <div class="company-cell">
                    <span class="company-name">${profile.company_name || row.input.company_name}</span>
                    <span class="industry-tag">${profile.industry || 'Unknown Industry'}</span>
                </div>
            </td>
            <td>
                <div class="intel-cell">
                    <p class="intel-desc">${profile.description || 'No description extracted.'}</p>
                    <div class="intel-badges">
                        ${profile.size_signals?.employee_estimate ? `<span class="badge">👥 ${profile.size_signals.employee_estimate}</span>` : ''}
                        ${profile.size_signals?.branches ? `<span class="badge">📍 ${profile.size_signals.branches} Branches</span>` : ''}
                        ${profile.digital_presence?.website ? `<span class="badge">🌐 Has Website</span>` : '<span class="badge opacity-50">⚪ No Website</span>'}
                    </div>
                </div>
            </td>
            <td>
                <div class="contact-cell">
                    ${contact.phone ? `<div class="contact-item"><span>📞</span> ${contact.phone}</div>` : ''}
                    ${contact.email ? `<div class="contact-item"><span>✉️</span> ${contact.email}</div>` : ''}
                    ${contact.whatsapp ? `<div class="contact-item"><a href="https://wa.me/${contact.whatsapp.replace(/\D/g, '')}" target="_blank" style="text-decoration:none"><span>💬</span> WhatsApp</a></div>` : ''}
                    ${!contact.phone && !contact.email && !contact.whatsapp ? '<div class="contact-item opacity-50">Not Found</div>' : ''}
                    ${contact.sources?.length > 0 ? `<a href="${contact.sources[0].url}" target="_blank" class="source-link">View Source ↗</a>` : ''}
                </div>
            </td>
            <td>
                <div class="outreach-cell">
                    <div class="message-box">
                        ${outreach.message || 'Waiting for context...'}
                        <button class="copy-btn" onclick="copyText(this, '${(outreach.message || "").replace(/'/g, "\\'")}')">Copy</button>
                    </div>
                </div>
            </td>
        `;
        body.appendChild(tr);
    });
}

function copyText(btn, text) {
    navigator.clipboard.writeText(text);
    const originalText = btn.innerText;
    btn.innerText = "Copied!";
    setTimeout(() => btn.innerText = originalText, 2000);
}