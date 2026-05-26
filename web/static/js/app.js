/* AudioScribe — Frontend Logic */
'use strict';

// ── Waveform animation ─────────────────────────────────────────────────────
function initWaveform() {
  const container = document.getElementById('heroWaveform');
  if (!container) return;
  const bars = 42;
  for (let i = 0; i < bars; i++) {
    const bar = document.createElement('div');
    const h1 = 8 + Math.floor(Math.random() * 12);
    const h2 = 30 + Math.floor(Math.random() * 50);
    bar.style.cssText = `
      width: 3px;
      height: ${h1}px;
      border-radius: 2px;
      background: var(--green);
      opacity: ${0.4 + Math.random() * 0.5};
      animation: wave${i} ${0.6 + Math.random() * 1.2}s ease-in-out infinite alternate;
      animation-delay: ${Math.random() * 1.5}s;
    `;
    container.appendChild(bar);
  }

  if (!document.getElementById('waveKF')) {
    let css = '';
    for (let i = 0; i < bars; i++) {
      const h1 = 8 + Math.floor(Math.random() * 12);
      const h2 = 30 + Math.floor(Math.random() * 50);
      css += `
        #heroWaveform div:nth-child(${i+1}) {
          animation-name: wave${i};
        }
        @keyframes wave${i} {
          from { height: ${h1}px; }
          to   { height: ${h2}px; }
        }
      `;
    }
    const style = document.createElement('style');
    style.id = 'waveKF';
    style.textContent = css;
    document.head.appendChild(style);
  }
}

// ── File state ─────────────────────────────────────────────────────────────
let selectedFile = null;
let currentJobId = null;
let pollInterval = null;

// ── DOM refs ───────────────────────────────────────────────────────────────
const dropzone        = document.getElementById('dropzone');
const dropzoneInner   = dropzone?.querySelector('.dropzone-inner');
const dropzonePreview = document.getElementById('dropzonePreview');
const fileInput       = document.getElementById('fileInput');
const previewName     = document.getElementById('previewName');
const previewSize     = document.getElementById('previewSize');
const previewRemove   = document.getElementById('previewRemove');
const processBtn      = document.getElementById('processBtn');
const processBtnLabel = document.getElementById('processBtnLabel');
const resultsArea     = document.getElementById('resultsArea');
const progressCard    = document.getElementById('progressCard');
const progressStatus  = document.getElementById('progressStatus');
const progressLog     = document.getElementById('progressLog');
const progressBar     = document.getElementById('progressBar');
const resultsCard     = document.getElementById('resultsCard');
const resultsMeta     = document.getElementById('resultsMeta');
const transcriptPreview = document.getElementById('transcriptPreview');
const transcriptText  = document.getElementById('transcriptText');
const downloadList    = document.getElementById('downloadList');
const errorCard       = document.getElementById('errorCard');
const errorMsg        = document.getElementById('errorMsg');
// URL input
const urlInput        = document.getElementById('urlInput');
const urlSubmitBtn    = document.getElementById('urlSubmitBtn');

// ── Drag & drop ───────────────────────────────────────────────────────────
// File input button handler
const chooseFileBtn = document.getElementById('chooseFileBtn');
if (chooseFileBtn) {
  chooseFileBtn.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    fileInput?.click();
  });
}

if (dropzone) {
  dropzone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropzone.classList.add('drag-over');
  });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag-over'));
  dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('drag-over');
    const file = e.dataTransfer.files[0];
    if (file) setFile(file);
  });
  dropzone.addEventListener('click', (e) => {
    if (e.target === previewRemove) return;
    if (!selectedFile) fileInput?.click();
  });
}

fileInput?.addEventListener('change', () => {
  if (fileInput.files[0]) setFile(fileInput.files[0]);
});

previewRemove?.addEventListener('click', (e) => {
  e.stopPropagation();
  clearFile();
});

