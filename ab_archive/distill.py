"""Distil the test idea from tickets that never got a Hypothesis.

About 900 older tickets have an empty Hypothesis field. Two things can still be recovered from
the title and description with certainty, and only these are stored:

- `idea`: what was changed and where it applied. Assembled by code from the ticket's own words
  (the change named in the title plus the "Trigger on" rules), so it cannot say anything the
  ticket does not. An earlier version let a local model rewrite this as one sentence; about one
  in ten came out subtly wrong, which is why no model is involved here.
- `idea_reason`: why the change was made, as the ticket's own sentences. A local model only
  points at them; each quote must occur word for word in the description, be prose rather than
  a rule or a list item, and read like a reason.

Anything that cannot be tied back to the ticket text is dropped, so an empty reason means "the
ticket does not say", never "nothing was intended". Nothing here is a reconstructed hypothesis.
"""
from __future__ import annotations

from collections.abc import Callable
import json
import re

from .db import ArchiveDB
from .ollama import OllamaClient

# Bump when the prompt or the rules change, so stored results are redone on the next run.
DISTILL_VERSION = "1"

MIN_QUOTE_CHARS = 25
MIN_QUOTE_WORDS = 7
MAX_QUOTES = 2
MAX_SCOPE_RULES = 6
MAX_RULE_CHARS = 110
# Descriptions shorter than this hold trigger rules at most, never an explanation.
MIN_DESCRIPTION_CHARS = 80
MAX_MODEL_FAILURES = 3

_REASON_SYSTEM = """You get the ticket description of a website A/B test.
Find the sentences in which the author explains WHY the change is made: the problem it should solve, the goal, or the result they expect.
Sentences that only describe WHAT is changed, trigger rules, copy texts, code and tracking instructions are NOT explanations.
Copy at most two such sentences exactly, character for character, without changing or shortening them.
Reply with a JSON object: {"quotes": ["<sentence>", ...]}. If the description gives no explanation, reply {"quotes": []}."""

# A quoted sentence only counts as a reason when it reads like one: it names a goal, a cause,
# a problem or an expectation. Guards against the model quoting a plain description of the change.
_REASON_MARKERS = re.compile(
    r"\b(because|since|so that|so we|so they|so users|in order to|goal|aim|we want|want to|hope|expect\w*|"
    r"we believe|we think|assum\w+|hypothes[ie]s|problem|issue|confus\w*|unclear|cannot|can't|can not|"
    r"hard to|difficult|easier|helps?|solve|prevent|avoid|reduce|increase|improve|boost|encourage|"
    r"stimulate|convince|reassure|more likely|less likely|leads? to|result in|might|this way|"
    r"due to|to make it|to see (if|whether|what)|to find out|find out if|found out|we see|we saw|we noticed|"
    r"did not|do not|don't|does not|doesn't|not know|in doubt|feedback|user testing|"
    r"data shows?|skews?|perform\w*|validat\w+|positive side|downside|omdat|zodat|waardoor|verwacht\w*|probleem)\b",
    re.I,
)
# Quotes that are configuration or references rather than an explanation.
_NOT_PROSE = re.compile(
    r"^\s*(-|\*|if |when |trigger|tracking|\{|\[|var |follow[- ]?up|next (step|iteration|test))|https?://",
    re.I,
)
_BULLET = re.compile(r"^\s*[-*•]\s+")
_TRIGGER_HEADER = re.compile(r"^\s*triggers?\b[^:\n]{0,40}:?\s*$", re.I)
_CODE = re.compile(r"[{}=;<>\[\]]|\bvar\b|\bdataLayer\b|https?://|\w+\(", re.I)
_RUN_MARKER = re.compile(r"\s*\b(ITERATION|ITERATON|RERUN|RETEST)\b\s*$", re.I)


def extract_idea(summary: str, description: str) -> str:
    """What was changed and where, in the ticket's own words. Empty when the title names no change."""
    change = _change_from_title(summary)
    if not change:
        return ""
    scope = _trigger_rules(description)
    idea = change.rstrip(". ")
    if scope:
        idea += ". Applies to: " + "; ".join(scope)
    return idea + "."


def find_reason(ollama: OllamaClient, description: str) -> str:
    """The ticket's own explanation of why, quoted. Empty when it gives none."""
    description = (description or "").strip()
    if len(description) < MIN_DESCRIPTION_CHARS:
        return ""
    messages = [{"role": "system", "content": _REASON_SYSTEM}, {"role": "user", "content": description[:6000]}]
    try:
        raw = ollama.chat_json(messages)
    except RuntimeError as exc:
        # On a few descriptions the model aborts its own reply ("token repeat limit reached").
        # That is repeatable for the same text, so it counts as "no answer" rather than as an
        # outage to retry every run. Anything else (model not running) goes to the caller.
        if "prediction aborted" in str(exc):
            return ""
        raise
    return accept_reason(raw, description)


