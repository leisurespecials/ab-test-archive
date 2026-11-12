"""Pull headline result numbers out of a test's free-text `results` field.

Only ~a quarter of tests have results text, and older ones are free-form, so every field is
optional (None when not found). The first match in the text is used (the overall figure, before
any per-device breakdown). Percentages are returned as plain numbers, e.g. -4.9 for "-4.9%".
"""
from __future__ import annotations

import re

_NUM = r"([+\-−–]?\s?\d+(?:[.,]\d+)?)"
_MINUS = "−–"


def _to_float(raw: str) -> float:
    raw = raw.replace(" ", "").replace(",", ".")
    for ch in _MINUS:
        raw = raw.replace(ch, "-")
    return float(raw)


def _direction(word: str) -> int:
    return -1 if word.lower() in ("down", "drop", "decline", "decrease", "lower") else 1


def _signed_change(text: str, label: str) -> float | None:
    """Find a % change for a metric label: '+4.9%' style, '(−11.5%)', or 'down by 18,2%' style."""
    patterns = [
        # "-4.9% lift in CR"
        rf"{_NUM}\s*%\s*(?:lift|uplift|change)\s+in\s+{label}",
        # "CR: +8.7% (...)", "CR +1.7% (4.72% → 4.80%)", "CR: -6.7%"
        rf"\b{label}\b\s*:?\s*{_NUM}\s*%",
        # "Conversion Rate: 24.48% → 18.28% (-25.3% relative)", "RPO: €29 → €30.43 (+4.9%)"
        rf"\b{label}\b[^\n(]*?→[^\n(]*\(\s*{_NUM}\s*%",
    ]
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            val = _to_float(m.group(1))
            # a bare "CR: 4.39%" with no sign is a rate, not a change; require a sign unless "lift"
            if "lift" in p or m.group(1).strip()[0] in "+-" + _MINUS:
                return val
    # "CR down by 18,2%", "CR drop:6,52%", "Revenue: up by 14,7%"
    m = re.search(rf"\b{label}\b\s*:?\s*(up|down|drop|decline|decrease|increase|lower|higher)\s*(?:by)?\s*:?\s*(\d+(?:[.,]\d+)?)\s*%", text, re.I)
    if m:
        return _direction(m.group(1)) * _to_float(m.group(2))
    return None


def parse_results(text: str | None) -> dict[str, float | str | None]:
    out: dict[str, float | str | None] = {
        "cr_change_pct": None,
        "rpo_change_pct": None,
        "revenue_change_pct": None,
        "confidence_pct": None,
        "confidence_favors": None,
        "control_cr_pct": None,
        "variation_cr_pct": None,
    }
    if not text:
        return out
    out["cr_change_pct"] = _signed_change(text, r"(?:CR|conversion rate)")
    out["rpo_change_pct"] = _signed_change(text, r"RPO")
    out["revenue_change_pct"] = _signed_change(text, r"revenue")

    for pat in (
        r"(\d+(?:\.\d+)?)\s*%\s*(?:probability|confidence)",
        r"(?:probability|confidence)\s*:?\s*(\d+(?:\.\d+)?)\s*%",
    ):
        m = re.search(pat, text, re.I)
        if m:
            out["confidence_pct"] = float(m.group(1))
            line = text[text.rfind("\n", 0, m.start()) + 1 : (text.find("\n", m.end()) if "\n" in text[m.end():] else len(text))]
            if re.search(r"control\s+(?:wins|beats|better)|probability\s+control", line, re.I):
                out["confidence_favors"] = "control"
            else:
                out["confidence_favors"] = "variation"
            break

    m = re.search(r"Control\s+(\d+(?:\.\d+)?)\s*%\s*→\s*Variation\s+(\d+(?:\.\d+)?)\s*%", text)
    if m:
        out["control_cr_pct"], out["variation_cr_pct"] = float(m.group(1)), float(m.group(2))
    return out
