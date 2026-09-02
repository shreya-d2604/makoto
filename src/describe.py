"""Turn a ranked PR into a one-line resume bullet."""

from __future__ import annotations

import re

from fetch import PRRecord
from rank import RankedPR

MAX_DESCRIPTION_LENGTH = 100

# Irregular / common commit-message verbs that don't take a plain "+ed" suffix.
IRREGULAR_PAST_TENSE = {
    "add": "Added",
    "fix": "Fixed",
    "remove": "Removed",
    "update": "Updated",
    "refactor": "Refactored",
    "improve": "Improved",
    "implement": "Implemented",
    "support": "Supported",
    "allow": "Allowed",
    "enable": "Enabled",
    "disable": "Disabled",
    "deprecate": "Deprecated",
    "migrate": "Migrated",
    "rename": "Renamed",
    "replace": "Replaced",
    "simplify": "Simplified",
    "clean": "Cleaned",
    "document": "Documented",
    "test": "Tested",
    "bump": "Bumped",
    "upgrade": "Upgraded",
    "downgrade": "Downgraded",
    "revert": "Reverted",
    "merge": "Merged",
    "split": "Split",
    "move": "Moved",
    "extract": "Extracted",
    "introduce": "Introduced",
    "drop": "Dropped",
    "ignore": "Ignored",
    "serve": "Served",
    "fetch": "Fetched",
    "use": "Used",
    "make": "Made",
    "write": "Wrote",
    "build": "Built",
    "break": "Broke",
    "change": "Changed",
    "correct": "Corrected",
    "resolve": "Resolved",
    "handle": "Handled",
    "avoid": "Avoided",
    "prevent": "Prevented",
    "ensure": "Ensured",
    "validate": "Validated",
    "optimize": "Optimized",
    "reduce": "Reduced",
    "increase": "Increased",
    "expand": "Expanded",
    "extend": "Extended",
    "restrict": "Restricted",
    "guard": "Guarded",
    "wrap": "Wrapped",
    "unify": "Unified",
    "consolidate": "Consolidated",
    "expose": "Exposed",
    "hide": "Hid",
    "stop": "Stopped",
    "set": "Set",
    "run": "Ran",
    "send": "Sent",
    "fall back": "Fell back",
}


def _verb_to_past_tense(word: str) -> str:
    lower = word.lower()
    if lower in IRREGULAR_PAST_TENSE:
        return IRREGULAR_PAST_TENSE[lower]

    if lower.endswith("e"):
        return word.capitalize() + "d"
    if len(lower) > 1 and lower[-1] == "y" and lower[-2] not in "aeiou":
        return word[:-1].capitalize() + "ied"
    if len(lower) >= 3 and lower[-1] not in "aeiouwxy" and lower[-2] in "aeiou" and lower[-3] not in "aeiou":
        return word.capitalize() + word[-1] + "ed"
    return word.capitalize() + "ed"


_CONVENTIONAL_PREFIX_RE = re.compile(r"^\s*[a-zA-Z][\w.-]*(\([^)]*\))?\s*:\s*(.+)$")


def _strip_conventional_prefix(title: str) -> str:
    match = _CONVENTIONAL_PREFIX_RE.match(title)
    if match:
        return match.group(2).strip()
    return title.strip()


def template_bullet(record: PRRecord) -> str:
    """Deterministic fallback: derive a resume bullet from the PR title alone."""
    text = _strip_conventional_prefix(record.title)
    if not text:
        return record.title.strip()

    words = text.split(" ", 1)
    first_word = words[0]
    rest = words[1] if len(words) > 1 else ""

    if first_word.isalpha() and first_word.islower():
        first_word = _verb_to_past_tense(first_word)
    else:
        first_word = first_word[:1].upper() + first_word[1:]

    return f"{first_word} {rest}".strip()


def get_bullet_text(record: PRRecord, ranked: RankedPR | None, config: dict) -> str:
    """Resolve the plain-text description: override > LLM-generated > template.

    This is just the sentence (e.g. "Fixed race condition in shutdown path") -
    no repo/PR reference. How that gets rendered into the resume's LaTeX is
    inject.py's job, since that depends on the resume template's own markup.
    """
    descriptions_cfg = config.get("descriptions", {})
    overrides = descriptions_cfg.get("overrides", {})
    if record.url in overrides:
        text = str(overrides[record.url]).strip()
    else:
        mode = descriptions_cfg.get("mode", "llm")
        if mode == "llm" and ranked is not None and ranked.bullet:
            text = ranked.bullet.strip()
        else:
            text = template_bullet(record)

    if len(text) > MAX_DESCRIPTION_LENGTH:
        text = text[: MAX_DESCRIPTION_LENGTH - 1].rstrip() + "…"
    return text
