# refcollector

Collect LaTeX citations from a project (main `.tex` + includes), parse BibTeX, map each occurrence to PDF **page** and printed **lineno** (when using the `lineno` package), and generate an interactive HTML dashboard of references.

## Features
- Recursively scans `\input{}`, `\include{}`, `\subfile{}`.
- SyncTeX + `pdftotext -bbox` to get **printed** line numbers (no Python PDF libs).
- Cards with key/link/doi pills, title, authors, collapsible abstract, and per-occurrence collapsible context.
- Toggle: PDF view (`page N • line L` or `page N`/`unmapped`) vs raw TeX line view.
- Sort: by first occurrence, year, or `.bib` order.
- Shows which aux files (`.aux`, `.bbl`, `.log`, `.synctex.gz`) were found.

## Install
```bash
pip install .
# or build
python -m build  # if you have 'build' installed
```

## Usage
```bash
collect-refs --tex path/to/main.tex --bib path/to/biblio.bib --out references.html
```

> Requires TeX build with SyncTeX: `latexmk -synctex=1 -pdf main.tex`
> And CLI tools on PATH: `synctex` (TeX Live) and `pdftotext` (Poppler). If not present, PDF view shows `unmapped` and does not fall back to TeX line numbers.
