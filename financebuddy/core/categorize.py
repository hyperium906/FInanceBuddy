"""Two-tier transaction categorization: cheap rules first, LLM only for the rest.

Tier 1 is a user-editable JSON rules file mapping merchant substrings to
categories. It costs nothing and handles the recurring merchants that make up
most of a statement.

Tier 2 sends only what is left to the model named by ``GEMINI_MODEL_FAST``, in
batches, constrained to :data:`CATEGORIES` by a response schema. Only a scrubbed
merchant name and the amount are sent — never account numbers, and never a raw
description still carrying identifiers.

When the user corrects a category by hand, :func:`learn_rule` writes a new rule
so that merchant never needs the model again.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Sequence

import pandas as pd

from financebuddy.llm import LLMError, generate

log = logging.getLogger(__name__)

#: The closed set of categories. The model may return nothing outside this list.
#: The category vocabulary, which is the workbook's rather than this module's.
#: `_Budgets`, `_Recurring`, and the formula-driven `Budget Sheet` report all
#: key off these exact strings, so the list follows the sheet — a name the
#: categorizer emits that no budget row uses is spending that silently lands
#: in an unbudgeted category, which is the failure this ordering prevents.
CATEGORIES: tuple[str, ...] = (
    "Groceries",
    "Dining",
    "Gas / Transportation",
    "Utilities",
    "Housing",
    "Subscriptions",
    "Giving",
    "Shopping",
    "Health",
    "Debt",
    # Money you front for other people — a group meal, a shared trip, tickets
    # for the table. It is genuinely spent until they pay you back, so it is
    # not a transfer; but it was never your money, so it must not eat a budget
    # you set for yourself. Inflows here net against outflows, leaving exactly
    # what you are still out of pocket.
    "Reimbursable",
    "Income",
    # Moving money between your own accounts. The Budget page already writes
    # allocation rows under this name and affordability.py already excludes it
    # from discretionary spending — it was simply missing from the vocabulary
    # the categorizer may choose from, so an imported transfer had nowhere to
    # go and landed in Other, where it read as money spent.
    "Transfer",
    "Other",
)

#: Assigned when neither tier could decide.
UNCATEGORIZED = "Uncategorized"

#: Transactions sent per LLM call.
BATCH_SIZE = 40

#: Shipped starter rules; override with the CATEGORY_RULES_PATH env var.
DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent / "category_rules.json"


def rules_path() -> Path:
    """Where the rules file lives, honouring ``CATEGORY_RULES_PATH``."""
    override = os.getenv("CATEGORY_RULES_PATH", "").strip()
    return Path(override) if override else DEFAULT_RULES_PATH


# --------------------------------------------------------------------------
# Merchant scrubbing
# --------------------------------------------------------------------------

# Ordered, and the order matters: labelled identifiers go first, because
# stripping bare digit runs ahead of them would strand the label ("ACCT 12345"
# -> "ACCT") and leak it into the prompt.
_SCRUB_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Identifier labels, with or without a value following them.
    (re.compile(r"\b(?:ref|id|acct|account|auth|trace|inv|invoice|conf|txn|trn)"
                r"\b(?:\s*[:#]?\s*\S+)?", re.I), " "),
    # Masked card numbers: XXXX1234, ****5678, x-1234. Anchored to token
    # boundaries so it cannot eat the "xx" inside a merchant like "EXXON".
    (re.compile(r"(?<![a-z0-9])[x*#]{2,}[\s\-]*\d*(?![a-z])", re.I), " "),
    # Order/confirmation tokens: 5+ chars mixing at least two letters and two
    # digits. Keeps "h-e-b", "76", "7-eleven"; drops "2H4KL9DQ3".
    (re.compile(r"\b(?=[a-z0-9\-]*[a-z][a-z0-9\-]*[a-z])"
                r"(?=[a-z0-9\-]*\d[a-z0-9\-]*\d)[a-z0-9\-]{5,}\b", re.I), " "),
    # Any remaining run of 4+ digits — card, account, invoice, phone, trace.
    (re.compile(r"\d{4,}"), " "),
    # Bank/rail noise that carries no merchant signal.
    (re.compile(r"\b(?:pos|ach|eft|dbt|crd|debit|credit|purchase|payment|pmt|"
                r"recurring|visa|mastercard|amex|withdrawal|deposit|sq|tst|py|"
                r"dda|pur|xfer|ppd|des)\b", re.I), " "),
    # Dates left in the description.
    (re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b"), " "),
    # Store numbers: "#417", "STORE 22"
    (re.compile(r"#\s*\d+"), " "),
    (re.compile(r"\bstore\s+\d+\b", re.I), " "),
    # Trailing state code: "SAN FRANCISCO CA"
    (re.compile(r"\b[A-Z]{2}\s*$"), " "),
)

_PUNCT = re.compile(r"[^\w&'\-. ]+")
_SPACES = re.compile(r"\s+")


def scrub_merchant(description: str, max_length: int = 48) -> str:
    """Reduce a raw bank description to a bare merchant name.

    Strips card and account numbers, reference and auth IDs, transaction-rail
    noise, dates, and store numbers, so that nothing identifying is left to
    send to a third-party model.

    >>> scrub_merchant("POS DEBIT TRADER JOE'S #417 XXXXXX4412 SAN JOSE CA")
    "trader joe's san jose"
    """
    if not description:
        return ""
    text = str(description)
    for pattern, replacement in _SCRUB_PATTERNS:
        text = pattern.sub(replacement, text)
    text = _PUNCT.sub(" ", text)
    text = _SPACES.sub(" ", text).strip().lower()
    return text[:max_length].strip()


# --------------------------------------------------------------------------
# Tier 1: rules
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rule:
    """One substring-to-category mapping."""

    match: str
    category: str


def load_rules(path: Path | str | None = None) -> list[Rule]:
    """Read the rules file, longest pattern first so specific rules win.

    A missing or malformed file yields an empty rule set and a warning rather
    than an exception — categorization still works, it just falls through to
    the model.
    """
    target = Path(path) if path else rules_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.warning("No rules file at %s; starting with no rules.", target)
        return []
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read rules file %s: %s", target, exc)
        return []

    rules = [
        Rule(str(entry["match"]).strip().lower(), str(entry["category"]).strip())
        for entry in payload.get("rules", [])
        if str(entry.get("match", "")).strip() and str(entry.get("category", "")).strip()
    ]
    rules.sort(key=lambda rule: len(rule.match), reverse=True)
    return rules


def save_rules(rules: Sequence[Rule], path: Path | str | None = None) -> None:
    """Write ``rules`` back to disk, preserving the file's comment field."""
    target = Path(path) if path else rules_path()
    existing: dict = {}
    try:
        existing = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass

    payload = {
        "version": existing.get("version", 1),
        "comment": existing.get(
            "comment",
            "Merchant substring -> category. Longest match wins.",
        ),
        "rules": [{"match": rule.match, "category": rule.category} for rule in rules],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


@lru_cache(maxsize=2048)
def _pattern(match: str) -> re.Pattern[str]:
    """Compile a rule pattern that will not match inside a longer word.

    Plain substring matching is too loose: "rent" would fire on "PARENT", and a
    two-letter pattern like "bp" on any merchant containing those letters.
    Guarding both ends with a non-word lookaround keeps short and punctuated
    patterns ("bp", "h-e-b", "apple.com/bill") behaving as whole tokens.
    """
    return re.compile(rf"(?<!\w){re.escape(match.strip())}(?!\w)", re.I)


def apply_rules(merchant: str, rules: Sequence[Rule]) -> str | None:
    """First rule whose pattern occurs in ``merchant`` as a whole token, or None."""
    if not merchant:
        return None
    for rule in rules:
        if _pattern(rule.match).search(merchant):
            return rule.category
    return None


def learn_rule(
    description: str, category: str, path: Path | str | None = None
) -> Rule | None:
    """Record that ``description``'s merchant belongs in ``category``.

    Called when the user recategorizes by hand. The scrubbed merchant becomes
    the pattern, so the same merchant is free to categorize next time. Returns
    the rule written, or None if there was nothing usable to learn.
    """
    merchant = scrub_merchant(description)
    if not merchant or not category or category == UNCATEGORIZED:
        return None

    rules = load_rules(path)
    for existing in rules:
        if existing.match == merchant:
            if existing.category == category:
                return None  # already known
            rules = [r for r in rules if r.match != merchant]
            break

    learned = Rule(merchant, category)
    rules.append(learned)
    rules.sort(key=lambda rule: len(rule.match), reverse=True)
    save_rules(rules, path)
    log.info("Learned rule: %r -> %s", merchant, category)
    return learned


# --------------------------------------------------------------------------
# Tier 2: the model
# --------------------------------------------------------------------------

#: Constrains the model to one category per input index, and nothing else.
RESPONSE_SCHEMA: dict = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "index": {"type": "integer"},
            "category": {"type": "string", "enum": list(CATEGORIES)},
        },
        "required": ["index", "category"],
    },
}

