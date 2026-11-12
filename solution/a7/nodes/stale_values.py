"""Direction-aware check for STALE values ("Was: ...") in a draft. Rule-based on purpose: no LLM, readable, teachable.

A rule chunk says "Was: SIF v2 (14 columns). Now: SIF v3 (16 columns)". The draft may MENTION 14 only as history
("16 columns instead of 14", "v2 (14 columns) no longer applies"). It must never ASSERT 14 as current
("14 columns instead of 16", "Previously 16 columns, now 14", "The SIF has 14 columns").

How the decision is made, for EVERY occurrence of a stale number in the draft:

  1. SLOT patterns (two-sided). A marker splits the sentence into a SUPERSEDED slot and a CURRENT slot:
       "NEW instead of OLD"   "from OLD to NEW"   "OLD -> NEW"   "previously OLD, now NEW"   "NEW, up from OLD"
       "replaced OLD with NEW"   Arabic "NEW بدلا من OLD"   Hinglish "OLD ki jagah NEW" / "pehle OLD, ab NEW"
     stale in the OLD slot (and a current value or reference in the NEW slot)  -> mention, fine
     stale in the NEW slot                                                      -> asserted as current -> FAIL
  2. CLAUSE markers (one-sided). In its own clause the stale value is next to an explicit "obsolete" signal:
       "OLD no longer applies", "v2 is obsolete", "do not use OLD", "no longer OLD", "purana OLD ab nahi chalega"
     -> mention, fine.  "the previous OLD layout" (history word) counts only if a current value sits elsewhere.
     Persistence words ("still", "unchanged", "not changed") or "now/ab/الآن" before it VETO this.
  3. Anything else -> direction unknown -> asserted -> FAIL (fail closed; the ticket goes to human review).

Everything is data-driven: the stale numbers come from each rule's "Was:" text, the current numbers/labels (16, v3)
from its "Now:" text. Nothing is specific to one rule.
"""
from __future__ import annotations

import re
from typing import Iterable

# ---- text helpers ---------------------------------------------------------------------------------------------
_DIGITS = str.maketrans({**{chr(0x0660 + i): str(i) for i in range(10)}, **{chr(0x06F0 + i): str(i) for i in range(10)},
                         **{chr(0x0966 + i): str(i) for i in range(10)}})
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
LIST_MARK_RE = re.compile(r"(?m)^\s*\d+[.)]\s")
AR_MARKS_RE = re.compile("[ً-ْـ]")      # tanween, harakat, tatweel: Arabic is matched on the bare letters
SENT_SPLIT_RE = re.compile(r"(?<=[.!?؟۔।])\s+|\n+")
VERSION_RE = re.compile(r"\bv\d+\b", re.I)


def norm_num(tok: str) -> str:
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", tok):
        tok = tok.replace(",", "")
    tok = tok.replace(",", ".")
    return str(int(tok)) if re.fullmatch(r"\d+", tok) else tok.rstrip(".")


def _prep(sentence: str) -> str:
    return LIST_MARK_RE.sub(" ", AR_MARKS_RE.sub("", sentence.translate(_DIGITS)))


# ---- word lists (English | Arabic | Hindi/Hinglish) -------------------------------------------------------------
def _w(body: str) -> str:
    """Whole-word match that also works next to Arabic/Devanagari (where \\b misbehaves)."""
    return rf"(?<!\w)(?:{body})(?!\w)"


# A clause = a comma/semicolon-delimited piece. K = text inside one clause, C = text across commas of one sentence.
_TOK = r"(?:[^\s,;:؛،—–]|,(?!\s))+"                      # a token; "15,000" is one token, "14," is not
K = r"(?:[^,;:؛،—–!?\n]|,(?!\s))"
C = r"[^;:؛!?\n—–]"

