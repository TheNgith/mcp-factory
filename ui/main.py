"""
ui/main.py – MCP Factory Web UI
Serves a single-page HTML frontend and proxies /api/* calls to the
pipeline container (PIPELINE_URL env var).
"""

from __future__ import annotations

import os
import logging
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp_factory.ui")

# ── Optional API-key guard ─────────────────────────────────────────────────
# Set UI_API_KEY env var on the container to require a shared key on every
# request.  Leave unset (or empty) for open access during local development.
UI_API_KEY   = os.getenv("UI_API_KEY", "")
PIPELINE_KEY = os.getenv("PIPELINE_KEY", "")  # forwarded as X-Pipeline-Key

PIPELINE_URL = os.getenv(
    "PIPELINE_URL",
    "https://mcp-factory-pipeline.icycoast-8ddfa278.eastus.azurecontainerapps.io",
).rstrip("/")

app = FastAPI(title="MCP Factory UI", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _api_key_guard(request: Request, call_next):
    """If UI_API_KEY is set, every non-health request must present it."""
    if not UI_API_KEY:
        return await call_next(request)
    # always allow health + static assets + Copilot Extension endpoint
    if request.url.path in ("/health", "/favicon.ico", "/copilot"):
        return await call_next(request)
    # check header or query param
    provided = (
        request.headers.get("X-UI-Key", "")
        or request.query_params.get("apikey", "")
    )
    # For browser page load we embed the key via a cookie set by the login form
    if not provided:
        provided = request.cookies.get("ui_api_key", "")
    if provided != UI_API_KEY:
        from fastapi.responses import HTMLResponse as _HR
        login_html = """
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>MCP Factory – Sign In</title>
<style>body{background:#0f1117;color:#e2e6f3;font-family:'Segoe UI',system-ui,sans-serif;
  display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
  .box{background:#1a1d27;border:1px solid #2e3254;border-radius:10px;padding:36px 40px;width:340px}
  h2{margin:0 0 24px;color:#5b8ef0;font-size:1.2rem}input{width:100%;background:#22263a;
  border:1px solid #2e3254;border-radius:7px;color:#e2e6f3;font-size:.9rem;padding:10px 13px;
  outline:none;box-sizing:border-box;margin-bottom:16px}input:focus{border-color:#5b8ef0}
  button{background:#5b8ef0;color:#fff;border:none;border-radius:7px;padding:10px 24px;
  font-size:.9rem;font-weight:600;cursor:pointer;width:100%}.err{color:#f09090;font-size:.82rem;margin-top:8px}
</style></head><body><div class="box">
<h2>⚙ MCP Factory — Access Required</h2>
<form id="f" onsubmit="return submit()">
  <input type="password" id="k" placeholder="Enter API key" autofocus />
  <button type="submit">Continue</button>
  <div class="err" id="err"></div>
</form></div>
<script>
function submit(){
  const k=document.getElementById('k').value;
  document.cookie='ui_api_key='+encodeURIComponent(k)+';path=/;SameSite=Strict';
  fetch('/',{headers:{'X-UI-Key':k}}).then(r=>{
    if(r.ok){location.reload();}else{document.getElementById('err').textContent='Invalid key.';}
  });
  return false;
}
</script></body></html>"""
        return _HR(content=login_html, status_code=401)
    return await call_next(request)


# ── Single-page frontend ───────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>MCP Factory</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg: #0f1117;
      --surface: #1a1d27;
      --surface2: #22263a;
      --border: #2e3254;
      --accent: #5b8ef0;
      --accent2: #7c5bf0;
      --green: #3ecf8e;
      --red: #f05b5b;
      --text: #e2e6f3;
      --muted: #7a80a0;
      --radius: 10px;
      --font: 'Segoe UI', system-ui, sans-serif;
    }

    body {
      background: var(--bg);
      color: var(--text);
      font-family: var(--font);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }

    /* ── Header ────────────────────────────────── */
    header {
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 14px 32px;
      display: flex;
      align-items: center;
      gap: 12px;
    }
    header .logo { font-size: 1.4rem; font-weight: 700; color: var(--accent); }
    header .tagline { font-size: 0.85rem; color: var(--muted); }

    /* ── Step bar ──────────────────────────────── */
    .step-bar {
      display: flex;
      justify-content: center;
      gap: 0;
      padding: 28px 32px 0;
    }
    .step-item {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 0.82rem;
      color: var(--muted);
      transition: color .2s;
    }
    .step-item.active { color: var(--accent); }
    .step-item.done   { color: var(--green); }
    .step-dot {
      width: 28px; height: 28px;
      border-radius: 50%;
      border: 2px solid var(--border);
      display: flex; align-items: center; justify-content: center;
      font-size: 0.75rem; font-weight: 700;
      transition: all .2s;
      flex-shrink: 0;
    }
    .step-item.active .step-dot { border-color: var(--accent); color: var(--accent); }
    .step-item.done   .step-dot { border-color: var(--green); background: var(--green); color: #000; }
    .step-connector {
      height: 2px; width: 60px;
      background: var(--border);
      align-self: center;
      transition: background .2s;
    }
    .step-connector.done { background: var(--green); }

    /* ── Main layout ───────────────────────────── */
    main {
      flex: 1;
      max-width: 860px;
      width: 100%;
      margin: 0 auto;
      padding: 32px 24px 64px;
    }

    /* ── Section cards ──────────────────────────── */
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 28px 32px;
      margin-top: 28px;
    }
    .card-title {
      font-size: 1.1rem; font-weight: 600;
      display: flex; align-items: center; gap: 10px;
      margin-bottom: 20px;
    }
    .badge {
      background: var(--accent2);
      color: #fff; font-size: 0.7rem; font-weight: 700;
      padding: 2px 8px; border-radius: 20px;
    }

    /* ── Form elements ──────────────────────────── */
    label { display: block; font-size: 0.85rem; color: var(--muted); margin-bottom: 6px; }

    .input, textarea, select {
      width: 100%;
      background: var(--surface2);
      border: 1px solid var(--border);
      border-radius: 7px;
      color: var(--text);
      font-family: var(--font);
      font-size: 0.9rem;
      padding: 10px 13px;
      transition: border-color .15s;
      outline: none;
    }
    .input:focus, textarea:focus { border-color: var(--accent); }
    textarea { resize: vertical; min-height: 72px; }

    .file-drop {
      border: 2px dashed var(--border);
      border-radius: var(--radius);
      padding: 32px;
      text-align: center;
      cursor: pointer;
      transition: border-color .2s, background .2s;
      position: relative;
    }
    .file-drop:hover, .file-drop.dragover {
      border-color: var(--accent);
      background: rgba(91,142,240,.06);
    }
    .file-drop input[type="file"] {
      position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%;
    }
    .file-drop .drop-icon { font-size: 2rem; margin-bottom: 8px; }
    .file-drop .drop-text { color: var(--muted); font-size: 0.88rem; }
    .file-name { margin-top: 10px; font-size: 0.85rem; color: var(--green); font-weight: 600; }

    .form-row { margin-bottom: 18px; }

    /* ── Buttons ────────────────────────────────── */
    .btn {
      display: inline-flex; align-items: center; gap: 7px;
      padding: 10px 22px; border-radius: 7px; border: none;
      font-family: var(--font); font-size: 0.9rem; font-weight: 600;
      cursor: pointer; transition: opacity .15s, transform .1s;
    }
    .btn:disabled { opacity: .4; cursor: not-allowed; }
    .btn:hover:not(:disabled) { opacity: .88; }
    .btn:active:not(:disabled) { transform: scale(.97); }

    .btn-primary { background: var(--accent); color: #fff; }
    .btn-secondary { background: var(--surface2); color: var(--text); border: 1px solid var(--border); }
    .btn-success { background: var(--green); color: #000; }
    .btn-danger { background: var(--red); color: #fff; }

    .btn-row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 20px; }

    /* ── Spinner ────────────────────────────────── */
    .spinner {
      display: inline-block; width: 18px; height: 18px;
      border: 2px solid rgba(255,255,255,.3);
      border-top-color: #fff;
      border-radius: 50%;
      animation: spin 0.7s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    /* ── Alert bar ──────────────────────────────── */
    .alert {
      padding: 10px 14px; border-radius: 7px; font-size: 0.86rem;
      margin-top: 14px; display: none;
    }
    .alert.show { display: block; }
    .alert-error { background: rgba(240,91,91,.15); border: 1px solid rgba(240,91,91,.4); color: #f09090; }
    .alert-info  { background: rgba(91,142,240,.12); border: 1px solid rgba(91,142,240,.35); color: #a0b8f0; }

    /* ── Invocables list ────────────────────────── */
    .inv-toolbar {
      display: flex; align-items: center; gap: 12px;
      margin-bottom: 14px; font-size: 0.84rem;
    }
    .inv-toolbar .count { color: var(--muted); margin-left: auto; }

    .inv-list {
      max-height: 420px;
      overflow-y: auto;
      border: 1px solid var(--border);
      border-radius: 8px;
    }
    .inv-item {
      display: flex; align-items: flex-start; gap: 12px;
      padding: 12px 16px;
      border-bottom: 1px solid var(--border);
      cursor: pointer;
      transition: background .1s;
    }
    .inv-item:last-child { border-bottom: none; }
    .inv-item:hover { background: var(--surface2); }
    .inv-item input[type="checkbox"] {
      width: 16px; height: 16px; margin-top: 3px; flex-shrink: 0;
      accent-color: var(--accent); cursor: pointer;
    }
    .inv-name { font-size: 0.88rem; font-weight: 600; color: var(--accent); font-family: monospace; }
    .inv-sig  { font-size: 0.78rem; color: var(--muted); margin-top: 2px; font-family: monospace; word-break: break-all; }
    .inv-doc  { font-size: 0.8rem; color: var(--text); margin-top: 3px; }
    .inv-tier {
      font-size: 0.68rem; font-weight: 700;
      padding: 1px 7px; border-radius: 12px; flex-shrink: 0; margin-top: 2px;
    }
    .tier-1 { background: rgba(62,207,142,.18); color: var(--green); }
    .tier-2 { background: rgba(91,142,240,.18); color: var(--accent); }
    .tier-3 { background: rgba(122,128,160,.18); color: var(--muted); }

    /* ── Schema preview ─────────────────────────── */
    .json-preview {
      background: #0a0c14;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
      font-family: 'Cascadia Code', 'Fira Code', monospace;
      font-size: 0.78rem;
      color: #a8d0f0;
      max-height: 360px;
      overflow-y: auto;
      white-space: pre;
      margin-top: 8px;
    }

    /* ── Chat ───────────────────────────────────── */
    .chat-window {
      background: #0a0c14;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
      height: 380px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 12px;
      margin-bottom: 14px;
    }
    .chat-msg {
      max-width: 78%;
      border-radius: 8px;
      padding: 10px 14px;
      font-size: 0.87rem;
      line-height: 1.5;
      word-break: break-word;
    }
    .chat-msg.user {
      align-self: flex-end;
      background: var(--accent);
      color: #fff;
    }
    .chat-msg.assistant {
      align-self: flex-start;
      background: var(--surface2);
      color: var(--text);
      border: 1px solid var(--border);
    }
    .chat-msg.tool-call {
      align-self: flex-start;
      background: rgba(124,91,240,.12);
      border: 1px solid rgba(124,91,240,.35);
      color: #c0a8f0;
      font-family: monospace;
      font-size: 0.78rem;
    }
    .chat-empty {
      flex: 1; display: flex; align-items: center; justify-content: center;
      color: var(--muted); font-size: 0.85rem; text-align: center;
    }
    .chat-input-row {
      display: flex; gap: 10px; align-items: flex-end;
    }
    .chat-input-row textarea {
      flex: 1; min-height: 52px; max-height: 140px;
    }

    section.hidden { display: none; }
  </style>
</head>
<body>

<header>
  <span class="logo">⚙ MCP Factory</span>
  <span class="tagline">Binary → MCP tool schema, AI-powered</span>
</header>

<!-- Step indicator -->
<div class="step-bar">
  <div class="step-item active" id="step1-item">
    <div class="step-dot">1</div>
    <span>Upload</span>
  </div>
  <div class="step-connector" id="conn12"></div>
  <div class="step-item" id="step2-item">
    <div class="step-dot">2</div>
    <span>Select</span>
  </div>
  <div class="step-connector" id="conn23"></div>
  <div class="step-item" id="step3-item">
    <div class="step-dot">3</div>
    <span>Generate</span>
  </div>
  <div class="step-connector" id="conn34"></div>
  <div class="step-item" id="step4-item">
    <div class="step-dot">4</div>
    <span>Chat</span>
  </div>
</div>

<main>

  <!-- ═══ Section 2 – Upload ═══════════════════════════════════════════ -->
  <section id="sec-upload">
    <div class="card">
      <div class="card-title">
        <span class="badge">Step 1</span>
        Upload Binary for Analysis
      </div>

      <div class="form-row">
        <label>Binary file (EXE, DLL, SO, PY, …)</label>
        <div class="file-drop" id="file-drop">
          <input type="file" id="file-input"
            accept=".dll,.exe,.py,.js,.rb,.php,.so,.jar,.sql,.ps1,.cmd,.bat,.bin" />
          <div class="drop-icon">📦</div>
          <div class="drop-text">Drag & drop a file here, or click to browse</div>
          <div class="file-name" id="file-name"></div>
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:12px;margin:4px 0 4px">
        <hr style="flex:1;border:none;border-top:1px solid var(--border)" />
        <span style="font-size:.78rem;color:var(--muted);white-space:nowrap">OR — already installed</span>
        <hr style="flex:1;border:none;border-top:1px solid var(--border)" />
      </div>

      <div class="form-row">
        <label>Installed path on the server (e.g. C:\Program Files\AppD\)</label>
        <input class="input" id="dir-path" type="text"
          placeholder="C:\Windows\System32\notepad.exe  or  C:\Program Files\AppD\"
          autocomplete="off" />
        <div style="font-size:.75rem;color:var(--muted);margin-top:4px">
          ⚠ Path must be accessible from the pipeline server. Use file upload for cloud deployments.
        </div>
      </div>
      <div class="form-row">
        <label>Hints / description (optional)</label>
        <textarea id="hints" placeholder="e.g. calculator CLI, zstd compression library…"></textarea>
      </div>

      <div class="alert alert-error" id="upload-error"></div>

      <div class="btn-row">
        <button class="btn btn-primary" id="analyze-btn" disabled>
          Analyze Binary
        </button>
      </div>
    </div>
  </section>

  <!-- ═══ Section 3 – Invocables ════════════════════════════════════ -->
  <section id="sec-invocables" class="hidden">
    <div class="card">
      <div class="card-title">
        <span class="badge">Step 2</span>
        Select Invocables
      </div>

      <div class="inv-toolbar">
        <button class="btn btn-secondary" style="padding:5px 12px;font-size:.8rem" id="sel-all-btn">Select all</button>
        <button class="btn btn-secondary" style="padding:5px 12px;font-size:.8rem" id="sel-none-btn">None</button>
        <span class="count" id="sel-count">0 selected</span>
      </div>

      <div class="inv-list" id="inv-list"></div>

      <div class="form-row" style="margin-top:18px">
        <label>Component name</label>
        <input class="input" id="component-name" type="text" placeholder="my-mcp-component" />
      </div>

      <div class="alert alert-error" id="inv-error"></div>

      <div class="btn-row">
        <button class="btn btn-secondary" id="back1-btn">← Back</button>
        <button class="btn btn-primary" id="generate-btn">Generate MCP Schema</button>
      </div>
    </div>
  </section>

  <!-- ═══ Section 4 – Generate ══════════════════════════════════════ -->
  <section id="sec-generate" class="hidden">
    <div class="card">
      <div class="card-title">
        <span class="badge">Step 3</span>
        Generated MCP Schema
      </div>

      <p style="font-size:.86rem;color:var(--muted);margin-bottom:12px">
        Review the OpenAI function-call tool schema derived from your selected invocables.
      </p>

      <div class="json-preview" id="schema-preview"></div>

      <div class="alert alert-error" id="gen-error"></div>

      <div class="btn-row">
        <button class="btn btn-secondary" id="back2-btn">← Back</button>
        <button class="btn btn-success" id="to-chat-btn">Proceed to Chat →</button>
      </div>
    </div>
  </section>

  <!-- ═══ Section 5 – Chat + Download ═══════════════════════════════ -->
  <section id="sec-chat" class="hidden">
    <div class="card">
      <div class="card-title">
        <span class="badge">Step 4</span>
        Chat &amp; Download
      </div>

      <p style="font-size:.86rem;color:var(--muted);margin-bottom:14px">
        Chat with GPT-4o using your MCP tools attached. The model can invoke your
        generated functions as tools.
      </p>

      <div class="chat-window" id="chat-window">
        <div class="chat-empty" id="chat-empty">
          Send a message to start the conversation.<br/>
          <span style="font-size:.75rem;">e.g. "What tools are available?" or "Run add(3, 4)"</span>
        </div>
      </div>

      <div class="chat-input-row">
        <textarea id="chat-input" placeholder="Ask something about the MCP tools…" rows="2"></textarea>
        <button class="btn btn-primary" id="send-btn">Send</button>
      </div>

      <div class="alert alert-error" id="chat-error"></div>

      <div class="btn-row" style="margin-top:18px">
        <button class="btn btn-secondary" id="back3-btn">← Back</button>
        <button class="btn btn-success" id="download-btn">⬇ Download Schema JSON</button>
        <button class="btn btn-secondary" id="transcript-btn">⬇ Transcript</button>
        <button class="btn btn-secondary" id="restart-btn">↺ Start Over</button>
      </div>
    </div>
  </section>

</main>

<script>
/* ═══════════════════════════════════════════════════════════
   MCP Factory SPA – client-side logic
═══════════════════════════════════════════════════════════ */

// ── State ────────────────────────────────────────────────
const state = {
  jobId: null,
  invocables: [],      // raw list from discovery
  tools: [],           // generated MCP tool schemas
  schemaBlob: null,
  messages: [],        // chat history [{role, content}]
};

// ── DOM refs ─────────────────────────────────────────────
const $ = id => document.getElementById(id);

const sections = ['sec-upload','sec-invocables','sec-generate','sec-chat'];
const stepItems = [1,2,3,4].map(n => $(`step${n}-item`));
const connectors = ['conn12','conn23','conn34'].map(id => $(id));

function showSection(idx) {
  sections.forEach((id, i) => $s(id).classList.toggle('hidden', i !== idx));
  stepItems.forEach((el, i) => {
    el.classList.remove('active','done');
    if (i < idx)  el.classList.add('done');
    if (i === idx) el.classList.add('active');
  });
  connectors.forEach((el, i) => el.classList.toggle('done', i < idx));
}

function $s(id) { return document.getElementById(id); }

// ── Error helpers ─────────────────────────────────────────
function showError(elId, msg) {
  const el = $(elId);
  el.textContent = msg; el.classList.add('show');
}
function clearError(elId) { $(elId).classList.remove('show'); }

// ── File drop ─────────────────────────────────────────────
const fileInput = $('file-input');
const fileDrop  = $('file-drop');

fileInput.addEventListener('change', () => {
  const name = fileInput.files[0]?.name || '';
  $('file-name').textContent = name ? `✓ ${name}` : '';
  // clear the path field when a file is chosen
  if (name) $('dir-path').value = '';
  _syncAnalyzeBtn();
  clearError('upload-error');
});

$('dir-path').addEventListener('input', () => {
  // clear file selection when a path is typed
  if ($('dir-path').value.trim()) {
    fileInput.value = '';
    $('file-name').textContent = '';
  }
  _syncAnalyzeBtn();
  clearError('upload-error');
});

function _syncAnalyzeBtn() {
  const hasFile = !!fileInput.files[0];
  const hasPath = !!$('dir-path').value.trim();
  $('analyze-btn').disabled = !hasFile && !hasPath;
}

['dragover','dragleave','drop'].forEach(ev =>
  fileDrop.addEventListener(ev, e => {
    e.preventDefault();
    if (ev === 'dragover') fileDrop.classList.add('dragover');
    else fileDrop.classList.remove('dragover');
    if (ev === 'drop') {
      fileInput.files = e.dataTransfer.files;
      fileInput.dispatchEvent(new Event('change'));
    }
  })
);

// ── Analyze ───────────────────────────────────────────────
$('analyze-btn').addEventListener('click', async () => {
  clearError('upload-error');
  const file    = fileInput.files[0];
  const dirPath = $('dir-path').value.trim();
  if (!file && !dirPath) return;

  const btn = $('analyze-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Analyzing…';

  try {
    let data;
    if (dirPath) {
      // §2.b installed-path route
      const res = await fetch('/api/analyze-path', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: dirPath, hints: $('hints').value.trim() }),
      });
      if (!res.ok) {
        const err = await res.text();
        throw new Error(`${res.status}: ${err}`);
      }
      data = await res.json();
    } else {
      // §2.a file-upload route
      const fd = new FormData();
      fd.append('file', file);
      fd.append('hints', $('hints').value.trim());
      const res = await fetch('/api/analyze', { method: 'POST', body: fd });
      if (!res.ok) {
        const err = await res.text();
        throw new Error(`${res.status}: ${err}`);
      }
      data = await res.json();
    }
    // Both routes return 202 {job_id, status_url} — poll until done
    const jobId = data.job_id;
    state.jobId = jobId;
    if (!jobId) throw new Error('No job_id in response');

    // Poll GET /api/jobs/{id} until status is done or error
    let jobResult = null;
    let _pollMisses = 0;
    for (let i = 0; i < 150; i++) {
      await new Promise(r => setTimeout(r, 3000));
      btn.innerHTML = `<span class="spinner"></span> Analyzing… (${(i+1)*3}s)`;
      const poll = await fetch(`/api/jobs/${jobId}`);
      if (!poll.ok) {
        // Tolerate transient cross-pod 404s and proxy/network blips (502/503/504)
        // up to 5 consecutive misses before giving up.
        if ([404, 502, 503, 504].includes(poll.status) && ++_pollMisses <= 5) continue;
        throw new Error(`Poll failed: ${poll.status}`);
      }
      _pollMisses = 0;
      const s = await poll.json();
      if (s.status === 'done') { jobResult = s.result; break; }
      if (s.status === 'error') throw new Error(s.error || 'Discovery failed');
    }
    if (!jobResult) throw new Error('Analysis timed out after 450s');

    state.invocables = flattenInvocables(jobResult.invocables);
    buildInvocablesList();
    showSection(1);
  } catch(e) {
    showError('upload-error', `Analysis failed: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = 'Analyze Binary';
  }
});

// ── Flatten invocables from the discovery JSON ────────────
function flattenInvocables(raw) {
  // The discovery JSON can come in several shapes; normalise to a flat array.
  // Always normalise individual items so `doc`, `signature`, and `execution`
  // are reliably present for the rest of the UI.
  function normalise(inv) {
    return {
      ...inv,
      // discovery uses `description`; older formats may use `doc`/`signature`
      doc: inv.doc ?? inv.description ?? '',
      signature: inv.signature ?? inv.description ?? inv.name ?? '',
      parameters: inv.parameters ?? [],
      execution: inv.execution ?? {},
      tier: inv.tier ?? inv.confidence_tier ?? 2,
    };
  }

  // Plain array – already normalised (the API now always returns this)
  if (Array.isArray(raw)) return raw.map(normalise);

  // Nested discovery format: {metadata:{}, invocables:[…], summary:{}}
  // Also handles the case where the API accidentally passes the full JSON dict.
  if (raw && Array.isArray(raw.invocables)) return raw.invocables.map(normalise);

  // OpenAI function-call tool schema: {tools: [{type:"function", function:{…}}]}
  if (raw && Array.isArray(raw.tools)) return raw.tools.map(t => normalise({
    name: t.function?.name ?? t.name,
    signature: t.function?.description ?? '',
    doc: t.function?.description ?? '',
    parameters: Object.entries(t.function?.parameters?.properties ?? {}).map(([k,v])=>({name:k,type:v.type})),
    tier: 1,
    execution: t.execution ?? {},
  }));

  // Legacy flat object {name: {signature, doc, …}}
  // Guard against top-level metadata/summary keys sneaking in.
  if (raw && typeof raw === 'object') {
    const skip = new Set(['metadata', 'summary', 'mcp_version', 'component']);
    return Object.entries(raw)
      .filter(([k]) => !skip.has(k))
      .map(([name, info]) => normalise({
        name,
        signature: info.signature ?? name,
        doc: info.doc ?? info.description ?? '',
        parameters: info.parameters ?? [],
        tier: info.tier ?? 2,
        execution: info.execution ?? {},
      }));
  }
  return [];
}

function buildInvocablesList() {
  const list = $('inv-list');
  list.innerHTML = '';

  if (!state.invocables.length) {
    list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--muted)">No invocables discovered.</div>';
    return;
  }

  state.invocables.forEach((inv, idx) => {
    const item = document.createElement('div');
    item.className = 'inv-item';

    const tier = inv.tier ?? 2;
    const tierLabel = ['','T1','T2','T3'][tier] ?? 'T?';
    const tierClass = `tier-${Math.min(tier,3)}`;

    item.innerHTML = `
      <input type="checkbox" id="cb${idx}" checked />
      <div style="flex:1;min-width:0">
        <div class="inv-name">${esc(inv.name)}</div>
        ${inv.signature && inv.signature !== inv.name
          ? `<div class="inv-sig">${esc(inv.signature)}</div>` : ''}
        ${inv.doc ? `<div class="inv-doc">${esc(inv.doc)}</div>` : ''}
      </div>
      <span class="inv-tier ${tierClass}">${tierLabel}</span>
    `;

    item.querySelector('input').addEventListener('change', updateSelCount);
    item.addEventListener('click', e => {
      if (e.target.tagName !== 'INPUT') item.querySelector('input').click();
    });
    list.appendChild(item);
  });

  updateSelCount();
  $('component-name').value = $('file-input').files[0]?.name.replace(/\.\w+$/,'') ?? 'mcp-component';
}

function updateSelCount() {
  const total = state.invocables.length;
  const n = document.querySelectorAll('#inv-list input[type=checkbox]:checked').length;
  $('sel-count').textContent = `${n} / ${total} selected`;
  $('generate-btn').disabled = n === 0;
}

$('sel-all-btn').addEventListener('click',  () => {
  document.querySelectorAll('#inv-list input[type=checkbox]').forEach(cb => cb.checked = true);
  updateSelCount();
});
$('sel-none-btn').addEventListener('click', () => {
  document.querySelectorAll('#inv-list input[type=checkbox]').forEach(cb => cb.checked = false);
  updateSelCount();
});

// ── Generate ──────────────────────────────────────────────
$('generate-btn').addEventListener('click', async () => {
  clearError('inv-error');
  const checked = [...document.querySelectorAll('#inv-list input[type=checkbox]:checked')];
  const selected = checked.map(cb => {
    const idx = parseInt(cb.id.replace('cb',''));
    return state.invocables[idx];
  });

  const btn = $('generate-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Generating…';

  try {
    const res = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        job_id: state.jobId,
        selected,
        component_name: $('component-name').value.trim() || 'mcp-component',
      }),
    });
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
    const data = await res.json();
    state.tools = data.mcp_schema?.tools ?? [];
    state.schemaBlob = data.schema_blob;
    $('schema-preview').textContent = JSON.stringify(data.mcp_schema, null, 2);
    showSection(2);
  } catch(e) {
    showError('inv-error', `Generation failed: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = 'Generate MCP Schema';
  }
});

// ── Chat ──────────────────────────────────────────────────
$('to-chat-btn').addEventListener('click', () => showSection(3));

async function sendMessage() {
  const input = $('chat-input');
  const text = input.value.trim();
  if (!text) return;

  input.value = '';
  clearError('chat-error');

  appendChatMsg('user', text);
  state.messages.push({ role: 'user', content: text });

  const btn = $('send-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';

  // Streaming response — create a placeholder bubble and fill it live
  let assistantBubble = null;
  let assistantText   = '';
  let roundCount      = 1;

  try {
    const res = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages:   state.messages,
        tools:      state.tools,
        invocables: state.invocables,
        job_id:     state.jobId,
      }),
    });
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);

    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let   buf     = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buf += decoder.decode(value, { stream: true });

      // SSE lines are separated by \n\n; process every complete event
      let boundary;
      while ((boundary = buf.indexOf('\n\n')) !== -1) {
        const raw = buf.slice(0, boundary).trim();
        buf = buf.slice(boundary + 2);

        if (!raw.startsWith('data:')) continue;
        let evt;
        try { evt = JSON.parse(raw.slice(5).trim()); }
        catch { continue; }

        if (evt.type === 'token') {
          // Append to the live assistant bubble
          assistantText += evt.content;
          if (!assistantBubble) {
            assistantBubble = appendChatBubble('assistant', assistantText);
          } else {
            assistantBubble.textContent = assistantText;
            $('chat-window').scrollTop = $('chat-window').scrollHeight;
          }

        } else if (evt.type === 'tool_call') {
          const argStr = typeof evt.args === 'string' ? evt.args : JSON.stringify(evt.args ?? {});
          appendChatMsg('tool-call', `🔧 ${evt.name}(${argStr})`);

        } else if (evt.type === 'tool_result') {
          appendChatMsg('tool-call', `   ↳ ${evt.result ?? ''}`);

        } else if (evt.type === 'status') {
          appendChatMsg('tool-call', `⏳ ${evt.message ?? 'Working...'}`);

        } else if (evt.type === 'done') {
          roundCount = evt.rounds ?? 1;

        } else if (evt.type === 'error') {
          throw new Error(evt.message);
        }
      }
    }

    // If no text tokens came through at all (shouldn't happen after server fix,
    // but keep a non-alarming fallback just in case).
    if (!assistantBubble && !assistantText) {
      assistantText = 'Done.';
      assistantBubble = appendChatBubble('assistant', assistantText);
    }
    if (roundCount > 1) {
      appendChatMsg('tool-call', `ℹ️ Completed in ${roundCount} agentic round(s)`);
    }
    state.messages.push({ role: 'assistant', content: assistantText });

  } catch(e) {
    showError('chat-error', `Chat error: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = 'Send';
  }
}

function appendChatBubble(role, text) {
  const win = $('chat-window');
  $('chat-empty')?.remove();
  const div = document.createElement('div');
  div.className = `chat-msg ${role}`;
  div.textContent = text;
  win.appendChild(div);
  win.scrollTop = win.scrollHeight;
  return div;
}

function appendChatMsg(role, text) {
  appendChatBubble(role, text);
}

$('send-btn').addEventListener('click', sendMessage);

$('chat-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

// ── Download ──────────────────────────────────────────────
$('download-btn').addEventListener('click', () => {
  if (!state.jobId) return;
  window.location.href = `/api/download/${state.jobId}/mcp_schema.json`;
});

$('transcript-btn').addEventListener('click', () => {
  const lines = state.messages
    .filter(m => m.role !== 'system')
    .map(m => `[${m.role.toUpperCase()}]\n${m.content ?? ''}`)
    .join('\n\n---\n\n');
  const blob = new Blob([lines], { type: 'text/plain' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `mcp-transcript-${state.jobId ?? 'session'}.txt`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
});

// ── Navigation ────────────────────────────────────────────
$('back1-btn').addEventListener('click',  () => showSection(0));
$('back2-btn').addEventListener('click',  () => showSection(1));
$('back3-btn').addEventListener('click',  () => showSection(2));

$('restart-btn').addEventListener('click', () => {
  state.jobId = null; state.invocables = []; state.tools = [];
  state.schemaBlob = null; state.messages = [];
  fileInput.value = ''; $('file-name').textContent = '';
  $('dir-path').value = ''; $('hints').value = '';
  $('analyze-btn').disabled = true;
  $('chat-window').innerHTML =
    '<div class="chat-empty" id="chat-empty">Send a message to start the conversation.<br/><span style="font-size:.75rem;">e.g. "What tools are available?" or "Run add(3, 4)"</span></div>';
  showSection(0);
});

// ── Utility ───────────────────────────────────────────────
function esc(str) {
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}

// Init
showSection(0);
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    return HTMLResponse(_HTML)


# ── Proxy helpers ──────────────────────────────────────────────────────────

_http: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        headers = {"X-Pipeline-Key": PIPELINE_KEY} if PIPELINE_KEY else {}
        _http = httpx.AsyncClient(base_url=PIPELINE_URL, timeout=180.0, headers=headers)
    return _http


async def _proxy_json(path: str, body: Any) -> JSONResponse:
    try:
        r = await _client().post(path, json=body)
        try:
            content = r.json()
        except Exception:
            content = {"detail": r.text or f"Pipeline returned empty response (HTTP {r.status_code})"}
        return JSONResponse(content=content, status_code=r.status_code if r.text else 502)
    except Exception as e:
        logger.error(f"Proxy error → {path}: {e}")
        return JSONResponse({"detail": str(e)}, status_code=502)


# ── Proxied endpoints ──────────────────────────────────────────────────────

@app.post("/api/analyze")
async def proxy_analyze(file: UploadFile = File(...), hints: str = Form(default="")):
    """Proxy file upload to the pipeline /api/analyze."""
    content = await file.read()
    try:
        r = await _client().post(
            "/api/analyze",
            data={"hints": hints},
            files={"file": (file.filename, content, file.content_type or "application/octet-stream")},
        )
        return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as e:
        logger.error(f"Proxy analyze error: {e}")
        return JSONResponse({"detail": str(e)}, status_code=502)


@app.post("/api/analyze-path")
async def proxy_analyze_path(body: dict[str, Any]) -> JSONResponse:
    """Proxy installed-path analysis to the pipeline /api/analyze-path."""
    return await _proxy_json("/api/analyze-path", body)


@app.post("/api/generate")
async def proxy_generate(body: dict[str, Any]) -> JSONResponse:
    """Proxy generate request to the pipeline /api/generate."""
    return await _proxy_json("/api/generate", body)


@app.post("/api/chat")
async def proxy_chat(body: dict[str, Any]) -> JSONResponse:
    """Proxy chat request to the pipeline /api/chat."""
    return await _proxy_json("/api/chat", body)


@app.post("/api/chat/stream")
async def proxy_chat_stream(request: Request):
    """Proxy the streaming SSE chat endpoint — pipes bytes through as they arrive."""
    body = await request.json()
    headers = {"X-Pipeline-Key": PIPELINE_KEY} if PIPELINE_KEY else {}

    async def _stream():
        async with httpx.AsyncClient(base_url=PIPELINE_URL, timeout=300.0, headers=headers) as c:
            async with c.stream("POST", "/api/chat", json=body) as r:
                if r.status_code != 200:
                    err = await r.aread()
                    yield f'data: {{"type":"error","message":"Pipeline {r.status_code}: {err.decode()[:200]}"}}\n\n'.encode()
                    return
                async for chunk in r.aiter_bytes():
                    yield chunk

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/jobs/{job_id}")
async def proxy_job_status(job_id: str) -> JSONResponse:
    """Proxy job-status polling to the pipeline /api/jobs/{job_id}."""
    try:
        r = await _client().get(f"/api/jobs/{job_id}")
        return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as e:
        logger.error(f"Proxy job status error: {e}")
        return JSONResponse({"detail": str(e)}, status_code=502)


@app.get("/api/download/{job_id}/{filename}")
async def proxy_download(job_id: str, filename: str) -> Response:
    """Stream artifact download from the pipeline."""
    try:
        r = await _client().get(f"/api/download/{job_id}/{filename}")
        return Response(
            content=r.content,
            status_code=r.status_code,
            media_type=r.headers.get("content-type", "application/json"),
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        return JSONResponse({"detail": str(e)}, status_code=502)


@app.get("/health")
def health():
    return {"status": "ok", "pipeline_url": PIPELINE_URL}


# ── P6: GitHub Copilot Extension agent endpoint ──────────────────────────
# GitHub routes @mcp-factory invocations here when the app is registered
# as a Copilot Extension.  Token verification + SSE streaming.
@app.post("/copilot")
async def copilot(request: Request):
    """GitHub Copilot Extensions agent endpoint (P6)."""
    from copilot_handler import copilot_endpoint  # type: ignore
    return await copilot_endpoint(request)