_PROMPT = """You are categorizing personal bank transactions.

For each numbered item below, choose exactly one category from this list:
{categories}

Rules:
- A positive amount is money coming in; a negative amount is money going out.
- Money coming in is almost always Income.
- Choose "Other" only when no other category plausibly fits.
- Return one object per item, echoing the item's index.

Items:
{items}"""


def _batch_prompt(batch: Sequence[tuple[int, str, float]]) -> str:
    """Render one batch as a prompt carrying only merchant names and amounts."""
    lines = [
        f"{index}. merchant={merchant!r} amount={amount:.2f}"
        for index, merchant, amount in batch
    ]
    return _PROMPT.format(
        categories=", ".join(CATEGORIES), items="\n".join(lines)
    )


def categorize_batch(
    batch: Sequence[tuple[int, str, float]], model: str | None = None
) -> dict[int, str]:
    """Categorize one batch, returning ``{index: category}``.

    Only indices the model answered with a valid category appear in the result.
    """
    if not batch:
        return {}

    answer = generate(_batch_prompt(batch), schema=RESPONSE_SCHEMA, model=model)
    if not isinstance(answer, list):
        raise LLMError(f"Expected a JSON array of results, got {type(answer).__name__}.")

    valid = set(CATEGORIES)
    wanted = {index for index, _, _ in batch}
    out: dict[int, str] = {}
    for entry in answer:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("index"))
        except (TypeError, ValueError):
            continue
        category = str(entry.get("category", "")).strip()
        if index in wanted and category in valid:
            out[index] = category
    return out


