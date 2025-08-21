# refcollector/tex_scanner.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Set

# Matches common LaTeX citation commands and variants
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

# ----------------------------
# Comment / block stripping
# ----------------------------

def _strip_line_comments(text: str) -> str:
    """Remove everything after an unescaped % on each line."""
    out_lines = []
    for line in text.splitlines():
        # Remove % ... unless the % is escaped as \%
        line = re.sub(r"(?<!\\)%.*", "", line)
        out_lines.append(line)
    return "\n".join(out_lines)

def _strip_env_blocks(text: str, env_names: List[str]) -> str:
    """
    Remove entire blocks for the given environments (non-nested, DOTALL).
    Example: env_names=['comment','verbatim','lstlisting','minted']
    """
    for env in env_names:
        # \begin{env}[...]\n ... \n\end{env}
        pattern = re.compile(
            r"\\begin\{" + re.escape(env) + r"\}.*?\\end\{" + re.escape(env) + r"\}",
            re.DOTALL | re.IGNORECASE,
        )
        text = pattern.sub("", text)
    return text

def _strip_iffalse_blocks(text: str) -> str:
    """
    Remove \iffalse ... \fi regions (simple, non-nested).
    If nesting exists, this removes outermost pairs greedily.
    """
    # Use a loop to handle multiple regions
    pat = re.compile(r"\\iffalse\b.*?\\fi\b", re.DOTALL | re.IGNORECASE)
    prev = None
    while prev != text:
        prev = text
        text = pat.sub("", text)
    return text

def _preprocess_source(text: str) -> str:
    """
    Order matters:
      1) Remove block-like regions first (comment/verbatim/lstlisting/minted + \iffalse...\fi),
      2) Then strip line comments to be safe,
      3) Return cleaned text for per-line scanning.
    """
    text = _strip_env_blocks(text, ["comment", "verbatim", "lstlisting", "minted"])
    text = _strip_iffalse_blocks(text)
    text = _strip_line_comments(text)
    return text

# ----------------------------
# File inclusion discovery
# ----------------------------

# \input{file}, \include{file}, \subfile{file}
_INCLUDE_RE = re.compile(
    r"""\\(?:(?:input)|(?:include)|(?:subfile))\s*\{([^}]+)\}""",
    re.IGNORECASE,
)

def _resolve_included_path(base: Path, arg: str) -> Path:
    """
    Resolve included file path relative to 'base' file's directory.
    Add .tex if no suffix is provided.
    """
    p = Path(arg)
    if not p.suffix:
        p = p.with_suffix(".tex")
    if not p.is_absolute():
        p = (base.parent / p).resolve()
    return p

# ----------------------------
# Public API
# ----------------------------

def scan_tex_project_citations(main_tex_path: Path) -> Dict[str, List[Dict[str, object]]]:
    r"""
    Recursively scan the LaTeX project starting from main_tex_path.
    Returns mapping: citation_key -> list of occurrences:
      {'file': str, 'line': int, 'column': int, 'snippet': str}

    Notes:
      - Ignores citations inside line comments (% ...), comment environments,
        verbatim-like blocks, and \iffalse ... \fi regions.
      - 'column' is 1-based and points to the first character of each key
        inside a cluster, e.g. \cite{A, B, C} yields different columns.
    """
    occurrences: Dict[str, List[Dict[str, object]]] = {}
    visited: Set[Path] = set()

    # Simple stateful skipping for environments and \iffalse...\fi (non-nested for \iffalse)
    ENV_SKIP = {"comment", "verbatim", "lstlisting", "minted"}
    begin_env_re = re.compile(r"\\begin\{(" + "|".join(map(re.escape, ENV_SKIP)) + r")\}", re.IGNORECASE)
    end_env_re   = re.compile(r"\\end\{("   + "|".join(map(re.escape, ENV_SKIP)) + r")\}", re.IGNORECASE)
    iff_re = re.compile(r"\\iffalse\b", re.IGNORECASE)
    fi_re  = re.compile(r"\\fi\b", re.IGNORECASE)

    # For clustered keys inside {...}, capture each key with its local start
    _KEY_ITEM_RE = re.compile(r"\s*([^,]+?)\s*(?:,|$)")

    def first_unescaped_percent(s: str) -> int:
        """Return index of first unescaped % or len(s) if none."""
        m = re.search(r"(?<!\\)%", s)
        return m.start() if m else len(s)

    def scan_file(tex_path: Path):
        tex_path = tex_path.resolve()
        if tex_path in visited:
            return
        visited.add(tex_path)

        try:
            raw = tex_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            raw = tex_path.read_text(errors="ignore")

        # Follow includes in raw order (to mirror compilation traversal)
        for m in _INCLUDE_RE.finditer(raw):
            inc = m.group(1).strip()
            if inc:
                inc_path = _resolve_included_path(tex_path, inc)
                if inc_path.exists():
                    scan_file(inc_path)

        # Line-by-line scanning with simple block states
        in_block_env = False
        in_block_name = None
        in_iffalse = False

        lines = raw.splitlines(keepends=False)
        for idx, full_line in enumerate(lines, start=1):
            line = full_line

            # Handle \iffalse ... \fi (simple, non-nested)
            if not in_iffalse and iff_re.search(line):
                in_iffalse = True
            if in_iffalse:
                if fi_re.search(line):
                    in_iffalse = False
                continue  # skip line content while in \iffalse block

            # Handle verbatim-like/comment environments
            if not in_block_env:
                m_begin = begin_env_re.search(line)
                if m_begin:
                    in_block_env = True
                    in_block_name = m_begin.group(1).lower()
                    continue  # skip line with \begin{...}
            else:
                m_end = end_env_re.search(line)
                if m_end:
                    # close only if names match, otherwise keep skipping
                    name = m_end.group(1).lower()
                    if in_block_name == name:
                        in_block_env = False
                        in_block_name = None
                    # in any case, skip this line
                continue

            # Strip trailing line comment (after first unescaped %)
            cut = first_unescaped_percent(line)
            scan_segment = line[:cut]
            if "\\" not in scan_segment:
                continue

            # Find \cite-like commands on this line segment
            for m in _CITE_PATTERN.finditer(scan_segment):
                keys_str = m.group(1)
                group_start = m.start(1)  # index within scan_segment of the '{...}' content
                # iterate each key in the group with local offsets
                pos = 0
                while pos <= len(keys_str):
                    km = _KEY_ITEM_RE.match(keys_str, pos)
                    if not km:
                        break
                    key = km.group(1).strip()
                    # Calculate the column at which THIS key starts in the raw line (1-based)
                    key_local_start = km.start(1)  # start within keys_str
                    abs_col = group_start + key_local_start + 1  # +1 for 1-based column
                    if key:
                        snippet = scan_segment.strip()
                        if len(snippet) > 240:
                            snippet = snippet[:240] + "…"
                        occurrences.setdefault(key, []).append({
                            "file": str(tex_path),
                            "line": idx,
                            "column": abs_col,
                            "snippet": snippet
                        })
                    # advance to next item (after comma or end)
                    if km.end() == pos:
                        pos += 1
                    else:
                        pos = km.end()

    scan_file(main_tex_path)
    return occurrences

