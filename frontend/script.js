// frontend/script.js — Command Center Logic (v3.0)

let currentMode = 'mining';
let selectedFile = null;
let overlayInterval = null;

function switchMode(mode) {
    currentMode = mode;
    document.querySelectorAll('.cmd-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.mode === mode);
    });
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

// Drag & Drop
const dropZone = document.getElementById('dropZone');
if (dropZone) {
    dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
    dropZone.addEventListener('dragleave', () => { dropZone.classList.remove('dragover'); });
    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        if (e.dataTransfer.files.length) {
            document.getElementById('fileInput').files = e.dataTransfer.files;
            handleFileSelect(document.getElementById('fileInput'));
        }
    });
}

// Pipeline overlay step animation
const STEP_LABELS = [
    '⬡ Layer 1: Mining Google Maps',
    '⬡ Layer 2: Scanning Websites',
    '⬡ Layer 3: AI Research'
];
const STEP_SUBTITLES = [
    'Querying Apify Maps Actor...',
    'Deep-scanning business websites...',
    'Analyzing reviews for pain points...'
];

function showOverlay(prompt) {
    const overlay = document.getElementById('pipelineOverlay');
    overlay.classList.remove('hidden');
    document.getElementById('overlayTitle').textContent = `Analyzing: "${prompt || 'Uploaded File'}"`;

    // Reset steps
    for (let i = 1; i <= 3; i++) {
        const el = document.getElementById(`ov${i}`);
        el.className = 'ov-step';
        el.textContent = STEP_LABELS[i - 1];
    }
    document.getElementById('ov1').classList.add('active');

    // Animate steps over time
    const stepTimings = [0, 20000, 60000]; // rough timing hints for 3 steps
    stepTimings.forEach((t, i) => {
        setTimeout(() => {
            for (let j = 1; j <= 3; j++) {
                const el = document.getElementById(`ov${j}`);
                if (j < i + 1) { el.className = 'ov-step done'; el.textContent = '✓ ' + STEP_LABELS[j - 1].slice(2); }
                else if (j === i + 1) { el.className = 'ov-step active'; }
                else { el.className = 'ov-step'; }
            }
            document.getElementById('overlaySubtitle').textContent = STEP_SUBTITLES[i] || '';
        }, t);
    });

    // Timer
    const start = Date.now();
    overlayInterval = setInterval(() => {
        const secs = Math.floor((Date.now() - start) / 1000);
        document.getElementById('overlayTimer').textContent = `${secs}s`;
    }, 1000);

    // Update status
    document.getElementById('statusIndicator').classList.add('loading');
    document.getElementById('statusText').innerText = 'Pipeline Running...';
}

function hideOverlay() {
    clearInterval(overlayInterval);
    document.getElementById('pipelineOverlay').classList.add('hidden');
    document.getElementById('statusIndicator').classList.remove('loading');
    document.getElementById('statusText').innerText = 'System Ready';
}

async function runCampaign() {
    const promptText = document.getElementById('promptText').value.trim();
    let formData = new FormData();

    if (currentMode === 'mining') {
        if (!promptText) { alert('Please enter a discovery prompt.'); return; }
        formData.append('prompt', promptText);
        formData.append('limit', document.getElementById('limitMiner').value || 10);
    } else {
        if (!selectedFile) { alert('Please upload a file to enrich.'); return; }
        formData.append('file', selectedFile);
        formData.append('limit', document.getElementById('limitEnricher').value || 10);
    }

    showOverlay(currentMode === 'mining' ? promptText : selectedFile.name);
    document.getElementById('runCampaignBtn').disabled = true;

    try {
        const response = await fetch('/api/campaign', { method: 'POST', body: formData });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || 'Processing failed');
        }

        const data = await response.json();

        // Store results in sessionStorage and navigate to results page
        sessionStorage.setItem('ois_results', JSON.stringify(data));
        sessionStorage.setItem('ois_query', currentMode === 'mining' ? promptText : selectedFile.name);

        hideOverlay();
        window.location.href = '/results.html';

    } catch (err) {
        hideOverlay();
        document.getElementById('runCampaignBtn').disabled = false;
        alert(`Pipeline Error: ${err.message}`);
        document.getElementById('statusText').innerText = 'System Error';
    }
}