# --------------------------------------------------------------------------
# The two tiers together
# --------------------------------------------------------------------------


@dataclass(slots=True)
class CategorizeResult:
    """What :func:`categorize_transactions` did, for reporting in the UI."""

    frame: pd.DataFrame
    rule_hits: int = 0
    llm_hits: int = 0
    unresolved: int = 0
    batches: int = 0
    failures: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Default the failure list without sharing one across instances."""
        if self.failures is None:
            self.failures = []

    @property
    def summary(self) -> str:
        """One-line description of the run."""
        parts = [f"{self.rule_hits} by rule", f"{self.llm_hits} by model"]
        if self.unresolved:
            parts.append(f"{self.unresolved} left uncategorized")
        return ", ".join(parts)


def categorize_transactions(
    df: pd.DataFrame,
    description_column: str = "Description",
    amount_column: str = "Amount",
    category_column: str = "Category",
    rules: Sequence[Rule] | None = None,
    model: str | None = None,
    use_llm: bool = True,
    progress: Callable[[float, str], None] | None = None,
) -> CategorizeResult:
    """Fill in ``category_column``, rules first and the model only for the rest.

    Rows that already carry a category are left alone. ``progress(fraction, message)``
    is called as batches complete so the caller can drive a progress bar. A batch
    that fails — rate limited past its retries, or malformed — is logged and
    skipped, leaving those rows uncategorized rather than failing the import.

    ``model`` defaults to ``GEMINI_MODEL_FAST``.
    """
    from financebuddy.config import get_config

    out = df.copy()
    if out.empty:
        return CategorizeResult(frame=out)

    if category_column not in out.columns:
        out[category_column] = ""
    out[category_column] = out[category_column].fillna("").astype(str).str.strip()

    active = list(rules) if rules is not None else load_rules()
    result = CategorizeResult(frame=out)

    # -- tier 1 ------------------------------------------------------------
    pending: list[tuple[int, str, float]] = []
    for position, row in enumerate(out.itertuples(index=False)):
        if str(getattr(row, _attr(out, category_column), "")).strip():
            continue  # already categorized

        raw = str(getattr(row, _attr(out, description_column), "") or "")
        merchant = scrub_merchant(raw)
        matched = apply_rules(merchant, active)
        if matched:
            out.iat[position, out.columns.get_loc(category_column)] = matched
            result.rule_hits += 1
        else:
            amount = _as_float(getattr(row, _attr(out, amount_column), 0.0))
            pending.append((position, merchant or "unknown", amount))

    if progress is not None:
        progress(
            0.0 if pending else 1.0,
            f"{result.rule_hits} matched by rule; {len(pending)} need the model.",
        )

    if not pending or not use_llm:
        result.unresolved = len(pending)
        _fill_blank(out, category_column)
        return result

    # -- tier 2 ------------------------------------------------------------
    target_model = model or get_config().gemini_model_fast
    batches = [pending[i:i + BATCH_SIZE] for i in range(0, len(pending), BATCH_SIZE)]
    result.batches = len(batches)
    resolved = 0

    for number, batch in enumerate(batches, start=1):
        try:
            answers = categorize_batch(batch, model=target_model)
        except LLMError as exc:
            # Keep going: a failed batch costs those rows a category, not the import.
            log.warning("Batch %d/%d failed: %s", number, len(batches), exc)
            result.failures.append(f"Batch {number}: {exc}")
            answers = {}

        for position, category in answers.items():
            out.iat[position, out.columns.get_loc(category_column)] = category
            resolved += 1

        if progress is not None:
            progress(
                number / len(batches),
                f"Categorized batch {number} of {len(batches)}.",
            )

    result.llm_hits = resolved
    result.unresolved = len(pending) - resolved
    _fill_blank(out, category_column)
    return result


def _fill_blank(df: pd.DataFrame, column: str) -> None:
    """Mark still-empty category cells as :data:`UNCATEGORIZED`, in place."""
    blank = df[column].astype(str).str.strip() == ""
    df.loc[blank, column] = UNCATEGORIZED


def _attr(df: pd.DataFrame, column: str) -> str:
    """Attribute name ``itertuples`` gives ``column`` (it renames invalid ones)."""
    position = df.columns.get_loc(column)
    name = re.sub(r"\W|^(?=\d)", "_", str(column))
    return name if name == column else f"_{position + 1}"


def _as_float(value: object) -> float:
    """Best-effort float for an amount cell."""
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return 0.0
        return float(str(value).replace("$", "").replace(",", "").strip() or 0.0)
    except (TypeError, ValueError):
        return 0.0


def learn_from_edits(
    original: pd.DataFrame,
    edited: pd.DataFrame,
    description_column: str = "Description",
    category_column: str = "Category",
    path: Path | str | None = None,
) -> list[Rule]:
    """Write a rule for every category the user changed by hand.

    Compares the frame handed to the editor with the frame that came back and
    learns one rule per differing row. Returns the rules actually written.
    """
    if original.empty or edited.empty:
        return []

    learned: list[Rule] = []
    shared = min(len(original), len(edited))
    for position in range(shared):
        before = str(original.iloc[position][category_column]).strip()
        after = str(edited.iloc[position][category_column]).strip()
        if not after or after == before:
            continue
        rule = learn_rule(
            str(edited.iloc[position][description_column]), after, path=path
        )
        if rule is not None:
            learned.append(rule)
    return learned
