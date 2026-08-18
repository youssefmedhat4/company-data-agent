/* ==========================================================================
   Company Data Agent — chat client
   Talks to /api/ask/stream (falls back to /api/ask). Charts and figures are
   rendered from the structured payload the backend returns, never parsed out
   of the model's prose.
   ========================================================================== */

/* --- icons (no emoji as UI affordances) ---------------------------------- */

const ICON_PATHS = {
  plus: '<path d="M8 3v10M3 8h10"/>',
  search: '<circle cx="7.2" cy="7.2" r="4.2"/><path d="M10.5 10.5 14 14"/>',
  'panel-left': '<rect x="2.5" y="3" width="15" height="14" rx="2"/><path d="M8 3v14"/>',
  'arrow-up': '<path d="M10 15V5"/><path d="M5.5 9.5 10 5l4.5 4.5"/>',
  stop: '<rect x="6" y="6" width="8" height="8" rx="1.5" fill="currentColor" stroke="none"/>',
  lock: '<rect x="4.5" y="8.5" width="11" height="7" rx="1.8"/><path d="M7.2 8.5V6.8a2.8 2.8 0 0 1 5.6 0v1.7"/>',
  sun: '<circle cx="10" cy="10" r="3.4"/><path d="M10 2.6v1.8M10 15.6v1.8M2.6 10h1.8M15.6 10h1.8M4.8 4.8l1.3 1.3M13.9 13.9l1.3 1.3M15.2 4.8l-1.3 1.3M6.1 13.9 4.8 15.2"/>',
  moon: '<path d="M15.5 11.4A6 6 0 0 1 8.6 4.5a6 6 0 1 0 6.9 6.9Z"/>',
  database: '<ellipse cx="10" cy="5.4" rx="5.6" ry="2.4"/><path d="M4.4 5.4v9.2c0 1.3 2.5 2.4 5.6 2.4s5.6-1.1 5.6-2.4V5.4"/><path d="M4.4 10c0 1.3 2.5 2.4 5.6 2.4s5.6-1.1 5.6-2.4"/>',
  table: '<rect x="3" y="4" width="14" height="12" rx="1.8"/><path d="M3 8h14M8 8v8"/>',
  chart: '<path d="M3.5 16.5V9M8 16.5V4.5M12.5 16.5v-5M17 16.5V7"/>',
  copy: '<rect x="7" y="7" width="9.5" height="9.5" rx="1.8"/><path d="M12.6 7V5.2A1.7 1.7 0 0 0 10.9 3.5H5.2A1.7 1.7 0 0 0 3.5 5.2v5.7c0 .94.76 1.7 1.7 1.7H7"/>',
  check: '<path d="M4.5 10.5 8 14l7.5-8"/>',
  retry: '<path d="M16 10a6 6 0 1 1-1.9-4.4"/><path d="M16.2 3.2v3.2H13"/>',
  trash: '<path d="M4.5 6.5h11M8 6.5V5a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v1.5M6 6.5l.6 8.2a1.4 1.4 0 0 0 1.4 1.3h4a1.4 1.4 0 0 0 1.4-1.3l.6-8.2"/>',
  chevron: '<path d="M8 5.5 12.5 10 8 14.5"/>',
  alert: '<path d="M10 3.8 2.9 16h14.2L10 3.8Z"/><path d="M10 8.4v3.2M10 13.9h.01"/>',
  eraser: '<path d="M8.6 15.5H16"/><path d="m4.4 12.1 4.3-4.3 4.6 4.6-2.4 2.4a1.6 1.6 0 0 1-2.3 0l-4.2-4.2a1.6 1.6 0 0 1 0-2.3l3.3-3.3a1.6 1.6 0 0 1 2.3 0l3.6 3.6"/>',
  spark: '<path d="M10 3.5 11.6 8 16 9.6 11.6 11.2 10 15.7 8.4 11.2 4 9.6 8.4 8Z"/>',
  message: '<path d="M16.5 9.6c0 3.1-2.9 5.6-6.5 5.6-.86 0-1.7-.14-2.4-.4L4 16.2l1.2-2.8A5.3 5.3 0 0 1 3.5 9.6C3.5 6.5 6.4 4 10 4s6.5 2.5 6.5 5.6Z"/>',
};