function setFile(file) {
  const ext = '.' + file.name.split('.').pop().toLowerCase();
  const allowed = ['.mp3','.wav','.m4a','.ogg','.flac','.aac','.opus','.webm',
                   '.mp4','.mkv','.avi','.mov','.wmv','.flv','.m4v'];
  if (!allowed.includes(ext)) {
    showToast(`Unsupported format: ${ext}`, 'error');
    return;
  }
  const maxMB = 50;
  const sizeMB = file.size / (1024 * 1024);
  if (sizeMB > maxMB) {
    showToast(`File too large: ${sizeMB.toFixed(1)} MB (max ${maxMB} MB)`, 'error');
    return;
  }

  selectedFile = file;
  previewName.textContent = file.name;
  previewSize.textContent = `${sizeMB.toFixed(2)} MB`;

  // Estimated processing time: ~1 min per 10 MB (rough heuristic)
  const etaEl = document.getElementById('previewEta');
  if (etaEl) {
    const etaMins = Math.max(1, Math.round(sizeMB / 10));
    etaEl.textContent = `Est. ~${etaMins} min`;
  }

  const isVideo = ['.mp4','.mkv','.avi','.mov','.wmv','.flv','.m4v'].includes(ext);
  dropzone.querySelector('.preview-icon').textContent = isVideo ? '🎬' : '🎵';

  dropzoneInner.style.display = 'none';
  dropzonePreview.style.display = 'flex';

  processBtn.disabled = false;
  processBtnLabel.textContent = `Process "${truncate(file.name, 35)}"`;
  updateModeVisibility();   // update button label with current mode
  if (urlInput && urlInput.value.trim()) {
    urlInput.value = '';
    showToast('URL cleared — using dropped file instead', 'info');
  }
}

function clearFile() {
  selectedFile = null;
  fileInput.value = '';
  dropzoneInner.style.display = '';
  dropzonePreview.style.display = 'none';
  processBtn.disabled = true;
  processBtnLabel.textContent = 'Select a file to begin';
}

// ── Export helpers ────────────────────────────────────────────────────────

// In-memory store for latest job's markdown summary content
let _lastMarkdownContent = null;
let _lastJobData = null;

async function _fetchMarkdownContent(jobId, files) {
  // Find the first .md summary file, or fall back to .txt summary
  const mdFile  = files.find(f => f.includes('summary') && f.endsWith('.md'));
  const txtFile = files.find(f => f.includes('summary') && f.endsWith('.txt'));
  const target  = mdFile || txtFile;
  if (!target) return null;
  try {
    const resp = await fetch(`/api/download/${jobId}/${encodeURIComponent(target)}`);
    if (!resp.ok) return null;
    return await resp.text();
  } catch (_) { return null; }
}

async function exportToNotion() {
  const btn = document.getElementById('notionExportBtn');
  if (!btn) return;
  const origHTML = btn.innerHTML;
  btn.textContent = '⏳ Preparing...';
  btn.disabled = true;

  try {
    if (!_lastMarkdownContent && _lastJobData) {
      _lastMarkdownContent = await _fetchMarkdownContent(_lastJobData.jobId, _lastJobData.files);
    }
    if (!_lastMarkdownContent) {
      showToast('No Markdown summary available. Choose "Both" or "Markdown" format first.', 'error');
      return;
    }

    // Copy to clipboard
    await navigator.clipboard.writeText(_lastMarkdownContent);

    // Show a clear instruction modal overlay
    showNotionModal(_lastMarkdownContent);

  } catch (err) {
    if (err.name === 'NotAllowedError') {
      showToast('Clipboard permission denied. Use "Copy Markdown" instead.', 'error');
    } else {
      showToast('Export failed: ' + err.message, 'error');
    }
  } finally {
    btn.innerHTML = origHTML;
    btn.disabled = false;
  }
}

