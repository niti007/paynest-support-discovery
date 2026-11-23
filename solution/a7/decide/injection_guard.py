"""Human-route rule for prompt injection in Arabic / Hindi tickets (a documented Gate 2 exception).

The LLM injection check (Qwen3-32B) is weakest on ar/hi, so every ar/hi ticket whose text is *instruction-like*
is routed to a person regardless of what the backend said. "Instruction-like" is defined generically from the
threat model (imperative / meta language aimed at the assistant), NOT from any eval data:

  1. override verbs      ignore / disregard / forget / override (previous|above|all) instructions or rules
  2. persona reset       "you are now ...", "act as ...", "from now on you ..."
  3. role markers        system: / assistant: / developer: / [INST] / <|im_start|> / ### system / <system>
  4. prompt exfiltration reveal / print / repeat your system prompt, instructions, tools
  5. tool / function syntax   function_call, tool_call, {"name": ..., "arguments": ...}, ```json tool blocks
  6. encoded payloads    long base64 or hex runs

Patterns exist in English (injections in ar/hi tickets are often mixed-language), Devanagari + romanised Hindi
and Arabic. Pure text matching, no model, deterministic. Input text is masked ticket text; it is never logged.
"""
from __future__ import annotations

import re

HUMAN_REVIEW_LANGS = frozenset({"ar", "hi"})

_I = re.IGNORECASE | re.UNICODE


def _c(*pats: str) -> list[re.Pattern]:
    return [re.compile(p, _I) for p in pats]


# 1. override verbs ------------------------------------------------------------------------------------------
_OVERRIDE = _c(
    r"\b(ignore|disregard|forget|override|bypass|discard)\b.{0,40}\b(instruction|instructions|rules?|prompt|guidelines?|polic(?:y|ies)|above|previous|prior|earlier)\b",
    r"\b(previous|prior|above|earlier)\s+(instructions?|rules?|prompts?|messages?)\b",
    # Hindi, Devanagari
    r"(पिछले|पहले\s+के|ऊपर\s+के|सभी|सारे)?\s*(निर्देश|निर्देशों|आदेश|नियम|नियमों|हिदायत)\s*(को)?\s*(अनदेखा|नज़रअंदाज़|नजरअंदाज|नजरंदाज|भूल|अनदेखी|अनदेखा\s+कर|रद्द|छोड़)",
    r"(अनदेखा|नज़रअंदाज़|नजरअंदाज|भूल\s+जाओ|भूल\s+जाइए|भूल\s+जा).{0,30}(निर्देश|आदेश|नियम)",
    # Hindi, romanised
    r"\b(pichhle|pichle|purane|saare|sabhi|upar\s+ke)\b.{0,30}\b(nirdesh|nirdeshon|aadesh|niyam|niyamon|instructions?)\b",
    r"\b(nirdesh|nirdeshon|aadesh|niyam|niyamon|instructions?|rules?)\b.{0,30}\b(ignore|andekha|andekha\s+karo|nazarandaz|nazarandaaz|bhool|bhul|bhool\s+jao)\b",
    r"\b(ignore|andekha|nazarandaz|bhool\s+jao|bhul\s+jao)\b.{0,30}\b(nirdesh|aadesh|niyam|instructions?|rules?|prompt)\b",
    # Arabic
    r"(تجاهل|تجاهلي|تجاهلوا|انسَ|انس|أهمل|اهمل|تخطَّ|تخط|تجاوز)\s*(كل|جميع)?\s*(ال)?(تعليمات|التعليمات|الأوامر|اوامر|أوامر|القواعد|قواعد|الإرشادات|ارشادات|السابقة)",
    r"(التعليمات|الأوامر|القواعد|الإرشادات)\s*(ال)?(سابقة|السابقة|أعلاه|اعلاه)",
)

# 2. persona reset -------------------------------------------------------------------------------------------
_PERSONA = _c(
    r"\byou\s+are\s+now\b",
    r"\bfrom\s+now\s+on\b.{0,30}\byou\b",
    r"\b(act|behave|respond|pretend)\s+(as|like)\s+(an?\s+)?(unrestricted|jailbroken|different|new|admin|developer|dan)\b",
    r"\bpretend\s+(to\s+be|you\s+are)\b",
    r"\bnew\s+(instructions?|role|persona)\b",
    r"\bdeveloper\s+mode\b|\bjailbreak\b",
    r"(अब\s+से\s+(तुम|आप)|तुम\s+अब|आप\s+अब|अब\s+तुम|अब\s+आप).{0,40}(हो|हैं|बन|हो\s+गए|एजेंट|असिस्टेंट|सहायक)",
    r"(नया|नई)\s+(निर्देश|भूमिका|आदेश)",
    r"\b(ab\s+se\s+(tum|aap)|tum\s+ab|aap\s+ab)\b",
    r"\bnaye\s+(nirdesh|niyam|aadesh)\b",
    r"(أنت\s+الآن|انت\s+الان|من\s+الآن\s+(فصاعدا|فصاعداً|أنت|انت)|تصرف\s+(ك|كأنك)|تظاهر\s+(بأنك|انك|بأنّك))",
    r"(تعليمات|أوامر)\s+جديدة",
)