# 1. SLOT patterns. (?P<old>..) is the superseded slot, (?P<new>..) the current slot.
_INSTEAD = r"instead of|rather than|in place of|in lieu of|as opposed to|versus|بدلا من|بدلا عن|بدل|عوضا عن|وليس|ليس"
_HI_INSTEAD = r"ki jagah|ki jagha|ke badle|ke bajay|ki bajay|के बजाय|के बदले|की जगह"
_NUMTOK = r"(?:[^\s,;]|,(?!\s))*\d(?:[^\s,;]|,(?!\s))*"          # a token holding a digit: 14, 15,000, (14, v2
_WORDTOK = r"(?:[^\s\d,;]|,(?!\s))+"
_PRE_NOW = r"previously|formerly|earlier|originally|before|used to be|used to have|pehle|pahle|पहले|سابقا|كان|كانت|(?:was|were)(?=\s+(?:[A-Za-z]{1,4}\.?\s*)?\d)"
_NOW = r"now|currently|today|at present|but now|and now|ab|abhi|अब|अभी|الان|الآن|حاليا|اليوم|اصبح|اصبحت|أصبح|أصبحت|لكن الان"
SLOT_PATTERNS = [re.compile(p, re.I) for p in (
    # NEW instead of OLD          "Use 16 columns instead of 14" / "... بدلا من 14"
    rf"(?P<new>{C}+?)\s+{_w(_INSTEAD)}\s+(?P<old>{K}+)",
    # Instead of OLD, NEW         "Rather than 14, use 16"
    rf"^\s*{_w(_INSTEAD)}\s+(?P<old>{K}+)[,،]\s*(?P<new>{C}+)",
    # NEW, not OLD                "16 columns, not 14"
    rf"(?P<new>{C}+?)[,،]\s*{_w('not|no')}\s+(?P<old>{K}+)",
    # from OLD to NEW             "changed from 14 to 16" / "من 14 إلى 16"
    rf"{_w('from|من')}\s+(?P<old>{K}+?)\s+{_w('to|into|إلى|الى|لتصبح|ليصبح')}\s+(?P<new>{K}+)",
    # OLD -> NEW
    rf"(?P<old>{K}+?)\s*(?:->|→|=>|⇒)\s*(?P<new>{K}+)",
    # NEW, up/down from OLD       "now 25,000, up from 15,000"
    rf"(?P<new>{C}+?)[,،]?\s+{_w('up|down|raised|increased|decreased|reduced|lowered|changed|moved|rose|fell|went|upgraded')}\s+from\s+(?P<old>{K}+)",
    # previously OLD, now NEW     "Previously 16 columns, now 14" / "Pehle 25,000 tha, ab 15,000 hai"
    rf"{_w(_PRE_NOW)}\s+(?P<old>{K}+?)[,،;]?\s+{_w(_NOW)}\s+(?P<new>{K}+)",
    # NEW, previously/after being OLD   "now 16, after being 14" / "بعد أن كان 14"
    rf"(?P<new>{C}+?)[,،(]?\s*{_w('previously|formerly|earlier|originally|used to be|after being|after it was|(?:was|were)(?=\s+(?:[A-Za-z]{1,4}\.?\s*)?\d)|بعد أن كان|بعد ان كان|بعد أن كانت|بعدما كان')}\s+(?P<old>{K}+)",
    # replaced OLD with/by NEW
    rf"{_w('replac(?:e|es|ed|ing)|supersed(?:e|es|ed|ing)')}\s+(?:the\s+)?(?P<old>{K}+?)\s+{_w('with|by')}\s+(?P<new>{K}+)",
    # OLD was replaced by NEW / OLD is superseded by NEW
    rf"(?P<old>{K}+?)\s+(?:was|is|were|are|has been|have been)\s+(?:replaced|superseded)\s+(?:by|with)\s+(?P<new>{K}+)",
    # NEW replaces OLD            "SIF v3 replaces the 14-column layout" / "..., replacing the 14-column v2 layout"
    rf"(?P<new>{C}+?)[,،]?\s+{_w('replaces|replaced|replacing|supersedes|superseding')}\s+(?:the\s+)?(?P<old>{K}+)",
    # Hinglish / Hindi "OLD ki jagah": OLD is the number right before the marker, everything else is NEW
    rf"(?P<new0>{C}*?)(?P<old>{_NUMTOK}(?:\s+{_WORDTOK}){{0,2}})\s+{_w(_HI_INSTEAD)}(?P<new1>{C}*)",
)]

# 2. CLAUSE markers. Phrases are first replaced by one token so that "ab nahi" ("no longer") is not read as "now".
_POST_PRED = (r"apply|applies|applicable|valid|in force|in use|used|supported|accepted|required|mandatory|current|correct|allowed|"
              r"relevant|available|enforced|effective|accurate|true")