function showNotionModal(content) {
  // Remove any existing modal
  document.getElementById('notionModal')?.remove();

  const modal = document.createElement('div');
  modal.id = 'notionModal';
  modal.style.cssText = `
    position:fixed;inset:0;z-index:9999;
    background:rgba(0,0,0,0.75);backdrop-filter:blur(4px);
    display:flex;align-items:center;justify-content:center;padding:20px;
  `;
  modal.innerHTML = `
    <div style="
      background:var(--bg-2);border:1px solid var(--border-md);
      border-radius:16px;padding:28px;max-width:480px;width:100%;
      box-shadow:0 24px 64px rgba(0,0,0,0.5);
    ">
      <div style="font-size:28px;margin-bottom:12px">📋 → 🗒️</div>
      <h3 style="font-size:18px;font-weight:700;color:var(--text);margin-bottom:8px">
        Summary copied to clipboard!
      </h3>
      <p style="font-size:14px;color:var(--text-dim);line-height:1.6;margin-bottom:20px">
        Now paste it into Notion:<br>
        <strong>1.</strong> Open <a href="https://notion.so/new" target="_blank" style="color:var(--green)">notion.so/new</a><br>
        <strong>2.</strong> Press <kbd style="background:var(--bg-3);border:1px solid var(--border-md);padding:2px 7px;border-radius:4px;font-size:12px">Ctrl+V</kbd>
        or <kbd style="background:var(--bg-3);border:1px solid var(--border-md);padding:2px 7px;border-radius:4px;font-size:12px">⌘V</kbd> to paste<br>
        <strong>3.</strong> Notion auto-formats Markdown headings &amp; bullets ✨
      </p>
      <div style="display:flex;gap:10px">
        <a href="https://notion.so/new" target="_blank"
          style="flex:1;display:flex;align-items:center;justify-content:center;gap:8px;
          padding:10px;background:var(--green);color:#000;font-weight:700;font-size:14px;
          border-radius:8px;text-decoration:none;">
          Open Notion
        </a>
        <button onclick="document.getElementById('notionModal').remove()"
          style="flex:1;padding:10px;background:var(--bg-3);color:var(--text-dim);
          font-size:14px;border:1px solid var(--border-md);border-radius:8px;cursor:pointer;">
          Close
        </button>
      </div>
    </div>
  `;
  // Close on backdrop click
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);
}

async function copyMarkdown() {
  const btn = document.getElementById('copyMdBtn');
  if (!btn) return;
  try {
    if (!_lastMarkdownContent && _lastJobData) {
      _lastMarkdownContent = await _fetchMarkdownContent(_lastJobData.jobId, _lastJobData.files);
    }
    if (!_lastMarkdownContent) {
      showToast('No Markdown summary available.', 'error'); return;
    }
    await navigator.clipboard.writeText(_lastMarkdownContent);
    btn.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg> Copied!`;
    setTimeout(() => {
      btn.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg> Copy Markdown`;
    }, 2500);
  } catch (_) { showToast('Clipboard access denied.', 'error'); }
}

// ── Theme Toggle ──────────────────────────────────────────────────────────
function toggleTheme() {
  const html   = document.documentElement;
  const isDark = html.getAttribute('data-theme') !== 'light';
  const next   = isDark ? 'light' : 'dark';
  html.setAttribute('data-theme', next === 'dark' ? '' : 'light');
  document.getElementById('themeIcon').textContent  = next === 'light' ? '🌙' : '☀️';
  document.getElementById('themeLabel').textContent = next === 'light' ? 'Dark' : 'Light';
  try { sessionStorage.setItem('audioscribe_theme', next); } catch(_) {}
}

function initTheme() {
  try {
    const saved = sessionStorage.getItem('audioscribe_theme');
    if (saved === 'light') {
      document.documentElement.setAttribute('data-theme', 'light');
      const icon  = document.getElementById('themeIcon');
      const label = document.getElementById('themeLabel');
      if (icon)  icon.textContent  = '🌙';
      if (label) label.textContent = 'Dark';
    }
  } catch(_) {}
}

// ── Session History ───────────────────────────────────────────────────────
// Stored in memory (cleared on tab close) — no localStorage needed
const _sessionHistory = [];   // [{name, lang, files, jobId, ts}]

function addToHistory(data, sourceName) {
  const entry = {
    name:   sourceName || 'Untitled',
    lang:   data.lang_name || data.detected_lang || '?',
    files:  data.files || [],
    jobId:  currentJobId,
    ts:     new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}),
  };
  _sessionHistory.unshift(entry);
  if (_sessionHistory.length > 10) _sessionHistory.pop();
  renderHistory();
  // Make sure the results area is visible so history shows
  if (resultsArea) resultsArea.style.display = 'block';
}

