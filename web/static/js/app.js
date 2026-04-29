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

  const isVideo = ['.mp4','.mkv','.avi','.mov','.wmv','.flv','.m4v'].includes(ext);
  dropzone.querySelector('.preview-icon').textContent = isVideo ? '🎬' : '🎵';

  dropzoneInner.style.display = 'none';
  dropzonePreview.style.display = 'flex';

  processBtn.disabled = false;
  processBtnLabel.textContent = `Process "${truncate(file.name, 35)}"`;
}

function clearFile() {
  selectedFile = null;
  fileInput.value = '';
  dropzoneInner.style.display = '';
  dropzonePreview.style.display = 'none';
  processBtn.disabled = true;
  processBtnLabel.textContent = 'Select a file to begin';
}

// ── Process ───────────────────────────────────────────────────────────────
processBtn?.addEventListener('click', startProcessing);

async function startProcessing() {
  if (!selectedFile) return;

  const lang = document.querySelector('input[name="lang"]:checked')?.value || 'en';
  const sourceLang = document.querySelector('input[name="source_lang"]:checked')?.value || '';
  const style = document.querySelector('input[name="style"]:checked')?.value || 'both';
  const summaryStyle = document.querySelector('input[name="summary_style"]:checked')?.value || 'detailed';
  const summaryTone = document.querySelector('input[name="summary_tone"]:checked')?.value || 'professional';
  const groqKey = document.querySelector('input[name="groq_key"]')?.value || '';

  const formData = new FormData();
  formData.append('file', selectedFile);
  formData.append('langs', lang);
  formData.append('source_lang', sourceLang);
  formData.append('style', style);
  formData.append('summary_style', summaryStyle);
  formData.append('summary_tone', summaryTone);
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
    pollStatus(); // immediate first poll
  } catch (err) {
    showError(err.message);
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
    });

    // Update progress bar
    const progress = Math.min(10 + (newLines.length / 8) * 85, 95);
    progressBar.style.width = `${progress}%`;
    progressStatus.textContent = newLines[newLines.length - 1] || 'Processing...';

    if (data.status === 'done') {
      clearInterval(pollInterval);
      progressBar.style.width = '100%';
      setTimeout(() => showResults(data), 400);
    } else if (data.status === 'error') {
      clearInterval(pollInterval);
      showError(data.error || 'Processing failed');
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

  processBtn.disabled = false;
  processBtnLabel.textContent = 'Process Another File';
  processBtn.onclick = resetUI;
}

function showError(message) {
  progressCard.style.display = 'none';
  errorCard.style.display = 'flex';
  errorMsg.textContent = message;
  processBtn.disabled = false;
  processBtnLabel.textContent = 'Try Again';
  processBtn.onclick = resetUI;
}

function resetUI() {
  if (pollInterval) clearInterval(pollInterval);
  currentJobId = null;

  // Clean up job on server
  if (currentJobId) fetch(`/api/job/${currentJobId}`, { method: 'DELETE' }).catch(() => {});

  resultsArea.style.display = 'none';
  clearFile();

  processBtn.onclick = startProcessing;
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

function showToast(message, type = 'info') {
  const toast = document.createElement('div');
  toast.style.cssText = `
    position: fixed; bottom: 24px; right: 24px; z-index: 9998;
    background: ${type === 'error' ? '#ff5050' : '#39ff8a'};
    color: ${type === 'error' ? '#fff' : '#000'};
    padding: 12px 20px; border-radius: 8px;
    font-size: 14px; font-weight: 600;
    box-shadow: 0 8px 32px rgba(0,0,0,0.4);
    animation: slideUp 0.3s ease;
  `;
  toast.textContent = message;

  const style = document.createElement('style');
  style.textContent = `@keyframes slideUp { from { transform: translateY(20px); opacity: 0; } }`;
  document.head.appendChild(style);

  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

// ── Init ───────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initWaveform();

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