function icon(name, size = 16) {
  const path = ICON_PATHS[name] || '';
  return `<svg width="${size}" height="${size}" viewBox="0 0 20 20" fill="none"
    stroke="currentColor" stroke-width="1.6" stroke-linecap="round"
    stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
}

function hydrateIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach((el) => {
    el.innerHTML = icon(el.dataset.icon, Number(el.dataset.size) || 16);
  });
}

/* --- small helpers -------------------------------------------------------- */

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
};
const escapeHtml = (s) => String(s).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const isNumeric = (v) => typeof v === 'number' ||
  (typeof v === 'string' && v.trim() !== '' && /^[-+]?[\d.,\s%]+$/.test(v.trim()));

const fmtNumber = (v) => typeof v === 'number' && Number.isFinite(v)
  ? v.toLocaleString('en-US', { maximumFractionDigits: 2 }) : String(v ?? '');

const TOOL_DESCRIPTIONS = {
  search_objects: 'reading the schema',
  execute_sql: 'running a query',
};

/* --- state ---------------------------------------------------------------- */

const PREVIEW = location.protocol === 'file:';
const STORAGE_KEY = 'cda.conversations.v1';

const state = {
  conversations: [],
  activeId: null,
  status: 'idle',
  search: '',
  allowSensitive: false,
  controller: null,
};

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    state.conversations = raw ? JSON.parse(raw) : [];
  } catch { state.conversations = []; }
}
function save() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state.conversations.slice(0, 100)));
  } catch { /* private mode — the UI still works for this session */ }
}
const active = () => state.conversations.find((c) => c.id === state.activeId) || null;

function newConversation() {
  const conv = { id: `c${Date.now()}`, title: 'New chat', updatedAt: Date.now(), turns: [] };
  state.conversations.unshift(conv);
  state.activeId = conv.id;
  return conv;
}

/* --- ConversationList ----------------------------------------------------- */

function groupLabel(ts) {
  const day = 864e5;
  const start = new Date(); start.setHours(0, 0, 0, 0);
  if (ts >= start.getTime()) return 'Today';
  if (ts >= start.getTime() - day) return 'Yesterday';
  if (ts >= start.getTime() - 7 * day) return 'Previous 7 days';
  return 'Earlier';
}

function renderConversationList() {
  const list = $('#convList');
  list.innerHTML = '';
  const q = state.search.trim().toLowerCase();
  const items = state.conversations.filter((c) => !q ||
    c.title.toLowerCase().includes(q) ||
    c.turns.some((t) => (t.content || '').toLowerCase().includes(q)));

  if (!items.length) {
    list.append(el('p', 'sidebar-empty',
      q ? 'No conversation matches that search.' : 'No conversations yet.'));
    return;
  }

  let lastGroup = null;
  for (const conv of items) {
    const label = groupLabel(conv.updatedAt);
    if (label !== lastGroup) {
      lastGroup = label;
      list.append(el('div', 'conv-group-label', label));
    }

    const row = el('div', 'conv-item');
    row.dataset.active = String(conv.id === state.activeId);

    const open = el('button', 'conv-open', conv.title);
    open.type = 'button';
    open.dir = 'auto';
    if (conv.id === state.activeId) open.setAttribute('aria-current', 'true');
    open.addEventListener('click', () => {
      state.activeId = conv.id;
      renderAll();
      if (window.innerWidth < 768) closeDrawer();
      $('#conversation').scrollTop = $('#conversation').scrollHeight;
    });

    const del = el('button', 'icon-btn conv-del');
    del.type = 'button';
    del.innerHTML = icon('trash', 15);
    del.setAttribute('aria-label', `Delete conversation: ${conv.title}`);
    del.addEventListener('click', (e) => {
      e.stopPropagation();
      state.conversations = state.conversations.filter((c) => c.id !== conv.id);
      if (state.activeId === conv.id) state.activeId = state.conversations[0]?.id ?? null;
      save();
      renderAll();
    });

    row.append(open, del);
    list.append(row);
  }
}

/* --- markdown ------------------------------------------------------------- */

function renderInline(text) {
  let out = escapeHtml(text);
  out = out.replace(/`([^`]+)`/g, (_, code) => `<code>${code}</code>`);
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return out;
}

function renderTableBlock(rows) {
  const cells = rows.map((r) => r.replace(/^\||\|$/g, '').split('|').map((c) => c.trim()));
  const head = cells[0];
  const body = cells.slice(2);
  const numeric = head.map((_, i) => body.length && body.every((r) => isNumeric(r[i])));
  const th = head.map((h, i) => `<th class="${numeric[i] ? 'num' : ''}">${renderInline(h)}</th>`).join('');
  const tr = body.map((r) =>
    `<tr>${r.map((c, i) => `<td class="${numeric[i] ? 'num' : ''}" dir="auto">${renderInline(c)}</td>`).join('')}</tr>`
  ).join('');
  return `<div class="table-wrap"><table><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table></div>`;
}

function renderMarkdown(src) {
  const lines = String(src || '').replace(/\r/g, '').split('\n');
  const out = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (/^\s*```/.test(line)) {
      const lang = line.replace(/^\s*```/, '').trim() || 'text';
      const buf = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      out.push(
        `<figure class="code"><figcaption><span class="code-lang">${escapeHtml(lang)}</span>` +
        `<button type="button" class="icon-btn" data-copy aria-label="Copy code">${icon('copy', 15)}</button>` +
        `</figcaption><pre dir="ltr"><code>${escapeHtml(buf.join('\n'))}</code></pre></figure>`);
      continue;
    }

    if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1] || '')) {
      const buf = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) buf.push(lines[i++].trim());
      out.push(renderTableBlock(buf));
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const level = Math.min(heading[1].length + 1, 6);
      out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      i++;
      continue;
    }

    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { out.push('<hr>'); i++; continue; }

    if (/^\s*>\s?/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ''));
      out.push(`<blockquote>${renderInline(buf.join(' '))}</blockquote>`);
      continue;
    }

    const bullet = /^\s*[-*+]\s+/;
    const ordered = /^\s*\d+[.)]\s+/;
    if (bullet.test(line) || ordered.test(line)) {
      const isOrdered = ordered.test(line);
      const re = isOrdered ? ordered : bullet;
      const items = [];
      while (i < lines.length && re.test(lines[i])) {
        let item = lines[i++].replace(re, '');
        while (i < lines.length && lines[i].trim() && !re.test(lines[i]) &&
               !/^\s*```/.test(lines[i]) && !/^#{1,6}\s/.test(lines[i])) {
          item += ' ' + lines[i++].trim();
        }
        items.push(`<li dir="auto">${renderInline(item)}</li>`);
      }
      out.push(`<${isOrdered ? 'ol' : 'ul'}>${items.join('')}</${isOrdered ? 'ol' : 'ul'}>`);
      continue;
    }

    if (!line.trim()) { i++; continue; }

    const buf = [];
    while (i < lines.length && lines[i].trim() &&
           !/^\s*```/.test(lines[i]) && !/^#{1,6}\s/.test(lines[i]) &&
           !bullet.test(lines[i]) && !ordered.test(lines[i]) &&
           !/^\s*>\s?/.test(lines[i]) && !/^\s*\|.*\|\s*$/.test(lines[i])) {
      buf.push(lines[i++]);
    }
    out.push(`<p dir="auto">${renderInline(buf.join(' '))}</p>`);
  }

  return out.join('');
}

