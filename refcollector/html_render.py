# refcollector/html_render.py
from __future__ import annotations

import html
import json
from string import Template
from typing import List, Dict, Any

def _escape_json_for_script(s: str) -> str:
    # keep </script and <!-- from breaking out of the tag
    return s.replace("</", "<\\/").replace("<!--", "<\\!--")

HTML_TEMPLATE = Template(r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>$PAGE_TITLE</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      --bg: #ffffff; --text: #0b1220; --card: #ffffff; --muted: #5b6576;
      --pill: #eef2f8; --accent: #1f6feb; --accent2: #0b5bd3; --border: #e2e8f0;
      --shadow: 0 6px 24px rgba(0,0,0,0.08);
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji"; background: var(--bg); color: var(--text); }
    header { position: sticky; top: 0; z-index: 10; background: rgba(255,255,255,0.9); backdrop-filter: blur(8px); border-bottom: 1px solid var(--border); }
    .wrap { max-width: 1100px; margin: 0 auto; padding: 1rem; }
    h1 { margin: 0.2rem 0 0.6rem; font-size: 1.35rem; letter-spacing: 0.2px; }
    .toolbar { display: flex; gap: 1rem; flex-wrap: wrap; align-items: center; }
    .group { display: flex; gap: 0.5rem; align-items: center; padding: 0.5rem 0; }
    select, .pill, button { background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 12px; padding: 0.35rem 0.6rem; font-size: 0.95rem; }
    select { cursor: pointer; }
    button#printBtn { cursor: pointer; background: var(--accent); color: white; border: none; padding: 0.35rem 0.8rem; border-radius: 12px; }
    .pill { display: inline-flex; align-items: center; gap: 6px; background: var(--pill); border-color: var(--border); color: var(--accent2); font-weight: 600; }
    .pill a { text-decoration: none; color: var(--accent2); font-weight: 600; }
    .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; padding: 1rem; }
    .card { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 0.9rem 0.95rem 0.7rem; box-shadow: var(--shadow); }
    .row { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
    .title { font-size: 1.02rem; font-weight: 700; margin: 0 0 5px; line-height: 1.25; }
    .sub { font-size: 0.92rem; color: var(--muted); margin-bottom: 8px; }
    details.abs { border: 1px dashed var(--border); border-radius: 10px; padding: 6px 8px; margin: 6px 0 10px; background: #fafcff; }
    details.abs > summary { cursor: pointer; color: var(--accent); font-weight: 600; }
    ul.occs { list-style: none; padding-left: 0; margin: 0; }
    ul.occs li { border: 1px solid var(--border); border-radius: 10px; padding: 6px 8px; margin-bottom: 6px; background: #fbfdff; }
    details.ctx { display: block; }
    details.ctx summary { list-style: none; display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
    details.ctx summary::-webkit-details-marker { display: none; }
    details.ctx summary::before { content: "▸"; color: var(--accent); font-size: 0.85rem; transition: transform 0.15s ease; }
    details.ctx[open] summary::before { transform: rotate(90deg); }
    .occ-meta { color: var(--muted); font-size: 0.85rem; }
    .occ-text { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.88rem; white-space: pre-wrap; color: #2b3850; padding-top: 4px; }
    .mutetext { color: var(--muted); }

    /* === PRINT-FRIENDLY STYLES === */
    @media print {
      .toolbar { display: none !important; }
      details.abs, details.ctx { border: none; padding: 0; background: transparent; }

      /* Hide only Abstract summary, keep occurrence labels */
      details.abs > summary { display: none !important; }
      details.ctx > summary { display: inline-flex !important; }

      .cards { display: block; }
      .card { break-inside: avoid; page-break-inside: avoid; margin-bottom: 12pt; }
      .row { gap: 4px; }
      .occ-meta { color: #000; }

      a[href^="http"]::after {
        content: " <" attr(href) ">";
        font-size: 9pt;
        color: #555;
      }
    }
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <h1>Reference Collector</h1>
      <div class="toolbar">
        <div class="group">
          <span class="mutetext">Occurrences view:</span>
          <select id="occView">
            <option value="pdf">PDF (page • line)</option>
            <option value="tex">Raw TeX lines</option>
          </select>
        </div>
        <div class="group">
          <span class="mutetext">Sort by:</span>
          <select id="sortMode">
            <option value="occ">First occurrence</option>
            <option value="year">Year</option>
            <option value="bib">.bib order</option>
          </select>
        </div>
        <div class="group">
          <button id="printBtn">Save as PDF</button>
        </div>
      </div>
    </div>
  </header>

  <main class="wrap">
    <div id="cards" class="cards"></div>
  </main>
  <footer>
    Generated by <span class="mutetext">refcollector</span>
  </footer>

<script>
  const DATA = $CARDS_JSON;
  const DEFAULT_VIEW = "$DEFAULT_VIEW";

  function e(tag, opts) {
    const el = document.createElement(tag);
    if (!opts) return el;
    if (opts.class) el.className = opts.class;
    if (opts.text != null) el.textContent = opts.text;
    if (opts.html != null) el.innerHTML = opts.html;
    if (opts.attrs) for (const [k,v] of Object.entries(opts.attrs)) el.setAttribute(k, v);
    return el;
  }

  function renderCard(ref) {
    const card = e('div', {class:'card'});

    // Pills row: [order] [key] [link] [doi]
    const pillrow = e('div', {class:'row'});
    const pillOrder = e('span', {class:'pill'});
    pillOrder.innerHTML = '<strong>' + String(ref.orderNum ?? '') + '</strong>';
    pillrow.appendChild(pillOrder);

    const pillKey = e('span', {class:'pill'});
    const strong = e('strong'); strong.textContent = ref.key || '(no key)';
    pillKey.appendChild(strong); pillrow.appendChild(pillKey);

    if (ref.url) {
      const pillUrl = e('span', {class:'pill'});
      const a = e('a', {attrs:{href: ref.url, target:'_blank', rel:'noopener'}});
      a.textContent = 'link'; pillUrl.appendChild(a); pillrow.appendChild(pillUrl);
    }
    if (ref.doi) {
      const pillDoi = e('span', {class:'pill'});
      const a = e('a', {attrs:{href: 'https://doi.org/' + encodeURIComponent(ref.doi), target:'_blank', rel:'noopener'}});
      a.textContent = 'doi'; pillDoi.appendChild(a); pillrow.appendChild(pillDoi);
    }
    card.appendChild(pillrow);

    // Title + authors
    const title = e('div', {class:'title'}); title.textContent = ref.title || '(no title)'; card.appendChild(title);
    const sub = e('div', {class:'sub'});
    const yearTxt = ref.year ? ' (' + ref.year + ')' : '';
    sub.textContent = (ref.authors && ref.authors.length ? ref.authors.join(', ') : '(authors unknown)') + yearTxt;
    card.appendChild(sub);

    // Abstract
    if (ref.abstract) {
      const det = e('details', {class:'abs'});
      det.appendChild(e('summary', {text:'Abstract'}));
      const abs = e('div'); abs.textContent = ref.abstract; det.appendChild(abs);
      card.appendChild(det);
    }

    // Occurrences list
    card.appendChild(e('div', {class:'mutetext', text:'Occurrences'}));
    const ul = e('ul', {class:'occs'});
    (ref.occurrences || []).forEach((occ) => {
      const li = e('li');
      const ctx = e('details', {class:'ctx'});
      const summary = e('summary');

      const meta = e('span', {class:'occ-meta'});
      const texLabel = 'line ' + occ.line;
      let pdfLabel = '';
      if (occ.pdfPage != null) {
        pdfLabel = 'page ' + occ.pdfPage + (occ.pdfLineno != null ? ' • line ' + occ.pdfLineno : '');
      }
      meta.setAttribute('data-tex-label', texLabel);
      meta.setAttribute('data-pdf-label', pdfLabel);
      meta.textContent = (DEFAULT_VIEW === 'pdf' && pdfLabel) ? pdfLabel : texLabel;

      summary.appendChild(meta);
      ctx.appendChild(summary);

      const txt = e('div', {class:'occ-text'});
      txt.textContent = occ.snippet || '';
      ctx.appendChild(txt);

      li.appendChild(ctx);
      ul.appendChild(li);
    });
    card.appendChild(ul);

    // data-* attributes used for sorting
    card.setAttribute('data-key', ref.key);
    card.setAttribute('data-year', ref.year != null ? String(ref.year) : '');
    card.setAttribute('data-bib', String(ref.bibIndex ?? 0));
    card.setAttribute('data-occ', String(ref.orderNum ?? 999999999));

    return card;
  }

  function renderCards() {
    const cont = document.getElementById('cards');
    cont.innerHTML = '';
    DATA.forEach(ref => cont.appendChild(renderCard(ref)));
  }

  function applyOccView(view) {
    const metas = document.querySelectorAll('.occ-meta');
    metas.forEach(m => {
      const pdf = m.getAttribute('data-pdf-label');
      const tex = m.getAttribute('data-tex-label');
      if (view === 'pdf' && pdf) m.textContent = pdf;
      else m.textContent = tex;
    });
  }

  function sortCards(mode) {
    const cont = document.getElementById('cards');
    const cards = Array.from(cont.children);
    const getNum = (el, attr, def=1e15) => {
      const v = el.getAttribute(attr);
      if (!v) return def;
      const n = Number(v);
      return Number.isFinite(n) ? n : def;
    };
    if (mode === 'year') {
      cards.sort((a,b) => getNum(b,'data-year',-1) - getNum(a,'data-year',-1) || getNum(a,'data-occ') - getNum(b,'data-occ'));
    } else if (mode === 'bib') {
      cards.sort((a,b) => getNum(a,'data-bib') - getNum(b,'data-bib'));
    } else {
      cards.sort((a,b) => getNum(a,'data-occ') - getNum(b,'data-occ'));
    }
    cards.forEach(c => cont.appendChild(c));
  }

  // ==== PRINTING LOGIC ====
  let _prevOpen = new WeakMap();
  let _prevViewValue = null;

  function expandAllDetails() {
    document.querySelectorAll('details').forEach(d => {
      if (d.open) _prevOpen.set(d, true);
      d.setAttribute('open', '');
    });
  }
  function restoreDetails() {
    document.querySelectorAll('details').forEach(d => {
      if (!_prevOpen.get(d)) d.removeAttribute('open');
    });
    _prevOpen = new WeakMap();
  }

  function switchToPdfViewForPrint() {
    const occSel = document.getElementById('occView');
    if (!occSel) return;
    _prevViewValue = occSel.value;
    occSel.value = 'pdf';
    applyOccView('pdf');
  }
  function restoreViewAfterPrint() {
    const occSel = document.getElementById('occView');
    if (!occSel) return;
    if (_prevViewValue) {
      occSel.value = _prevViewValue;
      applyOccView(_prevViewValue);
    }
    _prevViewValue = null;
  }

  window.addEventListener('beforeprint', () => {
    switchToPdfViewForPrint();
    expandAllDetails();
  });
  window.addEventListener('afterprint', () => {
    restoreDetails();
    restoreViewAfterPrint();
  });

  function setupPrintButton() {
    const btn = document.getElementById('printBtn');
    if (!btn) return;
    btn.addEventListener('click', () => {
      switchToPdfViewForPrint();
      expandAllDetails();
      setTimeout(() => window.print(), 0);
    });
  }

  (function(){
    renderCards();
    const occSel = document.getElementById('occView');
    const sortSel = document.getElementById('sortMode');
    occSel.value = DEFAULT_VIEW; applyOccView(DEFAULT_VIEW);
    sortSel.value = 'occ'; sortCards('occ');
    occSel.addEventListener('change', () => applyOccView(occSel.value));
    sortSel.addEventListener('change', () => sortCards(sortSel.value));
    setupPrintButton();
  })();
</script>
</body>
</html>
""")


def render_html(page_title: str, cards: List[Dict[str, Any]], default_view: str) -> str:
    page_title_safe = html.escape(page_title)
    cards_json = json.dumps(cards, ensure_ascii=False)
    html_str = HTML_TEMPLATE.substitute(
        PAGE_TITLE=page_title_safe,
        CARDS_JSON=_escape_json_for_script(cards_json),
        DEFAULT_VIEW=default_view,
    )
    return html_str
