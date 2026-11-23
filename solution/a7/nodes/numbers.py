"""Number normaliser: every numeric mention in a text -> one canonical value. Table-driven, no LLM, no per-string rules.

Why: the output guard compares numbers ("is 14 in the evidence?", "does the draft assert the stale 14?"). People write the same
number many ways: 14 | 14.0 | ١٤ | १४ | fourteen | أربعة عشر | चौदह ;  15,000 | 15k | 15 thousand | fifteen thousand | 15 hazaar |
पंद्रह हज़ार | خمسة عشر ألف ;  Rs 15,000 | INR 15,000 | ₹15,000 | AED 15,000 | د.إ 15,000 | 15,000 rupees.
All of them must reach the checks as the SAME canonical value, on BOTH sides (draft and evidence), or the check is bypassable (a stale
value written in words) or unfair (a grounded value written as 25k rejected).

Design in four lines:
  1. scan()      split text into tokens: digit-runs, words (letters + combining marks), everything else.
  2. lexicon     LEXICON maps a folded word to (kind, value): add (1-99), hundred, scale (thousand, lakh, crore, ...), plus connectors.
  3. parse       a left-to-right state machine sums "add"/"hundred" words and multiplies by "scale" words; a digit-run followed by a
                 scale word or an attached "k" is multiplied the same way ("15 thousand", "1.5 lakh", "25k").
  4. output      find_mentions() -> Mention(span, canonical value, form, currency); canonicalise() rewrites non-digit forms to plain
                 digits so every downstream regex (slots, clauses, grounding) keeps working on one spelling.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

# ---- digits -------------------------------------------------------------------------------------------------------
DIGIT_MAP = str.maketrans({**{chr(0x0660 + i): str(i) for i in range(10)},       # Arabic-Indic
                           **{chr(0x06F0 + i): str(i) for i in range(10)},       # Extended Arabic-Indic (Persian/Urdu)
                           **{chr(0x0966 + i): str(i) for i in range(10)}})      # Devanagari


def norm_digits(tok: str) -> str:
    """'15,000' -> '15000'; '1,5' -> '1.5'; '014' -> '14'; '14.0' stays '14.0' (as the original check did)."""
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", tok):
        tok = tok.replace(",", "")
    tok = tok.replace(",", ".")
    return str(int(tok)) if re.fullmatch(r"\d+", tok) else tok.rstrip(".")


def _canon(value: Decimal) -> str:
    return str(int(value)) if value == value.to_integral_value() else format(value.normalize(), "f")


# ---- folding: make spelling variants equal (applied to table keys AND to text) --------------------------------------
def fold(word: str) -> str:
    w = unicodedata.normalize("NFD", word.lower())
    w = "".join(c for c in w if c not in "़ـ" and not ("ً" <= c <= "ٟ") and c != "ٰ")  # nukta, tatweel, harakat
    w = w.replace("ँ", "ं")                                   # chandrabindu -> anusvara  (पाँच = पांच)
    w = w.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي"}))
    return unicodedata.normalize("NFC", w)


# ---- lexicon ------------------------------------------------------------------------------------------------------
ADD, HUNDRED, SCALE = "add", "hundred", "scale"
LEXICON: dict[str, tuple[str, int]] = {}


def _reg(kind: str, value: int, *words: str) -> None:
    for w in words:
        LEXICON[fold(w)] = (kind, value)


# English
for _v, _w in enumerate("one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
                        "eighteen nineteen".split(), 1):
    _reg(ADD, _v, _w)
for _v, _w in zip(range(20, 100, 10), "twenty thirty forty fifty sixty seventy eighty ninety".split()):
    _reg(ADD, _v, _w)
_reg(HUNDRED, 100, "hundred")
_reg(SCALE, 1_000, "thousand", "hazaar", "hazar", "hajar", "hazaar", "हज़ार", "हजार", "ألف", "الف", "آلاف", "الاف")
_reg(SCALE, 100_000, "lakh", "lakhs", "lac", "lacs", "लाख", "لاخ")
_reg(SCALE, 1_000_000, "million", "millions", "मिलियन", "مليون", "ملايين")
_reg(SCALE, 10_000_000, "crore", "crores", "karod", "करोड़", "करोड", "كرور")

# Arabic 1-100 (masculine + feminine forms). 11-19 = unit + عشر, 21-99 = unit + و + tens, so the parser adds them.
for _v, _ws in enumerate([("واحد", "واحدة", "احد", "احدى"), ("اثنان", "اثنين", "اثنا", "اثني", "اثنتان", "اثنتين"), ("ثلاثة", "ثلاث"),
                          ("اربعة", "اربع"), ("خمسة", "خمس"), ("ستة", "ست"), ("سبعة", "سبع"), ("ثمانية", "ثماني", "ثمان"),
                          ("تسعة", "تسع")], 1):
    _reg(ADD, _v, *_ws)
_reg(ADD, 10, "عشرة")                                          # 11-19 = unit + عشر/عشرة (see ARABIC_TEN)
_reg(ADD, 10, "عشر")
for _v, _ws in zip(range(20, 100, 10), [("عشرون", "عشرين"), ("ثلاثون", "ثلاثين"), ("اربعون", "اربعين"), ("خمسون", "خمسين"),
                                        ("ستون", "ستين"), ("سبعون", "سبعين"), ("ثمانون", "ثمانين"), ("تسعون", "تسعين")]):
    _reg(ADD, _v, *_ws)
_reg(HUNDRED, 100, "مئة", "مائة", "ميه", "مئه", "مائه")
_reg(SCALE, 2_000, "الفين", "ألفين", "الفان")

# Hindi 1-100 (Devanagari). Variant spellings share a value. One word per number, so "add" with no combining.
HINDI = """एक दो तीन चार पाँच,पांच छह,छः,छ: सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह,पन्द्रह सोलह सत्रह अठारह उन्नीस बीस
इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस,अठाईस उनतीस तीस इकतीस बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस चालीस
इकतालीस बयालीस तैंतालीस चौवालीस,चवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास पचास इक्यावन बावन तिरपन,तिरेपन चौवन पचपन छप्पन सत्तावन
अट्ठावन,अठावन उनसठ साठ इकसठ बासठ तिरसठ,तिरेसठ चौंसठ पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर
छिहत्तर सतहत्तर अठहत्तर उनासी,उन्यासी अस्सी इक्यासी बयासी तिरासी चौरासी पचासी छियासी सतासी अठासी नवासी नब्बे इक्यानवे बानवे तिरानवे चौरानवे
पंचानवे छियानवे सत्तानवे अट्ठानवे,अठानवे निन्यानवे""".split()
assert len(HINDI) == 99, len(HINDI)
for _v, _ws in enumerate(HINDI, 1):
    _reg(ADD, _v, *_ws.split(","))
_reg(HUNDRED, 100, "सौ")

# Hinglish (Hindi in Latin letters) 1-99, same order as HINDI above; "," separates spelling variants. A word is left out when it
# is also an everyday English/Hinglish word and would turn prose into numbers: do (2), teen (3), char (4), tera (13), saath (60), sat, tis, bis.
HINGLISH = """ek do! teen! chaar,char! paanch,panch chhah,chhe,chheh saat aath nau das,dus
gyarah,gyaarah,igyarah barah,baarah terah chaudah,chaudha,chodah pandrah,pandra,pandrha solah satrah atharah,aathrah unnis,unnees,unis
bees ikkis,ikkees,ekkis baais,baees,bais teis,teyis chaubis,chaubees pachchis,pachis,pachees,pachchees,pacchis chhabbis,chhabbees,chabbis
sattais,sattaees,sataees atthais,athais,atthaees untis,unatees,untees tees ikatis,iktis,ikattis battis,battees taintis,tentis,tetis chautis,chauntis
paintis,paitis,paintees chhattis,chhatis saintis,saintees adtis,artis,adatis untalis,unchalis chalis,chaalis,chalees ikatalis,iktalis bayalis,bayaalis
taintalis,tetalis chauvalis,chaualis,chawalis paintalis,paitalis chiyalis,chhiyalis,chheyalis saintalis,saitalis adtalis,artalis unchas,unanchas
pachas,pachaas ikyavan,ikkyawan bavan,baavan tirpan,tirepan,trepan chauvan,chauwan pachpan,pachpun,pachapan chhappan,chappan sattavan,satavan
atthavan,athaavan unsath,unasath satth! iksath basath tirsath chausath painsath chhiyasath sadsath adsath unhattar sattar ikhattar bahattar
tihattar chauhattar pachhattar,pachattar chhihattar sathattar athhattar unyasi,unasi assi ikyasi bayasi tirasi chaurasi pachasi chhiyasi satasi
athasi navasi nabbe ikyanve banve tiranve chauranve panchanve chhiyanve sattanve atthanve ninyanve,ninnanve""".split()
assert len(HINGLISH) == 99, len(HINGLISH)
for _v, _ws in enumerate(HINGLISH, 1):
    if _ws.endswith("!"):                # excluded: ambiguous with an ordinary word
        continue
    _reg(ADD, _v, *_ws.split(","))
_reg(HUNDRED, 100, "sau")

# Multiplier suffixes that may sit right after DIGITS only ("25k"); a bare letter k is not a word.
DIGIT_SUFFIX = {"k": 1_000}

# Currency markers around a number (recorded on the Mention, and dropped when a word-number is rewritten to digits).
CURRENCY_PREFIX_RE = re.compile(r"(?:₹|(?<![^\W\d_])(?:rs|inr|aed|dhs?|usd)\.?|د\.إ\.?|د\.ا\.?)\s*$", re.I)
CURRENCY_WORDS = {fold(w) for w in ("rupee", "rupees", "rupaye", "rupay", "rs", "inr", "aed", "dirham", "dirhams", "dhs", "dh",
                                    "روبية", "روبيه", "روبيات", "درهم", "دراهم", "रुपये", "रुपए", "रुपया", "रु")}

SMALL_WORD_MAX = 10      # "one", "two" ... "ten" written as words are ordinary prose; they are not grounding claims (see numbers_in)


# ---- scanning -----------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Token:
    kind: str       # "num" | "word" | "other"
    text: str
    start: int
    end: int


def _is_word_char(c: str) -> bool:
    return c.isalpha() or unicodedata.category(c).startswith("M") or c in "ـ"


def scan(text: str) -> list[Token]:
    out: list[Token] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isdigit() and c.isascii():
            j = i + 1
            while j < n and (text[j].isdigit() and text[j].isascii() or (text[j] in ",." and j + 1 < n and text[j + 1].isdigit() and text[j + 1].isascii())):
                j += 1
            out.append(Token("num", text[i:j], i, j))
            i = j
        elif _is_word_char(c):
            j = i + 1
            while j < n and _is_word_char(text[j]):
                j += 1
            out.append(Token("word", text[i:j], i, j))
            i = j
        else:
            out.append(Token("other", c, i, i + 1))
            i += 1
    return out


@dataclass(frozen=True)
class Mention:
    start: int
    end: int
    value: str            # canonical: "14", "15000", "1.5"
    form: str             # "digits" | "shorthand" (15k, 15 thousand) | "words" (fourteen, चौदह)
    currency: bool = False


def _lookup(word: str) -> tuple[str, int] | None:
    w = fold(word)
    if w in LEXICON:
        return LEXICON[w]
    if w.startswith("و") and fold(w[1:]) in LEXICON:           # Arabic "and" glued to the next word: وعشرون
        return LEXICON[fold(w[1:])]
    return None


def _glued_and(word: str) -> bool:
    w = fold(word)
    return w not in LEXICON and w.startswith("و") and w[1:] in LEXICON


ARABIC_TEN = {fold("عشر"), fold("عشرة")}                      # "unit + ten" = 13..19 in Arabic (ثلاثة عشر)


class _Acc:
    """Sums one spoken number: [hundreds/tens/units] [scale] [hundreds/tens/units] [lower scale] ... Refuses a word that cannot continue it."""

    def __init__(self) -> None:
        self.total = 0
        self.part = 0              # hundreds collected below the next scale word
        self.small = 0             # the 0-99 piece being built
        self.last_scale = 10 ** 12
        self.n = 0

    def accept(self, word: str) -> bool:
        kind, v = _lookup(word)  # type: ignore[misc]
        if kind == ADD:
            if self.small == 0:
                self.small = v
            elif self.small >= 20 and self.small % 10 == 0 and v < 10:
                self.small += v                                               # twenty five
            elif self.small < 10 and v == 10 and fold(word) in ARABIC_TEN:
                self.small += v                                               # ثلاثة عشر = 13
            elif self.small < 10 and v >= 20 and _glued_and(word):
                self.small += v                                               # خمسة وعشرون = 25
            else:
                return False
        elif kind == HUNDRED:
            if self.part:
                return False
            self.part = (self.small or 1) * 100
            self.small = 0
        elif kind == SCALE:
            if v >= self.last_scale:
                return False
            self.total += ((self.part + self.small) or 1) * v if v != 2_000 else 2_000
            self.part = self.small = 0
            self.last_scale = v
        else:
            return False
        self.n += 1
        return True

    def value(self) -> int:
        return self.total + self.part + self.small


def _skip_space(toks: list[Token], k: int, chars: str = " \u00a0") -> int:
    while k < len(toks) and toks[k].kind == "other" and toks[k].text in chars:
        k += 1
    return k


def _word_number(toks: list[Token], i: int) -> tuple[Mention, int] | None:
    """Spoken number starting at toks[i]: returns (mention, index of the next token) or None."""
    acc = _Acc()
    if not acc.accept(toks[i].text):
        return None
    j, end = i + 1, toks[i].end
    while True:
        k = _skip_space(toks, j, " \u00a0-")
        if k >= len(toks) or toks[k].kind != "word":
            break
        if toks[k].text.lower() == "and":                                      # "one hundred and fifty"
            k2 = _skip_space(toks, k + 1)
            if k2 < len(toks) and toks[k2].kind == "word" and (acc.part or acc.total) and acc.small == 0 \
                    and (lk := _lookup(toks[k2].text)) and lk[0] in (ADD, HUNDRED) and acc.accept(toks[k2].text):
                j, end = k2 + 1, toks[k2].end
                continue
            break
        if not _lookup(toks[k].text) or not acc.accept(toks[k].text):
            break
        j, end = k + 1, toks[k].end
    return Mention(toks[i].start, end, _canon(Decimal(acc.value())), "words"), j


def _digit_number(toks: list[Token], i: int, text: str) -> tuple[Mention, int]:
    """Digit-run at toks[i], optionally followed by an attached k or a scale word: 14 | 25k | 15 thousand | 1.5 lakh."""
    t = toks[i]
    plain = norm_digits(t.text)
    nxt = toks[i + 1] if i + 1 < len(toks) else None
    if nxt and nxt.kind == "word" and nxt.start == t.end and nxt.text.lower() in DIGIT_SUFFIX:
        return Mention(t.start, nxt.end, _canon(Decimal(plain) * DIGIT_SUFFIX[nxt.text.lower()]), "shorthand"), i + 2
    k = _skip_space(toks, i + 1)
    if k < len(toks) and toks[k].kind == "word" and (lk := _lookup(toks[k].text)) and lk[0] == SCALE and not _glued_and(toks[k].text):
        return Mention(t.start, toks[k].end, _canon(Decimal(plain) * lk[1]), "shorthand"), k + 1
    return Mention(t.start, t.end, plain, "digits"), i + 1


def find_mentions(text: str) -> list[Mention]:
    """All numeric mentions of `text`, left to right. Digits of other scripts are mapped to ASCII first (same length)."""
    text = text.translate(DIGIT_MAP)
    toks = scan(text)
    out: list[Mention] = []
    i = 0
    while i < len(toks):
        found = None
        if toks[i].kind == "num":
            found = _digit_number(toks, i, text)
        elif toks[i].kind == "word" and (lk := _lookup(toks[i].text)) and lk[0] in (ADD, HUNDRED, SCALE):
            found = _word_number(toks, i)
        if not found:
            i += 1
            continue
        m, i = found
        # currency: a marker right before ("Rs 15,000", "₹15,000", "د.إ 15,000") or a word right after ("fifteen thousand rupees")
        k = _skip_space(toks, i)
        cur = bool(CURRENCY_PREFIX_RE.search(text[:m.start])) or \
            (k < len(toks) and toks[k].kind == "word" and fold(toks[k].text) in CURRENCY_WORDS)
        out.append(Mention(m.start, m.end, m.value, m.form, cur))
    return out


def canonicalise(text: str) -> str:
    """Rewrite every non-digit numeric mention (words, 25k, 15 thousand) as plain ASCII digits; plain digit mentions stay as written
    (so '15,000' keeps its comma). Digits of other scripts become ASCII. Idempotent."""
    text = text.translate(DIGIT_MAP)
    out, pos = [], 0
    for m in find_mentions(text):
        if m.form == "digits":
            continue
        out += [text[pos:m.start], m.value]
        pos = m.end
    out.append(text[pos:])
    return "".join(out)


def numbers_in(text: str, keep: frozenset[str] | set[str] = frozenset(), include_small: bool = False) -> list[str]:
    """Canonical numbers mentioned in `text`, for grounding. Small numbers written as WORDS (one ... ten: "one moment", "two options")
    are prose, not claims, so they are skipped unless their value is in `keep` (e.g. the stale values, which must always be seen)."""
    t = re.sub(r"(?m)^\s*\d+[.)]\s", " ", text.translate(DIGIT_MAP))
    return [m.value for m in find_mentions(t)
            if include_small or not (m.form == "words" and m.value.isdigit() and int(m.value) <= SMALL_WORD_MAX and m.value not in keep)]