/* --- ChartContainer ------------------------------------------------------- */

function looksLikeTime(labels) {
  const re = /^(?:\d{4}(?:[-./]\d{1,2}){0,2}|\d{4}\s*Q[1-4]|Q[1-4](?:\s*\d{4})?|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*|(?:يناير|فبراير|مارس|أبريل|ابريل|مايو|يونيو|يوليو|أغسطس|اغسطس|سبتمبر|أكتوبر|اكتوبر|نوفمبر|ديسمبر))$/i;
  const hits = (labels || []).filter((l) => re.test(String(l ?? '').trim())).length;
  return labels.length >= 2 && hits >= Math.max(2, Math.floor(labels.length * 0.6));
}

function chooseChartType(cd) {
  const labels = cd.labels || [];
  const values = (cd.values || []).map((v) => Number(v) || 0);
  const n = values.length;
  if (looksLikeTime(labels)) return 'line';
  const allNonNeg = n > 0 && values.every((v) => v >= 0);
  if (n >= 2 && n <= 8 && allNonNeg) return 'pie';
  return 'bar';
}

function chartContainer(cd, { loading = false } = {}) {
  const type = chooseChartType(cd || {});
  const box = el('figure', 'chart');
  box.style.margin = '0';
  box.dataset.type = type;
  const title = el('figcaption', 'chart-title',
    (cd?.title || 'Chart') + (cd?.unit ? ` (${cd.unit})` : ''));
  const body = el('div', 'chart-body');
  body.dataset.type = type;
  box.append(title, body);

  if (loading) {
    const ph = el('div', 'chart-placeholder', 'Preparing chart…');
    ph.setAttribute('role', 'status');
    body.append(ph);
    return box;
  }

  const canvas = document.createElement('canvas');
  body.append(canvas);
  const draw = () => drawChart(canvas, cd);
  requestAnimationFrame(draw);
  if (window.ResizeObserver) new ResizeObserver(draw).observe(body);
  return box;
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function drawChart(canvas, cd) {
  const body = canvas.parentElement;
  if (!body || !body.clientWidth) return;
  const dpr = window.devicePixelRatio || 1;
  const w = body.clientWidth, h = body.clientHeight;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const palette = [1, 2, 3, 4, 5, 6].map((n) => cssVar(`--chart-${n}`));
  const fgMuted = cssVar('--fg-muted');
  const fgSubtle = cssVar('--fg-subtle');
  const border = cssVar('--border');
  const labels = (cd.labels || []).map((l) => String(l ?? ''));
  const values = (cd.values || []).map((v) => Number(v) || 0);
  const type = chooseChartType(cd);
  const compact = w < 420;

  if (type === 'pie') {
    const total = values.reduce((a, b) => a + b, 0) || 1;
    const legendH = compact ? labels.length * 22 + 8 : 0;
    const r = Math.min(h - legendH, compact ? w : w * 0.42) / 2 - 8;
    const cx = compact ? w / 2 : r + 16;
    const cy = compact ? r + 10 : h / 2;
    let start = -Math.PI / 2;
    values.forEach((v, idx) => {
      const angle = (v / total) * Math.PI * 2;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, r, start, start + angle);
      ctx.closePath();
      ctx.fillStyle = palette[idx % palette.length];
      ctx.fill();
      start += angle;
    });
    ctx.font = `12px ${cssVar('--font-sans') || 'sans-serif'}`;
    ctx.textAlign = 'start';
    ctx.textBaseline = 'middle';
    const lx = compact ? 8 : cx + r + 24;
    const ly = compact ? cy + r + 20 : Math.max(14, cy - (labels.length * 22) / 2);
    labels.forEach((label, idx) => {
      const y = ly + idx * 22;
      const pct = Math.round((values[idx] / total) * 100);
      ctx.fillStyle = palette[idx % palette.length];
      ctx.fillRect(lx, y - 5, 10, 10);
      ctx.fillStyle = fgMuted;
      ctx.fillText(`${label} — ${fmtNumber(values[idx])} (${pct}%)`, lx + 16, y);
    });
    return;
  }

  const max = Math.max(...values, 1);
  const padTop = 12, padBottom = 26;
  const font = `11px ${cssVar('--font-sans') || 'sans-serif'}`;
  ctx.font = font;
  const padLeft = Math.min(72, Math.max(36, ctx.measureText(fmtNumber(max)).width + 12));
  const plotW = w - padLeft - 8, plotH = h - padTop - padBottom;

  ctx.strokeStyle = border;
  ctx.lineWidth = 1;
  [0, 0.5, 1].forEach((f) => {
    const y = Math.round(padTop + plotH * f) + 0.5;
    ctx.beginPath(); ctx.moveTo(padLeft, y); ctx.lineTo(padLeft + plotW, y); ctx.stroke();
    ctx.fillStyle = fgSubtle;
    ctx.textAlign = 'end'; ctx.textBaseline = 'middle';
    ctx.fillText(fmtNumber(Math.round(max * (1 - f))), padLeft - 8, y);
  });

  const slot = plotW / Math.max(values.length, 1);
  if (type === 'line') {
    ctx.strokeStyle = palette[0];
    ctx.lineWidth = 2;
    ctx.beginPath();
    values.forEach((v, idx) => {
      const x = padLeft + slot * idx + slot / 2;
      const y = padTop + plotH - (v / max) * plotH;
      idx ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
    ctx.fillStyle = palette[0];
    values.forEach((v, idx) => {
      const x = padLeft + slot * idx + slot / 2;
      const y = padTop + plotH - (v / max) * plotH;
      ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill();
    });
  } else {
    // One series, one colour: a hue per bar would encode nothing.
    const barW = Math.min(56, slot * 0.62);
    ctx.fillStyle = palette[0];
    values.forEach((v, idx) => {
      const x = padLeft + slot * idx + (slot - barW) / 2;
      const barH = (v / max) * plotH;
      ctx.fillRect(x, padTop + plotH - barH, barW, barH);
    });
  }

  ctx.fillStyle = fgMuted;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  const step = compact && labels.length > 4 ? 2 : 1;
  labels.forEach((label, idx) => {
    if (idx % step) return;
    const maxChars = Math.max(4, Math.floor(slot / 7));
    const short = label.length > maxChars ? label.slice(0, maxChars - 1) + '…' : label;
    ctx.fillText(short, padLeft + slot * idx + slot / 2, padTop + plotH + 8);
  });
}

/* --- DataTable ------------------------------------------------------------ */

function dataTable(columns, rows, caption) {
  const wrap = el('div');
  if (caption) wrap.append(el('div', 'table-caption', caption));
  const scroll = el('div', 'table-wrap');
  const table = el('table');
  const numeric = columns.map((c) => rows.length && rows.every((r) => isNumeric(r[c])));

  const thead = el('thead');
  const hr = el('tr');
  columns.forEach((c, idx) => {
    const th = el('th', numeric[idx] ? 'num' : '', c.replace(/_/g, ' '));
    hr.append(th);
  });
  thead.append(hr);

  const tbody = el('tbody');
  rows.forEach((r) => {
    const tr = el('tr');
    columns.forEach((c, idx) => {
      const td = el('td', numeric[idx] ? 'num' : '', fmtNumber(r[c]));
      td.dir = 'auto';
      tr.append(td);
    });
    tbody.append(tr);
  });

  table.append(thead, tbody);
  scroll.append(table);
  wrap.append(scroll);
  return wrap;
}

/* --- ToolStatus ----------------------------------------------------------- */

function toolStatusRow(name, args, stateName = 'running', result = null) {
  const row = el('div', 'tool-status');
  row.dataset.state = stateName;
  row.setAttribute('role', 'status');
  row.dir = 'ltr';  // tool names and arguments are identifiers, never RTL prose
  const glyph = name === 'search_objects' ? 'search' : name === 'execute_sql' ? 'table' : 'database';
  row.insertAdjacentHTML('afterbegin', icon(result?.error ? 'alert' : glyph, 14));
  row.append(el('code', null, name));
  const detail = TOOL_DESCRIPTIONS[name] || 'reading data';
  const argText = Object.entries(args || {})
    .filter(([, v]) => v !== null && v !== undefined && v !== 'none')
    .map(([k, v]) => `${k}=${v}`).join('  ');
  row.append(el('span', 'tool-args', argText ? `${detail} · ${argText}` : detail));
  if (result?.error) row.dataset.state = 'blocked';
  return row;
}

function toolSummary(calls) {
  const line = el('div', 'tool-summary');
  line.dir = 'ltr';
  line.insertAdjacentHTML('afterbegin', icon('database', 13));
  const names = [...new Set(calls.map((c) => c.tool))].join(', ');
  line.append(document.createTextNode(
    `${calls.length} database ${calls.length === 1 ? 'call' : 'calls'} · ${names}`));
  return line;
}

/* --- data extraction (structured payload only) ---------------------------- */

function sqlRows(result) {
  const sets = result?.resultSets || result?.data?.statements || [];
  const first = sets[0];
  return Array.isArray(first?.rows) ? first.rows : null;
}

function seriesFromRows(rows) {
  if (!Array.isArray(rows) || rows.length < 2) return null;
  const keys = Object.keys(rows[0] || {});
  if (keys.length < 2) return null;
  const labelKey = keys[0];
  let valueKey = keys[1];
  for (const k of keys.slice(1)) {
    if (rows.some((r) => typeof r[k] === 'number' && typeof r[k] !== 'boolean')) {
      valueKey = k;
      break;
    }
  }
  return {
    labels: rows.map((r) => String(r[labelKey] ?? '')),
    values: rows.map((r) => r[valueKey]),
    title: valueKey.replace(/_/g, ' '),
  };
}

function chartFrom(calls) {
  for (const call of [...(calls || [])].reverse()) {
    const series = seriesFromRows(sqlRows(call.result));
    if (!series) continue;
    return {
      type: chooseChartType(series),
      title: series.title,
      unit: '',
      labels: series.labels,
      values: series.values,
    };
  }
  return null;
}

function kpiFrom(calls) {
  for (const call of [...(calls || [])].reverse()) {
    const res = call.result;
    if (!res || res.error) continue;
    const rows = sqlRows(res);
    if (!rows || rows.length !== 1) continue;
    const keys = Object.keys(rows[0]);
    if (!keys.length) continue;
    const numKey = keys.find((k) => typeof rows[0][k] === 'number') || keys[0];
    return { value: rows[0][numKey], unit: '', label: numKey.replace(/_/g, ' ') };
  }
  return null;
}

function tabularFrom(calls) {
  for (const call of [...(calls || [])].reverse()) {
    const res = call.result;
    if (!res || res.error) continue;
    const rows = sqlRows(res);
    if (Array.isArray(rows) && rows.length > 1) {
      return {
        caption: 'query result',
        columns: Object.keys(rows[0]),
        rows,
      };
    }
  }
  return null;
}

/* --- AssistantMessage ----------------------------------------------------- */

function assistantTurn() {
  const turn = el('article', 'turn turn-assistant enter');
  turn.setAttribute('aria-live', 'polite');
  turn.setAttribute('aria-busy', 'true');
  turn.dir = 'auto';

  const label = el('div', 'turn-label');
  label.insertAdjacentHTML('afterbegin', icon('spark', 13));
  label.append(document.createTextNode('Assistant'));

  const tools = el('div', 'tool-list');
  const bodyEl = el('div');
  turn.append(label, tools, bodyEl);
  return { turn, tools, body: bodyEl };
}

function showThinking(body) {
  body.innerHTML = '';
  body.append(el('div', 'thinking', 'Thinking'));
}

function showSkeleton(body, label = 'Writing the answer') {
  body.innerHTML = '';
  const status = el('div', 'thinking', label);
  const set = el('div', 'skeleton-set');
  set.append(el('div', 'skeleton'), el('div', 'skeleton'), el('div', 'skeleton'));
  body.append(status, set);
}

function renderAssistantResult(turn, body, data, { onRetry }) {
  turn.setAttribute('aria-busy', 'false');
  body.innerHTML = '';

  if (data.error) {
    const err = el('div', 'error-block');
    err.setAttribute('role', 'alert');
    err.textContent = data.error;
    body.append(err);
    body.append(actionRow({ onRetry }));
    return;
  }

  const md = el('div', 'md');
  md.dir = 'auto';
  md.innerHTML = renderMarkdown(data.answer || '_No answer was returned._');
  body.append(md);

  const kpi = kpiFrom(data.calls);
  const cd = (data.chart_data && data.chart_data.values?.length > 1)
    ? data.chart_data
    : chartFrom(data.calls);
  const chartable = cd && Array.isArray(cd.values) && cd.values.length > 1 &&
    Array.isArray(cd.labels) && cd.labels.length === cd.values.length;

  if (kpi && !chartable) {
    const kpis = el('div', 'kpis');
    const item = el('div');
    const value = el('div', 'kpi-value', fmtNumber(kpi.value));
    value.dir = 'ltr';  // keep "52,000 EGP" in that order under an RTL answer
    if (kpi.unit) value.append(el('span', 'kpi-unit', kpi.unit));
    item.append(value, el('div', 'kpi-label', kpi.label));
    kpis.append(item);
    body.append(kpis);
  }

  if (chartable) body.append(chartContainer(cd));

  const table = tabularFrom(data.calls);
  if (table) body.append(dataTable(table.columns, table.rows, table.caption));

  if (data.ungrounded?.length) {
    const warn = el('div', 'grounding-warning');
    warn.textContent = `${data.ungrounded.join(', ')} — no tool returned ${
      data.ungrounded.length === 1 ? 'this figure' : 'these figures'}. Treat with caution.`;
    body.append(warn);
  }

  if (data.stopped) body.append(el('div', 'stopped-note', 'Stopped.'));

  if (data.calls?.length) body.append(rawDisclosure(data.calls));
  body.append(actionRow({ onRetry, copyText: data.answer || '' }));
}

function actionRow({ onRetry, copyText }) {
  const row = el('div', 'turn-actions');

  if (copyText != null) {
    const copy = el('button', 'text-btn');
    copy.type = 'button';
    copy.innerHTML = `${icon('copy', 14)}<span>Copy</span>`;
    copy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(copyText);
        copy.innerHTML = `${icon('check', 14)}<span>Copied</span>`;
        announce('Answer copied');
        setTimeout(() => { copy.innerHTML = `${icon('copy', 14)}<span>Copy</span>`; }, 1200);
      } catch { announce('Copying is not available in this browser'); }
    });
    row.append(copy);
  }

  if (onRetry) {
    const retry = el('button', 'text-btn');
    retry.type = 'button';
    retry.innerHTML = `${icon('retry', 14)}<span>Retry</span>`;
    retry.addEventListener('click', onRetry);
    row.append(retry);
  }
  return row;
}