function renderHistory() {
  const panel = document.getElementById('historyPanel');
  const list  = document.getElementById('historyList');
  if (!panel || !list || _sessionHistory.length === 0) return;

  panel.style.display = 'block';
  list.innerHTML = '';

  _sessionHistory.forEach(entry => {
    const isVideo = /mp4|mkv|avi|mov/i.test(entry.name);
    const icon = entry.name.startsWith('http') ? '🔗' : (isVideo ? '🎬' : '🎵');

    const zipFile = entry.files.find(f => f.endsWith('.zip'));
    const dlHref  = zipFile
      ? `/api/download/${entry.jobId}/${encodeURIComponent(zipFile)}`
      : '#';

    const item = document.createElement('div');
    item.className = 'history-item';
    item.innerHTML = `
      <span class="history-icon">${icon}</span>
      <div class="history-info">
        <div class="history-name">${truncate(entry.name, 45)}</div>
        <div class="history-meta">${entry.ts} · ${entry.lang}</div>
      </div>
      ${zipFile ? `<a class="history-dl" href="${dlHref}" download="${zipFile}">↓ ZIP</a>` : ''}
    `;
    list.appendChild(item);
  });
}

function clearHistory() {
  _sessionHistory.length = 0;
  const panel = document.getElementById('historyPanel');
  if (panel) panel.style.display = 'none';
}

// ── Rate limit display ────────────────────────────────────────────────────
async function fetchLimits() {
  try {
    const r = await fetch('/api/limits');
    if (!r.ok) return;
    const d = await r.json();
    const el = document.getElementById('rateLimitInfo');
    if (!el) return;
    if (d.remaining === 0) {
      const mins = Math.ceil(d.reset_in_seconds / 60);
      el.innerHTML = `⚠️ Free limit reached. Resets in <strong>${mins} min</strong> — or add your own key below.`;
      el.className = 'rate-limit-warn';
    } else {
      el.innerHTML = `Free tier: <strong>${d.remaining}/${d.limit}</strong> requests remaining this hour.`;
      el.className = 'rate-limit-ok';
    }
  } catch (_) {}
}

// ── Process ───────────────────────────────────────────────────────────────
processBtn?.addEventListener('click', startProcessing);

// ── Mode cards — keep active class in sync with radio state ───────────────
document.querySelectorAll('.mode-card').forEach(card => {
  card.addEventListener('click', () => {
    document.querySelectorAll('.mode-card').forEach(c => c.classList.remove('mode-card--active'));
    card.classList.add('mode-card--active');
    updateModeVisibility();
  });
});

function updateModeVisibility() {
  const mode = document.querySelector('input[name="mode"]:checked')?.value || 'full';

  const outputLangGroup    = document.getElementById('outputLangGroup');
  const summaryDetailGroup = document.getElementById('summaryDetailGroup');
  const subtitleLangGroup  = document.getElementById('subtitleLangGroup');
  const summaryOptionsStep = document.getElementById('summaryOptionsStep');

  const showSummaryLang = mode !== 'subtitles' && mode !== 'transcript';
  const showSummaryOpts = mode === 'full' || mode === 'summary';
  const showSubLang     = mode !== 'summary';

  outputLangGroup?.classList.toggle('hidden', !showSummaryLang);
  summaryDetailGroup?.classList.toggle('hidden', !showSummaryOpts);
  summaryOptionsStep?.classList.toggle('hidden', !showSummaryOpts);
  subtitleLangGroup?.classList.toggle('hidden', !showSubLang);

  // Update process button label
  const modeLabels = {
    full:       '⚡ Full Processing',
    transcript: '📄 Transcript + Subtitles',
    subtitles:  '🎞 Subtitles Only',
    summary:    '🧠 Summary Only',
  };
  if (selectedFile && processBtnLabel) {
    processBtnLabel.textContent = `${modeLabels[mode]}: "${truncate(selectedFile.name, 26)}"`;
  } else if (processBtnLabel && processBtnLabel.textContent !== 'Select a file to begin') {
    processBtnLabel.textContent = processBtnLabel.textContent;
  }
}

// Run once on load to set initial state
updateModeVisibility();

// URL submit — Enter key or button click
urlInput?.addEventListener('keydown', (e) => { if (e.key === 'Enter') startProcessingUrl(); });
urlSubmitBtn?.addEventListener('click', startProcessingUrl);

// Mutex: typing/pasting a URL clears the dropped file
urlInput?.addEventListener('input', () => {
  if (urlInput.value.trim() && selectedFile) {
    clearFile();
    showToast('File removed — using URL instead', 'info');
  }
});