# 3. role markers / chat-template tokens ---------------------------------------------------------------------
_ROLE = _c(
    r"(^|[\n\r>\]\)\s])\s*(system|assistant|developer|admin(?:istrator)?)\s*[:：]\s*\S",
    r"\[/?\s*(INST|SYS|SYSTEM)\s*\]|<<\s*/?SYS\s*>>",
    r"<\|\s*(im_start|im_end|system|assistant|user|endoftext)\s*\|?>",
    r"<\s*/?\s*(system|assistant|developer|instructions?)\s*>",
    r"^#{1,4}\s*(system|instruction|assistant)\b",
    r"\b(सिस्टम|एडमिन|डेवलपर|असिस्टेंट)\s*[:：]",
    r"\b(sistem|system)\s+(sandesh|message|prompt)\b",
    r"(^|[\n\r\s])(النظام|المطور|المساعد|المسؤول|مسؤول\s+النظام)\s*[:：]",
    r"(رسالة|موجه|تعليمات)\s+النظام",
    r"(सिस्टम)\s+(संदेश|प्रॉम्प्ट|निर्देश)",
)

# 4. prompt / instruction exfiltration -----------------------------------------------------------------------
_EXFIL = _c(
    r"\b(reveal|show|print|repeat|output|display|leak|tell\s+me|share|dump|disclose)\b.{0,40}\b(system\s+prompt|your\s+(initial\s+)?(prompt|instructions?|rules|configuration|tools?|functions?)|hidden\s+(prompt|instructions?)|api\s*keys?|secrets?)\b",
    r"\bwhat\s+(are|is)\s+your\s+(system\s+)?(instructions?|prompt|rules)\b",
    r"(अपना|अपने|आपका|तुम्हारा|तुम्हारे)\s*(सिस्टम)?\s*(प्रॉम्प्ट|निर्देश|नियम|कॉन्फ़िगरेशन|कॉन्फ़िग|कॉन्फ़िगरेशन)\s*(बताओ|बताइए|दिखाओ|दिखाइए|प्रिंट|दोहराओ|बताएं|बता)",
    r"(सिस्टम\s+प्रॉम्प्ट|गुप्त\s+(निर्देश|प्रॉम्प्ट))\s*(बताओ|बताइए|दिखाओ|दिखाइए|प्रिंट|दोहराओ|बताएं)?",
    r"\b(apna|apne|aapka|tumhara)\s+(system\s+)?(prompt|instructions?|nirdesh|niyam)\s+(batao|bataiye|dikhao|dikhaiye|print|repeat|batao)\b",
    r"\bsystem\s+prompt\b",
    r"(اكشف|أظهر|اظهر|اعرض|اطبع|كرر|أخبرني|اخبرني|انسخ|سرّب|سرب)\s*.{0,30}(موجه\s+النظام|تعليماتك|أوامرك|اوامرك|قواعدك|إعداداتك|برومبت|التعليمات\s+الداخلية|الموجه|مفاتيح|أسرار)",
    r"(موجه\s+النظام|برومبت\s+النظام|تعليمات\s+النظام)",
)

# 5. tool / function-call syntax -----------------------------------------------------------------------------
_TOOL = _c(
    r"\b(function_call|tool_call|tool_use|tool_calls|function_name|call_tool|invoke_tool|execute_tool)\b",
    r"\"(name|tool|function)\"\s*:\s*\"[^\"]+\"\s*,\s*\"(arguments|args|parameters|input)\"\s*:",
    r"<\s*(tool_call|function_call|invoke|function|tool)\b[^>]*>",
    r"```\s*(json|tool|python|bash|sh)\b",
    r"\b(approve|refund|delete|transfer|grant|escalate|reset|disable)_[a-z_]{2,}\s*\(",
    r"\bcall\s+the\s+(tool|function|api)\b",
    r"(टूल|फ़ंक्शन|फंक्शन)\s*(कॉल|को\s+कॉल|चलाओ|चलाएं)",
    r"\b(tool|function)\s+(call|chalao|chalayein)\b",
    r"(استدعِ|استدع|نفّذ|نفذ)\s*(ال)?(أداة|اداة|دالة|وظيفة)",
)

# 6. encoded payloads ----------------------------------------------------------------------------------------
_BASE64 = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=])")
_HEX = re.compile(r"(?<![0-9A-Fa-f])(?:0x)?[0-9A-Fa-f]{40,}(?![0-9A-Fa-f])")
_HEX_ESC = re.compile(r"(?:\\x[0-9A-Fa-f]{2}|\\u[0-9A-Fa-f]{4}|%[0-9A-Fa-f]{2}){8,}")
_PLACEHOLDER = re.compile(r"<[A-Z_]+_\d+>")

_GROUPS: list[tuple[str, list[re.Pattern]]] = [
    ("override_instructions", _OVERRIDE),
    ("persona_reset", _PERSONA),
    ("role_marker", _ROLE),
    ("prompt_exfiltration", _EXFIL),
    ("tool_syntax", _TOOL),
]


def instruction_like(text: str) -> str | None:
    """Return the matched group name if the text is instruction-like, else None."""
    if not text:
        return None
    t = _PLACEHOLDER.sub(" ", text)  # masked placeholders such as <IBAN_1> are not payloads
    for name, pats in _GROUPS:
        if any(p.search(t) for p in pats):
            return name
    if _BASE64.search(t) or _HEX.search(t) or _HEX_ESC.search(t):
        return "encoded_payload"
    return None


def needs_human_injection_review(text: str, language: str | None) -> tuple[bool, str]:
    """(True, reason) when language is ar/hi AND the text is instruction-like. Reason is a group name only."""
    if (language or "").lower() not in HUMAN_REVIEW_LANGS:
        return False, "language_not_in_scope"
    g = instruction_like(text)
    return (True, g) if g else (False, "no_instruction_pattern")