function rawDisclosure(calls) {
  const details = el('details', 'raw');
  const summary = el('summary');
  summary.insertAdjacentHTML('afterbegin', `<span class="chev">${icon('chevron', 14)}</span>`);
  summary.append(document.createTextNode(
    `Database detail — ${calls.length} ${calls.length === 1 ? 'call' : 'calls'}`));
  const inner = el('div', 'raw-body');
  calls.forEach((c) => {
    inner.append(toolStatusRow(c.tool, c.args, c.result?.error ? 'blocked' : 'done', c.result));
    const pre = el('pre');
    pre.dir = 'ltr';
    pre.append(el('code', null, JSON.stringify(c.result, null, 2)));
    const fig = el('figure', 'code');
    fig.style.margin = '0';
    const cap = el('figcaption');
    cap.append(el('span', 'code-lang', 'json'));
    fig.append(cap, pre);
    inner.append(fig);
  });
  details.append(summary, inner);
  return details;
}

/* --- EmptyState / SuggestedPrompts ---------------------------------------- */

let SUGGESTED = [
  { text: 'What tables and views are in this database?', hint: 'lists tables and views' },
  { text: 'What data can I ask about?', hint: 'lists what is readable' },
];

function emptyState() {
  const wrap = el('div', 'empty-state');
  const intro = el('div', 'empty-intro');
  const h = el('h2', null, 'Ask about this database');
  const p = el('p', null,
    'The agent reads the live schema and answers from query results, never from memory.');
  intro.append(h, p);

  const grid = el('div', 'prompt-grid');
  SUGGESTED.forEach((s) => {
    const btn = el('button', 'prompt-btn');
    btn.type = 'button';
    btn.dir = 'auto';
    btn.append(document.createTextNode(s.text), el('span', null, s.hint));
    btn.addEventListener('click', () => submit(s.text));
    grid.append(btn);
  });

  wrap.append(intro, grid);
  return wrap;
}