async function startProcessingUrl() {
  const url = urlInput?.value?.trim();
  if (!url) { showToast('Paste a URL first', 'error'); return; }

  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    showToast('URL must start with http:// or https://', 'error');
    return;
  }

  // Build form data — send URL as a text field; backend will detect & download
  const lang = document.querySelector('input[name="lang"]:checked')?.value || 'en';
  const sourceLang = document.querySelector('input[name="source_lang"]:checked')?.value || '';
  const style = document.querySelector('input[name="style"]:checked')?.value || 'both';
  const summaryStyle = document.querySelector('input[name="summary_style"]:checked')?.value || 'detailed';
  const summaryTone = document.querySelector('input[name="summary_tone"]:checked')?.value || 'professional';
  const groqKey = document.querySelector('input[name="groq_key"]')?.value || '';
  const mode = document.querySelector('input[name="mode"]:checked')?.value || 'full';
  const subtitleLangMode = document.querySelector('input[name="subtitle_lang_mode"]:checked')?.value || 'none';
  const subtitleLangs = subtitleLangMode === 'none' ? '' : subtitleLangMode;

  const formData = new FormData();
  formData.append('url', url);
  formData.append('langs', lang);
  formData.append('source_lang', sourceLang);
  formData.append('style', style);
  formData.append('summary_style', summaryStyle);
  formData.append('summary_tone', summaryTone);
  formData.append('mode', mode);
  formData.append('subtitle_langs', subtitleLangs);
  if (groqKey) formData.append('groq_key', groqKey);

  urlSubmitBtn.disabled = true;
  urlSubmitBtn.textContent = 'Processing...';
  resultsArea.style.display = 'block';
  progressCard.style.display = 'block';
  resultsCard.style.display = 'none';
  errorCard.style.display = 'none';
  progressLog.innerHTML = '';
  progressBar.style.width = '5%';
  progressStatus.textContent = 'Downloading from URL...';
  scrollToResults();

  try {
    const response = await fetch('/api/process-url', { method: 'POST', body: formData });
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: 'Request failed' }));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }
    const data = await response.json();
    currentJobId = data.job_id;
    pollInterval = setInterval(pollStatus, 2000);
    pollStatus();
  } catch (err) {
    showError(err.message);
    urlSubmitBtn.disabled = false;
    urlSubmitBtn.textContent = 'Process URL';
    fetchLimits();
  }
}

async function startProcessing() {
  if (!selectedFile) return;

  const lang = document.querySelector('input[name="lang"]:checked')?.value || 'en';
  const sourceLang = document.querySelector('input[name="source_lang"]:checked')?.value || '';
  const style = document.querySelector('input[name="style"]:checked')?.value || 'both';
  const summaryStyle = document.querySelector('input[name="summary_style"]:checked')?.value || 'detailed';
  const summaryTone = document.querySelector('input[name="summary_tone"]:checked')?.value || 'professional';
  const groqKey = document.querySelector('input[name="groq_key"]')?.value || '';
  const mode = document.querySelector('input[name="mode"]:checked')?.value || 'full';
  const subtitleLangMode = document.querySelector('input[name="subtitle_lang_mode"]:checked')?.value || 'none';
  const subtitleLangs = subtitleLangMode === 'none' ? '' : subtitleLangMode;

  const formData = new FormData();
  formData.append('file', selectedFile);
  formData.append('langs', lang);
  formData.append('source_lang', sourceLang);
  formData.append('style', style);
  formData.append('summary_style', summaryStyle);
  formData.append('summary_tone', summaryTone);
  formData.append('mode', mode);
  formData.append('subtitle_langs', subtitleLangs);
  if (groqKey) formData.append('groq_key', groqKey);

  // UI: switch to processing mode
  processBtn.disabled = true;
  processBtnLabel.textContent = 'Uploading...';
  resultsArea.style.display = 'block';
  progressCard.style.display = 'block';
  resultsCard.style.display = 'none';
  errorCard.style.display = 'none';
  progressLog.innerHTML = '';
  progressBar.style.width = '5%';
  progressStatus.textContent = 'Uploading file...';

  scrollToResults();

  try {
    const response = await fetch('/api/process', { method: 'POST', body: formData });
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: 'Upload failed' }));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }
    const data = await response.json();
    currentJobId = data.job_id;
    processBtnLabel.textContent = 'Processing...';
    pollInterval = setInterval(pollStatus, 2000);
    pollStatus();
  } catch (err) {
    showError(err.message);
    processBtn.disabled = false;
    processBtnLabel.textContent = selectedFile ? `Process "${truncate(selectedFile.name,35)}"` : 'Select a file to begin';
    fetchLimits();
  }
}