NEG_POST_RE = re.compile(_w(
    rf"no longer\s+(?:{_POST_PRED})|(?:does not|doesn't|do not|don't|is not|isn't|are not|aren't|not|cannot be)\s+(?:{_POST_PRED})|"
    r"(?:is |are |was |were |now |already )?(?:obsolete|deprecated|retired|discontinued|invalid|void|outdated|expired|cancelled|canceled|"
    r"withdrawn|superseded|scrapped|stale)|"
    r"لا ينطبق|لا تنطبق|لا يسري|لا تسري|غير ساري\w*|غير صالح\w*|غير معتمد\w*|غير مطبق\w*|ملغ[ىيا]\w*|ألغ\w+|الغ\w+|منتهي\w*|منسوخ\w*|لم يعد ساريا|"
    r"अब लागू नहीं|लागू नहीं|मान्य नहीं|ab nahi(?: chalega| chalta)?|nahi chalega|nahi chalta|(?:applicable|valid|lagu|laagu) nahi|"
    r"(?:ab )?lagu nahi|band ho gaya|बंद"), re.I)
NEG_PRE_RE = re.compile(_w(
    r"no longer|not any ?more|(?:do not|don't|dont|never|should not|shouldn't|must not|cannot|can't)\s+(?:use|using|apply|follow|choose|generate|"
    r"file|submit|rely on)|avoid(?:ing)?|stop(?:ped)? using|"
    r"و?لم يعد|و?لم تعد|لا يعد|لا تستخدم|لا تعتمد|تجنب|mat use|istemal mat|use mat"), re.I)
DOUBLE_NEG_RE = re.compile(_w(r"(?:not|never|isn't|aren't|hardly)\s+(?:obsolete|deprecated|retired|discontinued|invalid|void|outdated|expired|cancelled|"
                              r"canceled|withdrawn|superseded|scrapped|stale)"), re.I)
PERSIST_RE = re.compile(_w(
    r"still|remains?|remained|unchanged|same|continues?|keeps?|kept|retain(?:s|ed)?|(?:has|have|had)\s+(?:not|never)\s+changed|never changed|not changed|"
    r"abhi bhi|अभी भी|वही|ما زال\w*|لا يزال\w*|مازال\w*|لايزال\w*|ما يزال\w*"), re.I)
NOW_SPLIT_RE = re.compile(_w(r"now|currently|today|presently|at present|ab|abhi|अब|अभी|الان|الآن|حاليا|اليوم|اصبح\w*|أصبح\w*"), re.I)
CLAUSE_SPLIT_RE = re.compile(
    r",(?!\d)(?=\s)(?!\s*(?:which|that|الذي|التي|jo|जो)\b)|،|;|؛|:|—|–|\s-\s|" +
    _w(r"and|but|while|whereas|so|because|although|though|yet|however|then|ولكن|لكن|بينما|و|aur|lekin|par|magar|jabki|और|लेकिन|परंतु"), re.I)
PAST_BEFORE_RE = re.compile(_w(
    r"previous(?:ly)?|former(?:ly)?|earlier|original(?:ly)?|old|older|prior|legacy|pre-?\w+|pehle|pahle|purana|purane|purani|पहले|पुराना|पुराने|पुरानी|"
    r"سابقا|السابق\w*|القديم\w*|قديم\w*|الأصلي|كان|كانت|(?:was|were|had been)"), re.I)
PAST_AFTER_RE = re.compile(_w(r"tha|thi|the|था|थी|थे"), re.I)
NEG_PRE_TOK, NEG_POST_TOK, KEEP_TOK = "§NEGPRE§", "§NEGPOST§", "§KEEP§"
WINDOW = 5                                                # tokens between a stale number and its "obsolete" marker
PAST_WINDOW = 3
GENERIC_NEW_RE = re.compile(_w(r"new|newer|latest|updated|revised|current|naya|nayi|naye|नया|नई|नए|الجديد|جديد|الجديدة|الحالي|الأحدث"), re.I)
NEGATION_RE = re.compile(_w(r"not|no|never|isn't|aren't|don't|nahi|mat|لا|ليس|لم|नहीं|न"), re.I)


class CurrentValues:
    """What the rules say is current: numbers and version labels from every rule's "Now:" text."""

    def __init__(self, numbers: Iterable[str] = (), labels: Iterable[str] = ()) -> None:
        self.numbers, self.labels = frozenset(numbers), frozenset(x.lower() for x in labels)

    def anchors(self, text: str) -> bool:
        """A real current value (16, 25,000, v3) in the text."""
        t = _prep(text)
        return any(norm_num(m) in self.numbers for m in NUM_RE.findall(t)) or any(v.lower() in self.labels for v in VERSION_RE.findall(t))

    def refers(self, text: str) -> bool:
        """A current value, or a clear reference to it ("the new layout") that is not itself negated."""
        return self.anchors(text) or bool(GENERIC_NEW_RE.search(text) and not NEGATION_RE.search(text))