/* --- MessageList ---------------------------------------------------------- */

function userTurn(content) {
  const turn = el('article', 'turn turn-user enter');
  const box = el('div', null, content);
  box.dir = 'auto';
  turn.append(box);
  return turn;
}

function renderMessages() {
  const list = $('#messageList');
  const conv = active();
  list.innerHTML = '';
  const turns = conv?.turns ?? [];
  $('#main').dataset.empty = String(turns.length === 0);
  $('#chatTitle').textContent = conv?.title ?? 'New chat';

  if (!turns.length) { list.append(emptyState()); return; }

  turns.forEach((t, idx) => {
    if (t.role === 'user') { list.append(userTurn(t.content)); return; }
    const { turn, tools, body } = assistantTurn();
    if (t.data?.calls?.length) tools.append(toolSummary(t.data.calls));
    renderAssistantResult(turn, body, t.data || {}, {
      // Drop this answer and the question that produced it, then ask again, so
      // retrying does not leave a duplicate of the question behind.
      onRetry: () => retryFrom(conv, idx - 1),
    });
    list.append(turn);
  });

  // A question with no answer after it means the page was reloaded or closed
  // while the agent was still working. Say so, and offer to ask again.
  if (turns.at(-1)?.role === 'user' && state.status !== 'working') {
    const { turn, body } = assistantTurn();
    turn.setAttribute('aria-busy', 'false');
    body.append(el('div', 'stopped-note',
      'This answer was interrupted before it arrived.'));
    body.append(actionRow({ onRetry: () => retryFrom(conv, turns.length - 1) }));
    list.append(turn);
  }

  hydrateIcons(list);
  wireCopyButtons(list);
}