async function pollStatus() {
  if (!currentJobId) return;
  try {
    const response = await fetch(`/api/status/${currentJobId}`);
    const data = await response.json();

    // Update log
    const existingLines = progressLog.querySelectorAll('.log-line').length;
    const newLines = data.progress || [];
    newLines.slice(existingLines).forEach(line => {
      const div = document.createElement('div');
      div.className = 'log-line';
      div.textContent = line;
      progressLog.appendChild(div);
      progressLog.scrollTop = progressLog.scrollHeight;
    });

    // Accurate progress bar based on pipeline stage keywords
    const lastMsg = (newLines[newLines.length - 1] || '').toLowerCase();
    let pct = 5;
    if (lastMsg.includes('download'))          pct = 10;
    else if (lastMsg.includes('validat') || lastMsg.includes('processing audio')) pct = 18;
    else if (lastMsg.includes('chunk'))         pct = 25;
    else if (lastMsg.includes('transcrib') && lastMsg.includes('%')) {
      // Extract percentage from messages like "Transcribing 3/12 (25%)"
      const m = lastMsg.match(/\((\d+)%\)/);
      pct = m ? 25 + Math.round(parseInt(m[1]) * 0.45) : 40;
    } else if (lastMsg.includes('transcrib'))   pct = 30;
    else if (lastMsg.includes('transcription complete')) pct = 72;
    else if (lastMsg.includes('summariz'))      pct = 78;
    else if (lastMsg.includes('packag'))        pct = 92;

    progressBar.style.width = `${pct}%`;
    progressStatus.textContent = newLines[newLines.length - 1] || 'Processing...';

    if (data.status === 'done') {
      clearInterval(pollInterval);
      progressBar.style.width = '100%';
      fetchLimits();   // refresh remaining count after job
      setTimeout(() => showResults(data), 400);
    } else if (data.status === 'error') {
      clearInterval(pollInterval);
      showError(data.error || 'Processing failed. Please try again.');
    }
  } catch (err) {
    console.error('Poll failed:', err);
  }
}

function showResults(data) {
  progressCard.style.display = 'none';
  resultsCard.style.display = 'block';

  const lang = data.lang_name || data.detected_lang || 'Unknown';
  resultsMeta.textContent = `Detected language: ${lang}`;

  if (data.transcript_preview) {
    transcriptPreview.style.display = 'block';
    transcriptText.textContent = data.transcript_preview + (data.transcript_preview.length >= 500 ? '...' : '');
  }

  downloadList.innerHTML = '';
  const files = data.files || [];

  // Sort: ZIP first, then transcript, then summaries
  const sorted = [...files].sort((a, b) => {
    if (a.endsWith('.zip')) return -1;
    if (b.endsWith('.zip')) return 1;
    if (a.includes('transcript')) return -1;
    if (b.includes('transcript')) return 1;
    return 0;
  });

  sorted.forEach(filename => {
    const item = document.createElement('a');
    item.className = 'download-item';
    item.href = `/api/download/${currentJobId}/${encodeURIComponent(filename)}`;
    item.download = filename;

    const icon = filename.endsWith('.zip') ? '📦' :
                 filename.includes('transcript') ? '📄' :
                 filename.endsWith('.md') ? '📋' : '📝';

    const label = filename.endsWith('.zip') ? 'All outputs (ZIP bundle)' :
                  filename.includes('transcript') ? 'Full transcript' :
                  filename.includes('_ar-eg.') ? 'Egyptian Arabic summary' :
                  filename.includes('_ar.') ? 'Arabic summary' :
                  filename.includes('_en.') ? 'English summary' :
                  filename;

    item.innerHTML = `
      <span class="download-item-icon">${icon}</span>
      <span class="download-item-name">${label}</span>
      <span class="download-item-size">${filename.endsWith('.zip') ? 'ZIP' : filename.split('.').pop().toUpperCase()}</span>
      <span class="download-arrow">↓</span>
    `;
    downloadList.appendChild(item);
  });

  // Cache for export functions
  _lastMarkdownContent = null;   // reset — will lazy-load on export click
  _lastJobData = { jobId: currentJobId, files: data.files || [] };

  // Show export actions if there are files
  const exportActions = document.getElementById('exportActions');
  if (exportActions && (data.files || []).length > 0) {
    exportActions.style.display = 'block';
  }

  // Add to session history
  const sourceName = selectedFile ? selectedFile.name : (urlInput?.value?.trim() || 'Unknown');
  addToHistory(data, sourceName);

  processBtn.disabled = false;
  processBtnLabel.textContent = 'Process Another File';
  processBtn.onclick = resetUI;
}

