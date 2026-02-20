"""PII filtering for Telegram output.

Light filter: strips emails, phone numbers, credit cards, IP addresses, and
IBANs before any text reaches Telegram.  Keeps names, organisations, and
locations so the notification is still useful for triage.

Uses Microsoft Presidio when available, falls back to simple regex patterns.
"""

import re
from core.logging import log

# --- Regex fallback patterns (used when Presidio is not installed) ---

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_PHONE_RE = re.compile(
    r"(?<!\d)"                          # not preceded by digit
    r"(\+?\d{1,3}[-.\s]?)?"            # optional country code
    r"\(?\d{3}\)?[-.\s]?"              # area code
    r"\d{3}[-.\s]?\d{4}"              # number
    r"(?!\d)"                           # not followed by digit
)
_IP_RE = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
_CC_RE = re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[\s]?[\dA-Z]{4}[\s]?(?:[\dA-Z]{4}[\s]?){1,7}[\dA-Z]{1,4}\b")

_REGEX_RULES: list[tuple[re.Pattern, str]] = [
    (_IBAN_RE,  "<iban>"),   # IBAN before CC — CC regex would partial-match IBAN digits
    (_CC_RE,    "<card>"),
    (_EMAIL_RE, "<email>"),
    (_PHONE_RE, "<phone>"),
    (_IP_RE,    "<ip>"),
]

# --- Presidio engine (lazy-loaded) ---

_analyzer = None
_anonymizer = None
_presidio_available: bool | None = None  # None = not checked yet

# Entities to detect — light tier (no PERSON, ORGANIZATION, LOCATION)
_ENTITIES = [
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "IP_ADDRESS",
    "IBAN_CODE",
]


def _load_presidio():
    """Try to load Presidio engines. Sets _presidio_available flag."""
    global _analyzer, _anonymizer, _presidio_available
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_anonymizer import AnonymizerEngine
        from presidio_anonymizer.entities import OperatorConfig

        _analyzer = AnalyzerEngine()
        _anonymizer = AnonymizerEngine()
        _presidio_available = True
        log.info("PII filter: Presidio loaded")
    except ImportError:
        _presidio_available = False
        log.info("PII filter: Presidio not installed, using regex fallback")


def _scrub_presidio(text: str) -> str:
    """Scrub PII using Presidio analyzer + anonymizer."""
    from presidio_anonymizer.entities import OperatorConfig

    results = _analyzer.analyze(text=text, language="en", entities=_ENTITIES)
    if not results:
        return text

    operators = {
        "EMAIL_ADDRESS": OperatorConfig("replace", {"new_value": "<email>"}),
        "PHONE_NUMBER":  OperatorConfig("replace", {"new_value": "<phone>"}),
        "CREDIT_CARD":   OperatorConfig("replace", {"new_value": "<card>"}),
        "IP_ADDRESS":    OperatorConfig("replace", {"new_value": "<ip>"}),
        "IBAN_CODE":     OperatorConfig("replace", {"new_value": "<iban>"}),
    }
    result = _anonymizer.anonymize(text=text, analyzer_results=results,
                                   operators=operators)
    return result.text


def _scrub_regex(text: str) -> str:
    """Scrub PII using simple regex patterns (fallback)."""
    for pattern, replacement in _REGEX_RULES:
        text = pattern.sub(replacement, text)
    return text


def scrub(text: str) -> str:
    """Remove PII from text before sending to Telegram.

    Light filter — strips emails, phones, credit cards, IPs, IBANs.
    Keeps names, organisations, and locations.

    Returns the scrubbed text.  Empty/whitespace input passes through unchanged.
    """
    if not text or not text.strip():
        return text

    global _presidio_available
    if _presidio_available is None:
        _load_presidio()

    if _presidio_available:
        try:
            return _scrub_presidio(text)
        except Exception as e:
            log.warning(f"Presidio scrub failed, falling back to regex: {e}")
            return _scrub_regex(text)

    return _scrub_regex(text)