function wireCopyButtons(root) {
  root.querySelectorAll('[data-copy]').forEach((btn) => {
    if (btn.dataset.wired) return;
    btn.dataset.wired = '1';
    btn.addEventListener('click', async () => {
      const code = btn.closest('.code')?.querySelector('code');
      if (!code) return;
      try {
        await navigator.clipboard.writeText(code.textContent);
        btn.innerHTML = icon('check', 15);
        announce('Code copied');
        setTimeout(() => { btn.innerHTML = icon('copy', 15); }, 1200);
      } catch { announce('Copying is not available in this browser'); }
    });
  });
}

function retryFrom(conv, userIndex) {
  const question = conv.turns[userIndex]?.content;
  if (!question) return;
  conv.turns = conv.turns.slice(0, userIndex);
  save();
  renderAll();
  submit(question);
}

function renderAll() {
  renderConversationList();
  renderMessages();
}

/* --- transport ------------------------------------------------------------ */

function previewAnswer(question) {
  const q = question.toLowerCase();
  if (q.includes('region') || q.includes('منطقة')) {
    return {
      answer: 'Revenue for the year is concentrated in Cairo, which contributed **105,600 EGP** across three customers — roughly two thirds of the total. Alexandria follows at 53,400 EGP.\n\n| Region | Revenue (EGP) |\n| --- | --- |\n| Cairo | 105,600 |\n| Alexandria | 53,400 |',
      chart_data: { type: 'pie', title: 'revenue by region', unit: 'EGP', labels: ['Cairo', 'Alexandria'], values: [105600, 53400] },
      calls: [{
        tool: 'execute_sql',
        args: { sql: 'SELECT region, SUM(amount) AS revenue FROM v_orders GROUP BY region' },
        result: { success: true, data: { statements: [{ sql: '', rows: [{ region: 'Cairo', revenue: 105600 }, { region: 'Alexandria', revenue: 53400 }], count: 2 }] } },
      }],
      ungrounded: [],
    };
  }
  if (q.includes('salary') || q.includes('راتب')) {
    return {
      answer: 'Sara Ibrahim (id 7201, Finance) has a salary of **52,000 EGP**.\n\nThe figure comes from a restricted column, so it is only returned while sensitive data is allowed.',
      calls: [
        { tool: 'search_objects', args: { keywords: 'employee salary' }, result: { objects: [{ name: 'v_employees_sensitive', type: 'view' }] } },
        {
          tool: 'execute_sql',
          args: { sql: "SELECT id, name_en, department, salary FROM v_employees_sensitive WHERE name_en = 'Sara Ibrahim'" },
          result: { success: true, data: { statements: [{ sql: '', rows: [{ id: 7201, name_en: 'Sara Ibrahim', department: 'Finance', salary: 52000 }], count: 1 }] } },
        },
      ],
      ungrounded: [],
    };
  }
  return {
    answer: 'Revenue for last quarter was **73,700 EGP** from four orders, an average of 18,425 EGP per order.\n\nThe quarter covers 2026-04-01 to 2026-06-30, and the figure is the sum of order amounts before returns and tax.',
    calls: [{
      tool: 'execute_sql',
      args: { sql: "SELECT SUM(amount) AS revenue FROM v_orders WHERE order_date BETWEEN '2026-04-01' AND '2026-06-30'" },
      result: { success: true, data: { statements: [{ sql: '', rows: [{ revenue: 73700 }], count: 1 }] } },
    }],
    ungrounded: [],
  };
}

async function runPreview(question, hooks) {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const data = previewAnswer(question);
  await wait(500);
  for (const call of data.calls) {
    hooks.onTool(call.tool, call.args);
    await wait(650);
    hooks.onToolDone(call.tool, call.args, call.result);
  }
  await wait(450);
  return data;
}