function showError(message) {
  progressCard.style.display = 'none';
  errorCard.style.display = 'flex';

  // Rate limit — show a friendlier UI with instructions
  const isRateLimit = /rate.limit|429|quota|token/i.test(message);
  if (isRateLimit) {
    // Extract wait time if present e.g. "try again in 21m21.312s"
    const m = message.match(/(\d+)m[\d.]+s/);
    const waitStr = m ? `~${parseInt(m[1]) + 1} minutes` : 'a few minutes';
    errorMsg.innerHTML =
      `<strong>⏳ Summarization rate limit reached</strong><br><br>` +
      `The shared API key hit its daily limit. Please wait <strong>${waitStr}</strong> or add your own free key:<br><br>` +
      `<ol style="text-align:left;padding-left:1.2em;margin:8px 0">` +
      `<li>Visit <a href="https://console.groq.com" target="_blank" style="color:var(--green)">console.groq.com</a></li>` +
      `<li>Sign up → API Keys → Create key</li>` +
      `<li>Paste it in the <em>Your Groq API Key</em> field below and retry</li>` +
      `</ol>`;
  } else {
    errorMsg.textContent = message;
  }

  processBtn.disabled = false;
  processBtnLabel.textContent = 'Try Again';
  processBtn.onclick = resetUI;
  if (urlSubmitBtn) { urlSubmitBtn.disabled = false; urlSubmitBtn.textContent = 'Process URL'; }
}

function resetUI() {
  if (pollInterval) clearInterval(pollInterval);

  const jobToDelete = currentJobId;
  currentJobId = null;

  if (jobToDelete) {
    fetch(`/api/job/${jobToDelete}`, { method: 'DELETE' }).catch(() => {});
  }

  // Hide results/progress/error but keep history visible
  resultsArea.style.display = 'none';
  progressCard.style.display = 'none';
  resultsCard.style.display = 'none';
  errorCard.style.display = 'none';
  const exportActions = document.getElementById('exportActions');
  if (exportActions) exportActions.style.display = 'none';
  _lastMarkdownContent = null;
  _lastJobData = null;
  clearFile();

  if (urlInput) urlInput.value = '';
  if (urlSubmitBtn) { urlSubmitBtn.disabled = false; urlSubmitBtn.textContent = 'Process URL'; }

  processBtn.onclick = startProcessing;

  // Re-show history panel if it has entries
  if (_sessionHistory.length > 0) {
    const panel = document.getElementById('historyPanel');
    if (panel) panel.style.display = 'block';
  }
}

// ── Utilities ─────────────────────────────────────────────────────────────
function scrollToResults() {
  setTimeout(() => {
    resultsArea?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, 200);
}

function truncate(str, max) {
  return str.length > max ? str.slice(0, max) + '…' : str;
}

function showToast(message, type = 'info', duration = 4000) {
  const toast = document.createElement('div');
  toast.style.cssText = `
    position: fixed; bottom: 24px; right: 24px; z-index: 9998;
    background: ${type === 'error' ? '#ff5050' : type === 'info' ? '#4f9ef8' : '#39ff8a'};
    color: ${type === 'error' || type === 'info' ? '#fff' : '#000'};
    padding: 12px 20px; border-radius: 8px;
    font-size: 14px; font-weight: 600; max-width: 360px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.4);
    animation: slideUp 0.3s ease;
  `;
  toast.textContent = message;

  const style = document.createElement('style');
  style.textContent = `@keyframes slideUp { from { transform: translateY(20px); opacity: 0; } }`;
  document.head.appendChild(style);

  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), duration);
}

// ── Init ───────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  initWaveform();
  fetchLimits();

  // Register service worker for PWA support
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }

  // Smooth scroll for nav links
  document.querySelectorAll('a[href^="#"]').forEach(a => {
    a.addEventListener('click', (e) => {
      const target = document.querySelector(a.getAttribute('href'));
      if (target) {
        e.preventDefault();
        target.scrollIntoView({ behavior: 'smooth' });
      }
    });
  });
});
