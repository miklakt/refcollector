#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
refcollector.collect_refs

Orchestrates:
- Parse BibTeX
- Scan LaTeX project for citation occurrences
- Map to PDF pages + printed lineno (synctex binary + PyMuPDF)
- Render interactive HTML

Usage:
    python -m refcollector --tex main.tex --bib biblio.bib --out references.html

Debug:
    export REFCOLLECTOR_DEBUG=1
"""

from __future__ import annotations

import argparse
import re
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from .tex_scanner import scan_tex_project_citations
from .pdf_lineno import PdfLinenoResolver
from .html_render import render_html
from .latex_unicode import latex_to_unicode

# Debug flag (do not add extra debug prints; just gate the existing ones)
DEBUG = bool(os.environ.get("REFCOLLECTOR_DEBUG"))

# ----------------------------
# BibTeX parser (minimal but brace-aware)
# ----------------------------

class BibEntry:
    def __init__(self, entry_type: str, key: str, fields: Dict[str, str], order_index: int):
        self.entry_type = entry_type
        self.key = key
        self.fields = fields  # raw LaTeX values
        self.order_index = order_index
    def get(self, field: str, default: Optional[str] = None) -> Optional[str]:
        return self.fields.get(field.lower(), default)

def _strip_inline_comments(s: str) -> str:
    return "\n".join(re.sub(r"(?<!\\)%.*", "", line) for line in s.splitlines())

def _read_braced(s: str, i: int) -> Tuple[str, int]:
    assert s[i] == "{"
    depth = 0; j = i
    while j < len(s):
        c = s[j]
        if c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0: return s[i+1:j], j+1
        j += 1
    return s[i+1:], len(s)

def _read_quoted(s: str, i: int) -> Tuple[str, int]:
    assert s[i] == '"'
    j = i + 1; out = []
    while j < len(s):
        c = s[j]
        if c == '"' and s[j-1] != "\\": return "".join(out), j+1
        out.append(c); j += 1
    return "".join(out), j

def _read_value(s: str, i: int) -> Tuple[str, int]:
    while i < len(s) and s[i].isspace(): i += 1
    if i >= len(s): return "", i
    if s[i] == "{": return _read_braced(s, i)
    if s[i] == '"': return _read_quoted(s, i)
    j = i
    while j < len(s) and s[j] not in ",}": j += 1
    return s[i:j].strip(), j

def parse_bibtex(bib_text: str) -> List[BibEntry]:
    text = _strip_inline_comments(bib_text); entries: List[BibEntry] = []
    i = 0; order_index = 0
    while True:
        at = text.find("@", i)
        if at == -1: break
        m = re.match(r"@([A-Za-z]+)\s*[\(\{]", text[at:])
        if not m: i = at + 1; continue
        entry_type = m.group(1)
        brace_open_pos = at + m.end() - 1
        open_char = text[brace_open_pos]; close_char = "}" if open_char == "{" else ")"
        depth = 0; j = brace_open_pos
        while j < len(text):
            if text[j] == open_char: depth += 1
            elif text[j] == close_char:
                depth -= 1
                if depth == 0: break
            j += 1
        if j >= len(text): i = at + 1; continue
        body = text[brace_open_pos + 1 : j].strip()
        comma = body.find(",")
        if comma == -1: i = j + 1; continue
        key = body[:comma].strip()
        fields_str = body[comma + 1 :].strip()

        fields: Dict[str, str] = {}
        k = 0
        while k < len(fields_str):
            while k < len(fields_str) and (fields_str[k].isspace() or fields_str[k] == ","): k += 1
            if k >= len(fields_str): break
            fm = re.match(r"([A-Za-z][A-Za-z0-9_\-]*)\s*=", fields_str[k:])
            if not fm:
                nxt = fields_str.find(",", k)
                if nxt == -1: break
                k = nxt + 1; continue
            fname = fm.group(1).lower(); k += fm.end()
            val, k = _read_value(fields_str, k)
            fields[fname] = val.strip()

        entries.append(BibEntry(entry_type, key, fields, order_index))
        order_index += 1; i = j + 1
    return entries

# ----------------------------
# Citation numbering from source order (handles clustered \cite{a,b,c})
# ----------------------------

# Same command set as scanner
_CITE_PATTERN = re.compile(
    r"""\\(?:
        cite|citet|citep|Cite|Citet|Citep|
        parencite|textcite|autocite|smartcite|
        footcite|footcitetext|
        citeauthor|Citeauthor
    )\*?
    (?:\s*\[[^\]]*\]){0,2}
    \s*\{([^}]*)\}""",
    re.VERBOSE,
)
# Includes in raw order
_INCLUDE_RE = re.compile(
    r"""\\(?:(?:input)|(?:include)|(?:subfile))\s*\{([^}]+)\}""",
    re.IGNORECASE,
)

def _strip_env_blocks(text: str, envs: List[str]) -> str:
    for env in envs:
        pat = re.compile(r"\\begin\{" + re.escape(env) + r"\}.*?\\end\{" + re.escape(env) + r"\}",
                         re.IGNORECASE | re.DOTALL)
        text = pat.sub("", text)
    return text

def _strip_iffalse_blocks(text: str) -> str:
    pat = re.compile(r"\\iffalse\b.*?\\fi\b", re.IGNORECASE | re.DOTALL)
    prev = None
    while prev != text:
        prev = text
        text = pat.sub("", text)
    return text

def _preprocess_source_for_numbering(text: str) -> str:
    # Mirror tex_scanner semantics: remove comment/verbatim-like/iffalse, then strip line comments.
    text = _strip_env_blocks(text, ["comment", "verbatim", "lstlisting", "minted"])
    text = _strip_iffalse_blocks(text)
    text = "\n".join(re.sub(r"(?<!\\)%.*", "", line) for line in text.splitlines())
    return text

def _resolve_included_path(base: Path, arg: str) -> Path:
    p = Path(arg)
    if not p.suffix:
        p = p.with_suffix(".tex")
    if not p.is_absolute():
        p = (base.parent / p).resolve()
    return p

def compute_citation_numbers(main_tex: Path) -> Dict[str, int]:
    """
    Assign numbers in the exact order keys first appear in source, respecting
    order inside each \\cite{a,b,c} cluster. Recurses into includes.
    """
    visited: set[Path] = set()
    counter = 0
    numbers: Dict[str, int] = {}

    def scan_file(tex_path: Path):
        nonlocal counter
        tex_path = tex_path.resolve()
        if tex_path in visited:
            return
        visited.add(tex_path)
        try:
            raw = tex_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            raw = tex_path.read_text(errors="ignore")

        # Follow includes in raw order (match compilation traversal)
        for m in _INCLUDE_RE.finditer(raw):
            inc = m.group(1).strip()
            if inc:
                incp = _resolve_included_path(tex_path, inc)
                if incp.exists():
                    scan_file(incp)

        # Strip comments/blocked regions; then scan linearly for \cite{...}
        cleaned = _preprocess_source_for_numbering(raw)
        for m in _CITE_PATTERN.finditer(cleaned):
            keys_str = m.group(1)
            keys = [k.strip() for k in keys_str.split(",") if k.strip()]
            for key in keys:
                if key not in numbers:
                    counter += 1
                    numbers[key] = counter

    scan_file(main_tex)
    return numbers

# ----------------------------
# Build output (cards JSON)
# ----------------------------

def split_authors(author_field: Optional[str]) -> List[str]:
    if not author_field: return []
    parts = re.split(r"\s+\band\b\s+", author_field)
    return [latex_to_unicode(p.strip()) for p in parts if p.strip()]

def build_output_data(
    bib_entries: List[BibEntry],
    occurrences: Dict[str, List[Dict[str, object]]],
    pdf_resolver: Optional[PdfLinenoResolver] = None,
    cit_numbers: Optional[Dict[str, int]] = None,
) -> List[Dict[str, object]]:
    cards: List[Dict[str, object]] = []

    for be in bib_entries:
        k = be.key
        occs = occurrences.get(k, [])


        if DEBUG:
            print(f"{k}: {len(occs)} occurrence(s)")

        # Skip bib entries with no occurrences in the LaTeX source
        if not occs:
            continue


        occ_list = []
        best_order = None

        for idx, o in enumerate(occs, start=1):
            abs_file = Path(str(o["file"])).resolve()
            ln = int(o["line"])
            col = int(o["column"])
            page = printed = None

            if pdf_resolver is not None:
                page, printed = pdf_resolver.resolve(abs_file, ln, column=col)
                lino_ord = printed if printed is not None else 10**9
                occ_order = (page if page is not None else 10**9, lino_ord, ln)
                if best_order is None or occ_order < best_order:
                    best_order = occ_order

            if DEBUG:
                print(f"{idx}) tex line {ln}, tex column {col}, pdf page {page}, pdf line {printed}")

            occ_list.append({
                "file": str(abs_file),
                "line": ln,
                "pdfPage": page,
                "pdfLineno": printed,
                "snippet": o.get("snippet"),
            })

        if best_order is None:
            min_src = min((int(o["line"]) for o in occs), default=10**12)
            best_order = (10**9, 10**9, min_src)

        title = latex_to_unicode(be.get("title", "") or "")
        abstract = latex_to_unicode(be.get("abstract", "") or "")
        url = (be.get("url") or "").strip() or None
        doi = (be.get("doi") or "").strip() or None
        if doi: doi = doi.strip("{} \t\r\n")
        year_raw = be.get("year")
        try:
            year = int(re.findall(r"\d{4}", year_raw or "")[0]) if year_raw else None
        except Exception:
            year = None

        page_part, lino_part, src_part = best_order
        occ_score = int(page_part) * 1_000_000 + int(lino_part) * 1_000 + int(src_part)

        cards.append({
            "key": be.key,
            "title": title,
            "authors": split_authors(be.get("author")),
            "year": year,
            "doi": doi,
            "url": url,
            "abstract": abstract,
            "occurrences": occ_list,
            "firstOccurrence": best_order,
            "occScore": occ_score,
            "bibIndex": be.order_index,
            "orderNum": (cit_numbers.get(k) if cit_numbers else None),
        })

    # Sort by PDF-first for consistent UI order
    cards_sorted_idx = sorted(
        range(len(cards)),
        key=lambda i: (cards[i]["firstOccurrence"], cards[i]["bibIndex"], cards[i]["key"])
    )

    # Fill missing orderNum (uncited keys): assign smallest unused positive ints in PDF order
    used = {c["orderNum"] for c in cards if c["orderNum"] is not None}
    next_num = 1
    for idx in cards_sorted_idx:
        if cards[idx]["orderNum"] is None:
            while next_num in used:
                next_num += 1
            cards[idx]["orderNum"] = next_num
            used.add(next_num)
            next_num += 1

    return cards

# ----------------------------
# CLI
# ----------------------------

def main(argv: Optional[List[str]] = None):
    ap = argparse.ArgumentParser(description="Collect references and occurrences from LaTeX + BibTeX into an HTML dashboard.")
    ap.add_argument("--tex", required=True, help="Path to the main .tex file")
    ap.add_argument("--bib", required=True, help="Path to the .bib file")
    ap.add_argument("--out", default="references.html", help="Output HTML file path (default: references.html)")
    args = ap.parse_args(argv)

    tex_path = Path(args.tex).resolve()
    bib_path = Path(args.bib).resolve()
    out_path = Path(args.out).resolve()

    if not tex_path.exists():
        print(f"ERROR: tex file not found: {tex_path}", file=sys.stderr); sys.exit(1)
    if not bib_path.exists():
        print(f"ERROR: bib file not found: {bib_path}", file=sys.stderr); sys.exit(1)

    # Parse BibTeX
    try:
        bib_text = bib_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        bib_text = bib_path.read_text(errors="ignore")
    bib_entries = parse_bibtex(bib_text)

    # Scan LaTeX project for citations (main + includes)
    cit_occ = scan_tex_project_citations(tex_path)

    # Compute citation numbers from source order (handles clusters)
    cit_numbers = compute_citation_numbers(tex_path)

    # PDF resolver (synctex binary + PyMuPDF)
    pdf_path = tex_path.with_suffix(".pdf")
    pdf_resolver = PdfLinenoResolver(pdf_path, tex_main=tex_path) if pdf_path.exists() else None

    # Build cards (with PDF mapping + numbering)
    cards = build_output_data(bib_entries, cit_occ, pdf_resolver=pdf_resolver, cit_numbers=cit_numbers)

    default_view = "pdf" if pdf_resolver is not None else "tex"

    # Render HTML
    page_title = f"References for {tex_path.name}"
    html_str = render_html(page_title, cards, default_view)
    out_path.write_text(html_str, encoding="utf-8")
    print(f"Wrote {out_path}")

if __name__ == "__main__":
    main()