async function runStream(question, hooks, signal, history) {
  const res = await fetch('/api/ask/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, allow_sensitive: state.allowSensitive, history }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`stream unavailable (${res.status})`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let payload = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() ?? '';
    for (const frame of frames) {
      const line = frame.split('\n').find((l) => l.startsWith('data:'));
      if (!line) continue;
      let event;
      try { event = JSON.parse(line.slice(5).trim()); } catch { continue; }
      if (event.type === 'tool_start') hooks.onTool(event.name, event.args);
      else if (event.type === 'tool_end') hooks.onToolDone(event.name, event.args, event.result);
      else if (event.type === 'status') hooks.onStatus(event.state, event);
      else if (event.type === 'done') payload = event.payload;
      else if (event.type === 'error') throw new Error(event.message);
    }
  }
  if (!payload) throw new Error('the agent closed the connection before answering');
  return payload;
}

async function runBlocking(question, signal, history) {
  const res = await fetch('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, allow_sensitive: state.allowSensitive, history }),
    signal,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `request failed (${res.status})`);
  return data;
}

/* --- MessageComposer ------------------------------------------------------ */

const input = () => $('#composerInput');

function autoGrow() {
  const ta = input();
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 200) + 'px';
}

function setStatus(next) {
  state.status = next;
  const busy = next === 'working';
  const sendBtn = $('#sendBtn');
  sendBtn.hidden = busy;
  $('#stopBtn').hidden = !busy;
  sendBtn.disabled = busy || !input().value.trim();
}

function announce(text) { $('#liveRegion').textContent = text; }

const NEAR_BOTTOM = 140;
function stickToBottom(fn) {
  const conv = $('#conversation');
  const stick = conv.scrollHeight - conv.scrollTop - conv.clientHeight < NEAR_BOTTOM;
  fn();
  if (stick) conv.scrollTop = conv.scrollHeight;
}

async function submit(text) {
  const question = (text ?? input().value).trim();
  if (!question || state.status === 'working') return;

  let conv = active();
  if (!conv) conv = newConversation();
  if (!conv.turns.length) conv.title = question.slice(0, 60);
  conv.turns.push({ role: 'user', content: question });
  const userIndex = conv.turns.length - 1;
  conv.updatedAt = Date.now();
  save();

  input().value = '';
  autoGrow();
  setStatus('working');
  renderConversationList();

  const list = $('#messageList');
  $('#main').dataset.empty = 'false';
  $('#chatTitle').textContent = conv.title;
  if (list.querySelector('.empty-state')) list.innerHTML = '';
  list.append(userTurn(question));

  const { turn, tools, body } = assistantTurn();
  list.append(turn);
  showThinking(body);
  announce('Working on your question');
  stickToBottom(() => {});

  const rows = new Map();
  const hooks = {
    onTool(name, args) {
      const row = toolStatusRow(name, args, 'running');
      rows.set(`${name}:${JSON.stringify(args)}`, row);
      stickToBottom(() => tools.append(row));
      announce(`Querying the database: ${name}`);
    },
    onToolDone(name, args, result) {
      const row = rows.get(`${name}:${JSON.stringify(args)}`);
      if (row) {
        const replacement = toolStatusRow(name, args, result?.error ? 'blocked' : 'done', result);
        row.replaceWith(replacement);
      }
      showSkeleton(body);
    },
    onStatus(stateName) {
      if (stateName === 'regrounding') {
        showSkeleton(body, 'Checking the figures against the database');
        announce('Verifying the figures');
      }
    },
  };

  // Earlier turns let a follow-up such as "and in Alexandria?" resolve to
  // something concrete. The server trims this and treats it as wording only;
  // any figure still has to be fetched again.
  const history = conv.turns.slice(0, userIndex)
    .filter((t) => t.content)
    .map((t) => ({ role: t.role, content: t.content }));

  const controller = new AbortController();
  state.controller = controller;

  let data;
  try {
    if (PREVIEW) {
      data = await runPreview(question, hooks);
    } else {
      try {
        data = await runStream(question, hooks, controller.signal, history);
      } catch (err) {
        if (err.name === 'AbortError') throw err;
        showSkeleton(body, 'Working');
        data = await runBlocking(question, controller.signal, history);
      }
    }
  } catch (err) {
    data = err.name === 'AbortError'
      ? { answer: '', stopped: true, calls: [], ungrounded: [] }
      : { error: friendlyError(err) };
  } finally {
    state.controller = null;
  }

  tools.innerHTML = '';
  if (data.calls?.length) tools.append(toolSummary(data.calls));

  stickToBottom(() => {
    renderAssistantResult(turn, body, data, { onRetry: () => retryFrom(conv, userIndex) });
    hydrateIcons(turn);
    wireCopyButtons(turn);
  });

  conv.turns.push({ role: 'assistant', content: data.answer || '', data });
  conv.updatedAt = Date.now();
  save();
  // Switching conversations mid-request detaches the turn we were writing into.
  // Rebuild from state so a finished answer is never left invisible.
  if (!turn.isConnected && active() === conv) renderMessages();
  setStatus('idle');
  announce(data.error ? 'The request failed' : 'Answer ready');
  renderConversationList();
}

function friendlyError(err) {
  const msg = String(err?.message || err);
  if (/failed to fetch|networkerror/i.test(msg)) {
    return 'Could not reach the agent. Start it with "python webapp.py" and reload this page.';
  }
  if (/model|ollama|11434/i.test(msg)) {
    return `The local model did not respond. Check that Ollama is running, then retry. (${msg})`;
  }
  return `The request failed: ${msg}`;
}

/* --- sidebar / drawer ----------------------------------------------------- */

let lastFocused = null;
const isDrawer = () => window.innerWidth < 768;