def _tokens(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+", text)]


def _tok_index(toks: list[tuple[int, int, str]], pos: int) -> int:
    return next((i for i, (s, e, _) in enumerate(toks) if s <= pos < e), len(toks))


def _slot_verdicts(s: str, cur: CurrentValues) -> dict[int, set[str]]:
    """For every character position of s, 'old' / 'new' if some slot pattern puts it in the superseded / current slot."""
    out: dict[int, set[str]] = {}
    for rx in SLOT_PATTERNS:
        for m in rx.finditer(s):
            g = m.groupdict()
            old = m.span("old")
            news = [m.span(k) for k in ("new", "new0", "new1") if g.get(k) is not None and m.span(k)[1] > m.span(k)[0]]
            new_text = " ".join(s[a:b] for a, b in news)
            ok = bool(new_text.strip()) and cur.refers(new_text)          # two-sided: the current slot must hold a current value
            for pos in range(*old):
                out.setdefault(pos, set()).add("old" if ok else "old_unanchored")
            for a, b in news:
                for pos in range(a, b):
                    out.setdefault(pos, set()).add("new")
    return out


def _clause_ok(s: str, start: int, cur: CurrentValues) -> bool:
    """One-sided check: is the stale number at `start` explicitly marked obsolete within its own clause?"""
    clause_start = 0
    clause_end = len(s)
    for m in CLAUSE_SPLIT_RE.finditer(s):
        if m.end() <= start:
            clause_start = max(clause_start, m.end())
        elif m.start() >= start:
            clause_end = min(clause_end, m.start())
    for m in NOW_SPLIT_RE.finditer(s, clause_start, clause_end):          # "now 14": the clause begins at 'now'
        if m.end() <= start:
            clause_start = m.start()
        else:
            clause_end = min(clause_end, m.start())
    clause = s[clause_start:clause_end]
    pos = start - clause_start
    if PERSIST_RE.search(clause) or NOW_SPLIT_RE.match(clause.lstrip()) or KEEP_TOK in clause:
        return False
    toks = _tokens(clause)
    i = _tok_index(toks, pos)
    for j, (_, _, t) in enumerate(toks):
        if t.startswith(NEG_POST_TOK) and 0 <= j - i <= WINDOW:
            return True
        if t.startswith(NEG_PRE_TOK) and 0 <= i - j <= WINDOW:
            return True
    # history words ("the previous 14-column layout", "purane 14") only count when a current value is stated elsewhere
    past = any(PAST_BEFORE_RE.fullmatch(toks[k][2].strip("()[]\"'.,")) for k in range(max(0, i - PAST_WINDOW), i)) or \
        any(PAST_AFTER_RE.fullmatch(toks[k][2].strip("()[]\"'.,")) for k in range(i + 1, min(len(toks), i + 3)))
    if past:
        rest = s[:clause_start] + " " + s[clause_end:]
        return cur.anchors(re.sub(NEG_POST_RE, " ", rest))
    return False


def _mark(sentence: str) -> str:
    s = DOUBLE_NEG_RE.sub(f" {KEEP_TOK} ", _prep(sentence))
    s = NEG_POST_RE.sub(lambda m: " " + NEG_POST_TOK + " ", s)
    return NEG_PRE_RE.sub(lambda m: " " + NEG_PRE_TOK + " ", s)


def asserted_in_sentence(sentence: str, stale: set[str], cur: CurrentValues) -> set[str]:
    """Stale numbers this sentence states as current (or whose direction cannot be determined)."""
    raw = _prep(sentence)
    if not (stale & {norm_num(x) for x in NUM_RE.findall(raw)}):
        return set()
    s = _mark(sentence)
    slots = _slot_verdicts(s, cur)
    out: set[str] = set()
    for m in NUM_RE.finditer(s):
        n = norm_num(m.group())
        if n not in stale:
            continue
        v = slots.get(m.start(), set())
        if "new" in v:
            out.add(n)                                  # in the CURRENT slot of some marker: asserted
        elif "old" in v:
            continue                                    # superseded slot, current value in the other slot: a mention
        elif not _clause_ok(s, m.start(), cur):
            out.add(n)                                  # no marker, or no clear direction: fail closed
    return out


def asserted_stale(draft: str, stale: set[str], cur: CurrentValues | None = None) -> set[str]:
    out: set[str] = set()
    for sent in SENT_SPLIT_RE.split(draft or ""):
        out |= asserted_in_sentence(sent, stale, cur or CurrentValues())
    return out
