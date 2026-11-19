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
import unicodedata
from typing import Iterable

from .numbers import DIGIT_MAP, canonicalise, norm_digits

# ---- text helpers ---------------------------------------------------------------------------------------------
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
LIST_MARK_RE = re.compile(r"(?m)^\s*\d+[.)]\s")
AR_MARKS_RE = re.compile("[ً-ْـ]")      # tanween, harakat, tatweel: Arabic is matched on the bare letters
SENT_SPLIT_RE = re.compile(r"(?<=[.!?؟۔।])\s+|\n+")
VERSION_RE = re.compile(r"\bv\d+\b", re.I)


norm_num = norm_digits


def _prep(sentence: str) -> str:
    """Digits of every script -> ASCII, list markers and Arabic marks out, then EVERY numeric mention -> canonical digits
    (fourteen, 15k, 15 thousand, أربعة عشر, पंद्रह हज़ार ...). Everything below sees one spelling of each number."""
    s = unicodedata.normalize("NFC", sentence).translate(DIGIT_MAP)
    return canonicalise(LIST_MARK_RE.sub(" ", AR_MARKS_RE.sub("", s)))


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
_HI_DONE = r"कर दी|कर दिया|कर दिए|कर दी गई|हो गई|हो गया|हो गए|की गई|किया गया|हुई|हुआ|kar di|kar diya|kar diye|ho gayi|ho gaya|ho gaye|ki gayi|kiya gaya"
_NUMTOK =r"(?:[^\s,;]|,(?!\s))*\d(?:[^\s,;]|,(?!\s))*"          # a token holding a digit: 14, 15,000, (14, v2
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
    rf"(?P<new>{C}+?)[,،(]?\s*{_w(r'previously|formerly|earlier|originally|used to be|after being|after it was|(?:was|were)(?=\s+(?:[A-Za-z]{1,4}\.?\s*)?\d)|بعد أن كان|بعد ان كان|بعد أن كانت|بعدما كان')}\s+(?P<old>{K}+)",
    # replaced OLD with/by NEW
    rf"{_w('replac(?:e|es|ed|ing)|supersed(?:e|es|ed|ing)')}\s+(?:the\s+)?(?P<old>{K}+?)\s+{_w('with|by')}\s+(?P<new>{K}+)",
    # OLD was replaced by NEW / OLD is superseded by NEW
    rf"(?P<old>{K}+?)\s+(?:was|is|were|are|has been|have been)\s+(?:replaced|superseded)\s+(?:by|with)\s+(?P<new>{K}+)",
    # NEW replaces OLD            "SIF v3 replaces the 14-column layout" / "..., replacing the 14-column v2 layout"
    rf"(?P<new>{C}+?)[,،]?\s+{_w('replaces|replaced|replacing|supersedes|superseding')}\s+(?:the\s+)?(?P<old>{K}+)",
    # Hinglish / Hindi "OLD ki jagah": OLD is the number right before the marker, everything else is NEW
    rf"(?P<new0>{C}*?)(?P<old>{_NUMTOK}(?:\s+{_WORDTOK}){{0,2}})\s+{_w(_HI_INSTEAD)}(?P<new1>{C}*)",
    # Hindi / Hinglish "OLD se badhakar NEW"       "₹15,000 से बढ़ाकर ₹25,000"   "15,000 se badhakar 25,000"
    rf"(?P<old>{K}+?)\s+{_w('से|se')}\s+(?:बढ़?|घट|बदल|badh|ghat|badal)\S*\s+(?:कर\s+|kar\s+)?(?P<new>{K}+)",
    # "OLD se NEW kar diya / ho gaya"               "15,000 से 25,000 कर दी गई है"
    rf"(?P<old>{K}+?)\s+{_w('से|se')}\s+(?P<new>{K}+?)\s+{_w(_HI_DONE)}",
    # "compared to OLD" / "unlike OLD"              "16 columns compared to 14 earlier"
    rf"(?P<new>{C}+?)[,،]?\s+{_w('compared (?:to|with)|as against|unlike')}\s+(?P<old>{K}+)",
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
    r"(?:ab )?lagu nahi|band ho gaya|बंद|"
    r"purani hai|purana hai|puraana hai|purane hain|purani ho gayi|purana ho gaya|पुरानी है|पुराना है|पुराने हैं|पुरानी हो गई|पुराना हो गया|"
    r"(?:is|are) (?:now )?(?:old|out of date|out-of-date|no longer current)"), re.I)
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
    r"outdated|out-of-date|obsolete|superseded|stale|expired|deprecated|retired|discontinued|replaced|"
    r"سابقا|السابق\w*|القديم\w*|قديم\w*|الأصلي|كان|كانت|(?:was|were|had been)"), re.I)