function openSidebar() {
  lastFocused = document.activeElement;
  $('#app').dataset.sidebar = 'open';
  $('#sidebarToggle').setAttribute('aria-expanded', 'true');
  if (isDrawer()) {
    const sidebar = $('#sidebar');
    sidebar.setAttribute('role', 'dialog');
    sidebar.setAttribute('aria-modal', 'true');
    $('#main').setAttribute('aria-hidden', 'true');
    if (!$('#scrim')) {
      const scrim = el('div', 'scrim');
      scrim.id = 'scrim';
      scrim.addEventListener('click', closeDrawer);
      $('#app').append(scrim);
    }
    $('#newChatBtn').focus();
  }
}

function closeDrawer() {
  delete $('#app').dataset.sidebar;
  $('#sidebarToggle').setAttribute('aria-expanded', 'false');
  const sidebar = $('#sidebar');
  sidebar.removeAttribute('role');
  sidebar.removeAttribute('aria-modal');
  $('#main').removeAttribute('aria-hidden');
  $('#scrim')?.remove();
  lastFocused?.focus?.();
}

function toggleSidebar() {
  $('#app').dataset.sidebar === 'open' ? closeDrawer() : openSidebar();
}

/* --- theme ---------------------------------------------------------------- */

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const btn = $('#themeToggle');
  btn.innerHTML = icon(theme === 'dark' ? 'sun' : 'moon');
  btn.setAttribute('aria-label', `Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`);
  try { localStorage.setItem('cda.theme', theme); } catch { /* ignore */ }
  document.querySelectorAll('.chart-body canvas').forEach((c) => {
    const fig = c.closest('.chart');
    const title = fig?.querySelector('.chart-title')?.textContent || '';
    void title;
  });
  renderMessages();
}

/* --- boot ----------------------------------------------------------------- */

function syncScrollbarWidth() {
  const conv = $('#conversation');
  const width = Math.max(0, conv.offsetWidth - conv.clientWidth);
  document.documentElement.style.setProperty('--scrollbar-w', `${width}px`);
}

function boot() {
  hydrateIcons();

  const stopBtn = el('button', 'stop-btn');
  stopBtn.type = 'button';
  stopBtn.id = 'stopBtn';
  stopBtn.hidden = true;
  stopBtn.setAttribute('aria-label', 'Stop generating');
  stopBtn.innerHTML = icon('stop');
  stopBtn.addEventListener('click', () => {
    state.controller?.abort();
    announce('Stopped');
  });
  $('.composer-actions').append(stopBtn);

  let stored = null;
  try { stored = localStorage.getItem('cda.theme'); } catch { /* ignore */ }
  applyTheme(stored || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
  $('#themeToggle').addEventListener('click', () => {
    applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
  });

  load();
  if (!state.conversations.length) newConversation();
  else state.activeId = state.conversations[0].id;
  renderAll();

  $('#composer').addEventListener('submit', (e) => { e.preventDefault(); submit(); });

  const ta = input();
  ta.addEventListener('input', () => {
    autoGrow();
    $('#sendBtn').disabled = state.status === 'working' || !ta.value.trim();
  });
  const touch = window.matchMedia('(pointer: coarse)').matches;
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !touch) { e.preventDefault(); submit(); }
  });

  document.addEventListener('keydown', (e) => {
    const drawerOpen = $('#app').dataset.sidebar === 'open' && isDrawer();

    if (e.key === 'Escape') {
      if (state.status === 'working') { state.controller?.abort(); announce('Stopped'); }
      else if (drawerOpen) closeDrawer();
      return;
    }

    // While the drawer covers the page, Tab must not reach the content behind it.
    if (e.key === 'Tab' && drawerOpen) {
      const focusable = [...$('#sidebar').querySelectorAll('button, a[href], input, [tabindex]:not([tabindex="-1"])')]
        .filter((node) => !node.disabled && node.offsetParent !== null);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      else if (!$('#sidebar').contains(document.activeElement)) { e.preventDefault(); first.focus(); }
    }
  });

  $('#newChatBtn').addEventListener('click', () => {
    if (!active()?.turns.length) { input().focus(); return; }
    newConversation();
    save();
    renderAll();
    input().focus();
    if (isDrawer()) closeDrawer();
  });

  $('#clearBtn').addEventListener('click', () => {
    const conv = active();
    if (!conv) return;
    conv.turns = [];
    conv.title = 'New chat';
    save();
    renderAll();
    input().focus();
  });

  $('#searchInput').addEventListener('input', (e) => {
    state.search = e.target.value;
    renderConversationList();
  });

  $('#sidebarToggle').addEventListener('click', toggleSidebar);

  const sensitive = $('#sensitiveBtn');
  sensitive.addEventListener('click', () => {
    state.allowSensitive = !state.allowSensitive;
    sensitive.setAttribute('aria-pressed', String(state.allowSensitive));
    announce(state.allowSensitive
      ? 'Sensitive data allowed for this session'
      : 'Sensitive data blocked');
  });

  if (PREVIEW) {
    $('#previewBanner').hidden = false;
    $('#envMeta').textContent = 'preview · no server';
    hydrateIcons($('#previewBanner'));
  } else {
    fetch('/api/health')
      .then((r) => r.json())
      .then((h) => {
        const db = String(h.db || '').split('/').pop() || h.db;
        $('#envMeta').textContent = `${h.model} · ${db}`;
        $('#envMeta').title = `${h.model} · ${h.db}`;
      })
      .catch(() => { $('#envMeta').textContent = 'agent offline'; });
    fetch('/api/schema')
      .then((r) => r.json())
      .then((s) => {
        if (Array.isArray(s.prompts) && s.prompts.length) {
          SUGGESTED = s.prompts;
          if ($('#main').dataset.empty === 'true') renderAll();
        }
      })
      .catch(() => {});
  }

  syncScrollbarWidth();
  window.addEventListener('resize', syncScrollbarWidth);

  input().focus();
}

boot();