def accept_reason(raw: str, description: str) -> str:
    """Keep quotes that occur word for word in the description and read like a reason."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return ""
    quotes = data.get("quotes") if isinstance(data, dict) else None
    if not isinstance(quotes, list):
        return ""
    haystack = _squash(description)
    # List items are trigger rules, copy and change lists; an explanation is written as prose.
    bullets = [_squash(_BULLET.sub("", line)) for line in description.splitlines() if _BULLET.match(line)]
    kept: list[str] = []
    for quote in quotes:
        if not isinstance(quote, str):
            continue
        quote = " ".join(quote.split())
        squashed = _squash(quote)
        if (
            len(squashed) >= MIN_QUOTE_CHARS
            and len(quote.split()) >= MIN_QUOTE_WORDS
            and squashed in haystack
            and not _NOT_PROSE.search(quote)
            and not any(squashed in bullet for bullet in bullets)
            and _REASON_MARKERS.search(quote)
            and quote not in kept
        ):
            kept.append(quote)
    return " ".join(kept[:MAX_QUOTES])


def distill_missing(
    db: ArchiveDB,
    ollama: OllamaClient | None,
    *,
    limit: int = 0,
    redo: bool = False,
    progress: Callable[[str], None] | None = None,
) -> tuple[int, int]:
    """Fill `idea` / `idea_reason` for tests without a hypothesis. Returns (ideas, reasons).

    A test is redone when its ticket changed or the rules here did. Without a reachable model
    the idea is still stored, but the test stays marked as pending so the reason is looked for
    on a later run.
    """
    def log(message: str) -> None:
        if progress:
            progress(message)

    cleared = db.clear_ideas_where_hypothesis_exists()
    if cleared:
        log(f"Cleared {cleared} distilled idea(s): those tickets now have a written hypothesis.")
    rows = db.tests_needing_idea(DISTILL_VERSION, redo=redo)
    if limit:
        rows = rows[:limit]
    if not rows:
        db.commit()
        return 0, 0
    log(f"Distilling the idea for {len(rows)} test(s) without a hypothesis...")
    ideas = reasons = 0
    failures = 0  # consecutive; one slow or failed call must not cost the rest of the run
    model_ok = ollama is not None
    for index, row in enumerate(rows, start=1):
        idea = extract_idea(row["summary"], row["description"])
        reason, done = "", model_ok
        if model_ok:
            try:
                reason = find_reason(ollama, row["description"])
                failures = 0
            except RuntimeError as exc:
                done = False
                failures += 1
                log(f"Could not look for a reason in {row['key']}, will retry next run: {exc}")
                if failures >= MAX_MODEL_FAILURES:
                    model_ok = False
                    log("Local model unavailable, storing the remaining ideas without reasons for now.")
        stamp = f"{DISTILL_VERSION}|{row['updated'] or ''}" if done else ""
        db.set_idea(row["key"], idea, reason, stamp)
        ideas += bool(idea)
        reasons += bool(reason)
        if index % 50 == 0 or index == len(rows):
            db.commit()
            log(f"Distilled {index}/{len(rows)}: {ideas} ideas, {reasons} with a quoted reason.")
    db.commit()
    return ideas, reasons


def _change_from_title(summary: str) -> str:
    """The part of "HSNL - Property - Do something ITERATION" that names the change.

    Older tickets use "HSNL - Do something" or just "Do something"; then the rest of the title
    is the change.
    """
    parts = re.split(r"\s+[-–]\s+", (summary or "").strip())
    if len(parts) >= 3:
        change = " - ".join(parts[2:])
    elif len(parts) == 2 and re.fullmatch(r"[A-Z]{2,6}", parts[0]):
        change = parts[1]
    else:
        change = (summary or "").strip()
    change = _RUN_MARKER.sub("", change).strip()
    return change if len(change.split()) >= 2 else ""


def _trigger_rules(description: str) -> list[str]:
    """The list items under a "Trigger on:" heading, skipping code and oversized items."""
    rules: list[str] = []
    in_trigger = False
    for line in (description or "").splitlines():
        if not line.strip():
            continue
        if _TRIGGER_HEADER.match(line):
            in_trigger = True
            continue
        if not in_trigger:
            continue
        if not _BULLET.match(line):
            break  # the next heading or paragraph ends the list
        rule = " ".join(_BULLET.sub("", line).split()).rstrip(".;,")
        if rule and len(rule) <= MAX_RULE_CHARS and not _CODE.search(rule) and rule not in rules:
            rules.append(rule)
    return rules[:MAX_SCOPE_RULES]


def _squash(text: str) -> str:
    return re.sub(r"[\s\"'“”‘’`]+", " ", text.lower()).strip(" .")
