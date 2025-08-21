import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, List, Dict

# Debug toggle via env var
DEBUG = False


WORD_CHAR_RE = re.compile(r"\w", re.UNICODE)
UNIT_PT_PER_IN = 72.27

@dataclass
class SyncTeXResult:
    page: int
    x: float
    y: float

def check_synctex_available(exe: str = "synctex") -> None:
    if shutil.which(exe) is None:
        raise FileNotFoundError(
            "The 'synctex' executable was not found in PATH. "
            "Install a TeX distribution that provides it (e.g., TeX Live) "
            "and compile with -synctex=1."
        )

def synctex_view(tex_path: Path, pdf_path: Path, line: int, column: int, synctex_exe: str = "synctex") -> SyncTeXResult:
    """Call synctex to map (tex file, line, column) -> (page, x, y) in PDF coordinates."""
    check_synctex_available(synctex_exe)
    cmd = [synctex_exe, "view", "-i", f"{line}:{column}:{str(tex_path.resolve())}", "-o", str(pdf_path.resolve())]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"synctex failed: {e.stderr.strip() or e.stdout.strip()}") from e

    page = x = y = None
    for line_txt in proc.stdout.splitlines():
        line_txt = line_txt.strip()
        if line_txt.startswith("Page:"):
            page = int(line_txt.split(":", 1)[1].strip())
        elif line_txt.startswith("x:"):
            x = float(line_txt.split(":", 1)[1].strip())
        elif line_txt.startswith("y:"):
            y = float(line_txt.split(":", 1)[1].strip())
        if page is not None and x is not None and y is not None:
            break
    if page is None or x is None or y is None:
        raise RuntimeError(f"Could not parse page/x/y from synctex output:\n{proc.stdout}")
    return SyncTeXResult(page=page, x=x, y=y)

# ----------------------------
# Helpers for margin band detection (PyMuPDF)
# ----------------------------

def _cluster_by_x(spans, eps=12.0):
    """Simple 1-D clustering on x using threshold 'eps'. Spans: (x0, y0, x1, y1, text)"""
    pts = sorted(spans, key=lambda t: 0.5*(t[0]+t[2]))
    clusters = []
    cur = []
    last_xc = None
    for x0, y0, x1, y1, s in pts:
        xc = 0.5*(x0+x1)
        if last_xc is None or abs(xc - last_xc) <= eps:
            cur.append((x0, y0, x1, y1, s))
        else:
            clusters.append(cur)
            cur = [(x0, y0, x1, y1, s)]
        last_xc = xc
    if cur:
        clusters.append(cur)
    return clusters

