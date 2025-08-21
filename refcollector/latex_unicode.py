# refcollector/latex_unicode.py
import re
import unicodedata
from typing import List, Tuple

# ----------------------------
# Simple LaTeX macro → Unicode replacements
# (order matters: longer/more specific first)
# ----------------------------
LATEX_SIMPLE_REPLACEMENTS: List[Tuple[str, str]] = [
    # Basic punctuation & escapes
    (r"\&", "&"), (r"\%", "%"), (r"\_", "_"), (r"\$", "$"), (r"\#", "#"),
    (r"---", "—"), (r"--", "–"),
    (r"``", "“"), (r"''", "”"),
    (r"\textendash", "–"), (r"\textemdash", "—"),
    (r"\ldots", "…"), (r"\dots", "…"), (r"\textellipsis", "…"),
    (r"\slash", "/"), (r"\backslash", "\\"), (r"\textbackslash", "\\"),
    (r"\textbar", "|"), (r"\textless", "<"), (r"\textgreater", ">"),
    (r"\guillemotleft", "«"), (r"\guillemotright", "»"),
    (r"\guilsinglleft", "‹"), (r"\guilsinglright", "›"),
    (r"\textquotedblleft", "“"), (r"\textquotedblright", "”"),
    (r"\textquoteleft", "‘"), (r"\textquoteright", "’"),
    (r"\textregistered", "®"), (r"\texttrademark", "™"), (r"\textdegree", "°"),
    (r"\textbullet", "•"), (r"\textnumero", "№"),
    (r"\textsection", "§"), (r"\S", "§"), (r"\textparagraph", "¶"), (r"\P", "¶"),
    # Spaces / thinspaces (simplified to regular spaces)
    (r"~", " "), (r"\\ ", " "), (r"\enspace", " "), (r"\,", " "),
    (r"\;", " "), (r"\:", " "), (r"\!", ""),
    # Ligatures & special letters (standalone glyph macros)
    (r"\ae", "æ"), (r"\AE", "Æ"), (r"\oe", "œ"), (r"\OE", "Œ"),
    (r"\aa", "å"), (r"\AA", "Å"), (r"\o", "ø"), (r"\O", "Ø"),
    (r"\ss", "ß"), (r"\SS", "ẞ"),
    (r"\l", "ł"), (r"\L", "Ł"),
    # Greek lowercase
    (r"\alpha", "α"), (r"\beta", "β"), (r"\gamma", "γ"), (r"\delta", "δ"),
    (r"\epsilon", "ε"), (r"\varepsilon", "ε"), (r"\zeta", "ζ"),
    (r"\eta", "η"), (r"\theta", "θ"), (r"\vartheta", "ϑ"), (r"\iota", "ι"),
    (r"\kappa", "κ"), (r"\lambda", "λ"), (r"\mu", "μ"), (r"\nu", "ν"),
    (r"\xi", "ξ"), (r"\pi", "π"), (r"\varpi", "ϖ"), (r"\rho", "ρ"),
    (r"\varrho", "ϱ"), (r"\sigma", "σ"), (r"\varsigma", "ς"),
    (r"\tau", "τ"), (r"\upsilon", "υ"), (r"\phi", "φ"), (r"\varphi", "ϕ"),
    (r"\chi", "χ"), (r"\psi", "ψ"), (r"\omega", "ω"),
    # Greek uppercase
    (r"\Gamma", "Γ"), (r"\Delta", "Δ"), (r"\Theta", "Θ"), (r"\Lambda", "Λ"),
    (r"\Xi", "Ξ"), (r"\Pi", "Π"), (r"\Sigma", "Σ"), (r"\Upsilon", "Υ"),
    (r"\Phi", "Φ"), (r"\Psi", "Ψ"), (r"\Omega", "Ω"),
    # Common math-ish symbols found in titles
    (r"\pm", "±"), (r"\mp", "∓"), (r"\times", "×"), (r"\div", "÷"),
    (r"\cdot", "⋅"), (r"\ast", "∗"), (r"\star", "★"),
    (r"\leq", "≤"), (r"\geq", "≥"), (r"\neq", "≠"),
    (r"\approx", "≈"), (r"\sim", "∼"), (r"\simeq", "≃"),
    (r"\infty", "∞"), (r"\propto", "∝"), (r"\equiv", "≡"),
    (r"\rightarrow", "→"), (r"\to", "→"), (r"\leftarrow", "←"),
    (r"\uparrow", "↑"), (r"\downarrow", "↓"),
    (r"\subset", "⊂"), (r"\subseteq", "⊆"), (r"\supset", "⊃"), (r"\supseteq", "⊇"),
    (r"\in", "∈"), (r"\ni", "∋"), (r"\notin", "∉"),
    (r"\cup", "∪"), (r"\cap", "∩"), (r"\setminus", "∖"),
    (r"\forall", "∀"), (r"\exists", "∃"), (r"\nabla", "∇"),
    (r"\partial", "∂"), (r"\Re", "ℜ"), (r"\Im", "ℑ"),
    (r"\degree", "°"), (r"\circ", "∘"),
]

# dotless i/j used before accents (replace first so regex sees base letters)
_DOTLESS_MAP = {r"\i": "ı", r"\j": "ȷ"}

# accent command → combining mark
_ACCENT_COMBINING = {
    '"': "\u0308",   # diaeresis
    "'": "\u0301",   # acute
    "`": "\u0300",   # grave
    "^": "\u0302",   # circumflex
    "~": "\u0303",   # tilde
    "H": "\u030B",   # double acute
    "c": "\u0327",   # cedilla
    "k": "\u0328",   # ogonek
    "r": "\u030A",   # ring above
    "v": "\u030C",   # caron
    "=": "\u0304",   # macron
    ".": "\u0307",   # dot above
    "u": "\u0306",   # breve
    "b": "\u0331",   # bar below (macron below)
}

# basic inline math remover
_MATH_INLINE = re.compile(r"\$(?:\\\$|[^\$])*\$")

# matches: \"{o}, \'{e}, \~n, \r{a}, \k{a}, also without braces (\'e)
_ACCENT_REGEX = re.compile(r"""\\(["'`^~Hckrv=.ub])\s*\{?\s*([A-Za-zıȷ])\s*\}?""")

def _replace_accents(text: str) -> str:
    # replace \i and \j first so they can be accented
    for k, v in _DOTLESS_MAP.items():
        text = text.replace(k, v)

    def repl(m: re.Match) -> str:
        acc = m.group(1)
        base = m.group(2)
        comb = _ACCENT_COMBINING.get(acc)
        if not comb:
            return m.group(0)
        return unicodedata.normalize("NFC", base + comb)

    return _ACCENT_REGEX.sub(repl, text)

def latex_to_unicode(s: str) -> str:
    r"""
    Best-effort LaTeX → Unicode conversion for BibTeX fields (titles, authors, abstracts).
    - strips comments
    - converts accents using combining marks
    - applies simple symbol replacements
    - removes inline math
    - removes protective braces
    - collapses whitespace
    """
    if not s:
        return s

    # strip comments (%) outside math
    s = re.sub(r"(?<!\\)%.*", "", s)

    # accents
    s = _replace_accents(s)

    # simple replacements
    for pat, rep in LATEX_SIMPLE_REPLACEMENTS:
        s = s.replace(pat, rep)

    # remove inline math
    s = _MATH_INLINE.sub("", s)

    # drop protective braces
    s = s.replace("{", "").replace("}", "")

    # normalize whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s