# "until 31 July the limit was 15,000": a clause that says it is about the period BEFORE a date is history
HISTORY_CLAUSE_RE = re.compile(_w(r"previously|formerly|originally|earlier|used to|until|till|prior to|before(?=\s+\d)|back then|in the past|last (?:year|month|quarter)|حتى|قبل"), re.I)
# a copula between a history word and the number means the number is the predicate ("the previous ceiling IS 15,000"): not history
COPULA_RE = re.compile(_w(r"is|are|equals?|stands at|remains?|hai|hain|है|हैं|هو|هي|يبلغ|يساوي"), re.I)
# the customer's own file / payroll / setup, and a problem with it ("Your file was rejected because it has 14 columns")
CUSTOMER_RE = re.compile(_w(r"your|yours|you|aapki|aapka|aapke|aap|आपकी|आपका|आपके|आप|ملفك|ملفكم|لديكم|لديك|الخاص بكم|الخاصة بكم"), re.I)
PROBLEM_RE = re.compile(r"reject|fail|error|invalid|incorrect|mismatch|wrong|outdated|out of date|out-of-date|obsolete|stale|legacy|not accepted|"
                        r"not valid|रिजेक्ट|अस्वीकार|गलत|ग़लत|مرفوض|رفض|خطأ|خاطئ|غير صحيح|غير مقبول|قديم", re.I)
# a sentence that ASSERTS the current value: a "current" cue plus a real current value, nothing negated
CURRENT_CUE_RE = re.compile(_w(r"current|currently|now|as of|effective|since|new|newer|latest|updated|revised|requires?|required|must|needs?|"
                               r"from(?=\s+\d)|naya|nayi|naye|abhi|ab|नया|नई|नए|अब|अभी|الان|الآن|الحالي|حاليا|الجديد|جديد|اعتبارا|يجب"), re.I)
NEXT_NEG_RE = re.compile(_w(r"not|never|isn't|aren't|don't|doesn't|nahi|nahin|mat|لا|ليس|لم|नहीं"), re.I)
ANSWER_NO_RE = re.compile(r"^\s*(?:no|nope|not any ?more|not anymore|nahi|nahin|नहीं|लا|لا)\b[\s,.:;\-–—]*", re.I)
PAST_AFTER_RE = re.compile(_w(r"tha|thi|the|था|थी|थे"), re.I)
NEG_PRE_TOK, NEG_POST_TOK, KEEP_TOK = "§NEGPRE§", "§NEGPOST§", "§KEEP§"
NEG_POST_TOK_WORD = "outdated"
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


class Context:
    """What surrounds the sentence being checked: the NEXT sentence and whether this one is a question."""

    def __init__(self, nxt: str = "", question: bool = False) -> None:
        self.nxt, self.question = nxt, question


def _asserts_current(text: str, cur: CurrentValues) -> bool:
    """The text states the CURRENT value: a current value (16, v3, 25,000) plus a 'current' cue, nothing negated or 'still'."""
    t = NEG_POST_RE.sub(" ", _prep(text))
    return cur.anchors(t) and bool(CURRENT_CUE_RE.search(t)) and not NEXT_NEG_RE.search(t) and not PERSIST_RE.search(t)


def _clause_bounds(s: str, start: int) -> tuple[int, int]:
    clause_start, clause_end = 0, len(s)
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
    return clause_start, clause_end


def _clean(tok: str) -> str:
    return tok.strip("()[]\"'.,")