def _cluster_score_monotone(cluster, y_gap_min=6.0, y_gap_max=24.0):
    """
    Score a cluster by vertical monotonicity + density.
    - Sort by y_center; count increasing steps with reasonable gaps.
    """
    items = sorted(cluster, key=lambda t: 0.5*(t[1]+t[3]))
    ycs = [0.5*(y0+y1) for _, y0, _, y1, _ in items]
    good = 0
    gaps = []
    for i in range(1, len(ycs)):
        dy = ycs[i] - ycs[i-1]
        if dy > 0:
            gaps.append(dy)
            if y_gap_min <= dy <= y_gap_max:
                good += 1
    n = len(items)
    if n <= 1:
        return 0.0, {"n": n, "good": good, "median_gap": None}
    gaps_sorted = sorted(gaps) if gaps else []
    med_gap = gaps_sorted[len(gaps_sorted)//2] if gaps_sorted else None
    var_penalty = 0.0
    if gaps:
        mean = sum(gaps)/len(gaps)
        var = sum((g-mean)**2 for g in gaps)/len(gaps)
        var_penalty = min(var, 25.0)
    score = good + 0.1*n - 0.02*var_penalty
    return score, {"n": n, "good": good, "median_gap": med_gap}

def auto_detect_margin_band(pdf_path: Path, prefer_side: Optional[str], page_hint: Optional[int] = None) -> Dict[str, float]:
    """
    Detect the lineno margin digits column by clustering numeric tokens along x.
    Returns: dict with side ('left'|'right'), x_min/x_max bounds, y_tol, page_width.
    """
    import fitz  # PyMuPDF
    doc = fitz.open(str(pdf_path))
    W = doc[0].rect.width
    pages_to_scan = list(range(1, min(6, doc.page_count+1)))
    if page_hint and 1 <= page_hint <= doc.page_count and page_hint not in pages_to_scan:
        pages_to_scan = [page_hint] + pages_to_scan

    best = None  # (score, page_no, cluster, side)
    heights = []
    for pno in pages_to_scan:
        page = doc.load_page(pno-1)
        words = page.get_text("words")
        spans = []
        for x0, y0, x1, y1, txt, *_ in words:
            s = (txt or "").strip()
            if not re.fullmatch(r"\d{1,5}", s):
                continue
            spans.append((x0, y0, x1, y1, s))
            heights.append(y1 - y0)
        if not spans:
            continue
        clusters = _cluster_by_x(spans, eps=12.0)
        for cl in clusters:
            score, stats = _cluster_score_monotone(cl)
            xs = [0.5*(x0+x1) for x0,_,x1,_,_ in cl]
            xc = sum(xs)/len(xs)
            side_guess = "left" if xc < 0.5*W else "right"
            if prefer_side and prefer_side != side_guess:
                score -= 0.3
            if best is None or score > best[0]:
                best = (score, pno, cl, side_guess)

    if best is None:
        doc.close()
        raise ValueError("failed to find line numbers band")

    _, pno, cluster, side = best
    xmins = [x0 for x0,_,_,_,_ in cluster]
    xmaxs = [x1 for _,_,x1,_,_ in cluster]
    x_min = min(xmins)
    x_max = max(xmaxs)
    pad_inside = 2.0
    x_cut = max(0.0, x_min - pad_inside) if side == "right" else min(W, x_max + pad_inside)

    y_tol = 8.0
    if heights:
        hs = sorted(heights)
        med_h = hs[len(hs)//2]
        y_tol = max(6.0, 0.9*med_h)

    doc.close()
    band = {"side": side, "x_cut": float(x_cut), "x_min": float(x_min), "x_max": float(x_max),
            "y_tol": float(y_tol), "page_width": float(W)}
    return band

def find_nearest_margin_lineno(
    pdf_path: Path,
    page_num_1based: int,
    target_y: float,
    band: Dict[str, float],
    max_candidates: int = 3,
    align: str = "bottom",
):
    """
    Find nearest printed line number in margin band on page.
    Uses PyMuPDF 'words' boxes, compares target_y to token's bottom by default.
    """
    import fitz
    page_index = page_num_1based - 1
    doc = fitz.open(str(pdf_path))
    if not (0 <= page_index < doc.page_count):
        doc.close()
        raise ValueError(f"Page {page_num_1based} out of range (PDF has {doc.page_count} pages).")
    page = doc.load_page(page_index)
    words = page.get_text("words")
    pad = 2.0
    x_min = band.get("x_min", 0.0) - pad
    x_max = band.get("x_max", band["page_width"]) + pad
    W = band["page_width"]

    def y_ref(y0, y1):
        if align == "top": return y0
        if align == "center": return 0.5*(y0+y1)
        return y1  # default: bottom

    candidates = []
    for x0, y0, x1, y1, txt, *_ in words:
        s = (txt or "").strip()
        if not re.fullmatch(r"\d{1,5}", s):
            continue
        if x1 < x_min or x0 > x_max:
            continue
        xc = 0.5*(x0+x1)
        if band["side"] == "right" and xc < 0.5*W:  # keep right side only
            continue
        if band["side"] == "left" and xc > 0.5*W:   # keep left side only
            continue
        y_anchor = y_ref(y0, y1)
        dy = abs(y_anchor - target_y)
        candidates.append((s, (x0, y0, x1, y1), dy, y_anchor))

    candidates.sort(key=lambda t: t[2])
    result = {"lineno": None, "candidate_count": len(candidates)}
    if candidates:
        best_txt, best_bbox, best_dy, best_y_anchor = candidates[0]
        tol = band["y_tol"]
        if align == "bottom":
            tol = max(tol, 0.9*tol + 1.5)
        if best_dy <= tol:
            try: result["lineno"] = int(best_txt)
            except: result["lineno"] = None
    doc.close()
    return result

# ----------------------------
# Public resolver class (no .synctex.gz is used)
# ----------------------------

class PdfLinenoResolver:
    """
    Resolve (tex file, source line) -> (pdf page, printed lineno) using:
      - synctex view (binary)
      - PyMuPDF to read margin numbers
    """
    def __init__(self, pdf_path: Path, tex_main: Optional[Path] = None):
        self.pdf_path = pdf_path
        self.tex_main = tex_main
        self._band: Optional[Dict[str, float]] = None  # detected margin band, cached

    def _ensure_band(self, page_hint: Optional[int] = None) -> Dict[str, float]:
        if self._band is not None:
            return self._band
        prefer_side = None
        # Try to detect lineno side from preamble if main tex is available
        if self.tex_main and self.tex_main.exists():
            try:
                pre = self.tex_main.read_text(encoding="utf-8", errors="ignore")
                m = re.search(r"\\usepackage\[(.*?)\]\s*\{\s*lineno\s*\}", pre, flags=re.IGNORECASE)
                if m:
                    opts = m.group(1)
                    if "right" in opts: prefer_side = "right"
                    elif "left" in opts: prefer_side = "left"
            except Exception:
                pass
        self._band = auto_detect_margin_band(self.pdf_path, prefer_side=prefer_side, page_hint=page_hint)
        return self._band

    def resolve(self, tex_file: Path, src_line: int, column: int = 1) -> tuple[Optional[int], Optional[int]]:
        """
        Returns (pdf_page, printed_lineno). Either may be None if not resolvable.
        """
        try:
            sync = synctex_view(tex_file, self.pdf_path, src_line, column, synctex_exe="synctex")
        except Exception as e:
            return (None, None)

        band = None
        try:
            band = self._ensure_band(page_hint=sync.page)
        except Exception as e:
            print("Band detection for lineno failed")

        lineno = None
        if band:
            try:
                info = find_nearest_margin_lineno(self.pdf_path, sync.page, sync.y, band=band, max_candidates=3, align="bottom")
                lineno = info.get("lineno")
            except Exception as e:
                print("lineno detect failed")

        return (sync.page, lineno)