def _clause_ok(s: str, start: int, cur: CurrentValues, ctx: Context | None = None) -> bool:
    """One-sided check: is the stale number at `start` a MENTION of history, judged from its own clause plus the contrast around it?

    Accepted (each needs a current value stated on the right side of the contrast, never just a bare 'old' word):
      a. an explicit 'obsolete' signal next to the number                         "v2 is obsolete", "do not use 14", "15000 wali limit purani hai"
      b. history in the clause (previously / used to be / outdated / until DATE)  + a current value in the same sentence or the NEXT one
      c. the customer's own file with a problem, then the current value after it  "Your file was rejected because it has 14; the current SIF needs 16"
      d. a question answered 'No' by the next sentence, which gives the current value  "Is it still 15,000? No - it is 25,000."
    Persistence words (still, unchanged) or 'now' before the number veto a-c. Anything else: not a mention (fail closed)."""
    ctx = ctx or Context()
    clause_start, clause_end = _clause_bounds(s, start)
    clause = s[clause_start:clause_end]
    pos = start - clause_start
    if ctx.question and ctx.nxt:                                                   # d.
        m = ANSWER_NO_RE.match(ctx.nxt)
        if m and _asserts_current(ctx.nxt[m.end():], cur):
            return True
    if PERSIST_RE.search(clause) or NOW_SPLIT_RE.match(clause.lstrip()) or KEEP_TOK in clause:
        return False
    toks = _tokens(clause)
    i = _tok_index(toks, pos)
    for j, (_, _, t) in enumerate(toks):                                            # a.
        if t.startswith(NEG_POST_TOK) and 0 <= j - i <= WINDOW:
            return True
        if t.startswith(NEG_PRE_TOK) and 0 <= i - j <= WINDOW:
            return True
    # b. history words only count when a current value is stated elsewhere (same sentence, or the next sentence)
    before = [_clean(toks[k][2]) for k in range(max(0, i - PAST_WINDOW), i)]
    before = [NEG_POST_TOK_WORD if w.startswith(NEG_POST_TOK) else w for w in before]     # "the outdated 15,000": adjective use
    past_at = [k for k, w in enumerate(before) if PAST_BEFORE_RE.fullmatch(w) or w == NEG_POST_TOK_WORD]
    past = bool(past_at) and not any(COPULA_RE.fullmatch(w) for w in before[past_at[-1] + 1:]) or \
        any(PAST_AFTER_RE.fullmatch(_clean(toks[k][2])) for k in range(i + 1, min(len(toks), i + 3))) or \
        bool(HISTORY_CLAUSE_RE.search(clause))
    rest = s[:clause_start] + " " + s[clause_end:]
    if past and (cur.anchors(NEG_POST_RE.sub(" ", rest)) or (ctx.nxt and _asserts_current(ctx.nxt, cur))):
        return True
    # c. the customer's own file with a problem; the current value must come AFTER it
    whole = NEG_POST_RE.sub(" ", s)
    if CUSTOMER_RE.search(whole) and PROBLEM_RE.search(whole) and \
            (_asserts_current(s[clause_end:], cur) or (ctx.nxt and _asserts_current(ctx.nxt, cur))):
        return True
    return False


USED_TO_RE = re.compile(_w(r"used to (?:be|have|has|had|stand|apply|require|need)"), re.I)


def _mark(sentence: str) -> str:
    s = USED_TO_RE.sub(" was ", _prep(sentence))                     # "used to be 15,000" is plain past tense
    s = DOUBLE_NEG_RE.sub(f" {KEEP_TOK} ", s)
    s = NEG_POST_RE.sub(lambda m: " " + NEG_POST_TOK + " ", s)
    return NEG_PRE_RE.sub(lambda m: " " + NEG_PRE_TOK + " ", s)


SUPERSESSION_CTX_RE = re.compile(_w(
    r"previous(?:ly)?|former(?:ly)?|earlier|original(?:ly)?|prior|legacy|pre-?\w+|used to|until|till|"
    r"outdated|out-of-date|obsolete|superseded|stale|expired|deprecated|retired|discontinued|replaced|replac(?:es|ing)|no longer|old(?:er)?(?=\s+(?:value|figure|limit|ceiling|layout|format|version|rate|v\d|\d))|(?:was|were)(?=\s+(?:[A-Za-z]{1,4}\.?\s*)?\d)|"
    r"pehle|pahle|purana|purane|purani|tha|thi|पहले|पुराना|पुराने|पुरानी|था|थी|"
    r"سابقا|السابق\w*|القديم\w*|قديم\w*|الأصلي|كان|كانت|لم يعد|لم تعد") + "|" + re.escape(NEG_POST_TOK) + "|" + re.escape(NEG_PRE_TOK), re.I)


def _has_supersession_context(s: str) -> bool:
    """The sentence talks about an old / replaced / previous value in some way (so its direction is for the LLM check to judge),
    and does not say the value persists ("still", "unchanged"). Used only for stale numbers whose direction the slot and clause
    patterns could not read; clear-cut asserts (no context at all, or the number in the current slot) are still flagged here."""
    return bool(SUPERSESSION_CTX_RE.search(s)) and not PERSIST_RE.search(s)


def asserted_in_sentence(sentence: str, stale: set[str], cur: CurrentValues, ctx: Context | None = None) -> set[str]:
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
        elif not _clause_ok(s, m.start(), cur, ctx) and not _has_supersession_context(s):
            out.add(n)                                  # no marker and no supersession context at all: asserted. (Wording that has history
                                                        # context but no readable direction goes to the LLM stale-claim check, stale_llm.py.)
    return out


def asserted_stale(draft: str, stale: set[str], cur: CurrentValues | None = None) -> set[str]:
    sents = [x for x in SENT_SPLIT_RE.split(draft or "") if x and x.strip()]
    out: set[str] = set()
    for k, sent in enumerate(sents):
        ctx = Context(sents[k + 1] if k + 1 < len(sents) else "", sent.rstrip().endswith(("?", "؟")))
        out |= asserted_in_sentence(sent, stale, cur or CurrentValues(), ctx)
    return out
