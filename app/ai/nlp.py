"""
Question understanding for "Ask your data".

The AI layer already has a hard guardrail (blueprint section 9): the model is
never the source of truth for arithmetic. That rule is what makes natural
language safe to accept here - a question is only ever *understood*, never
*guessed at*. This module turns free text into a structured intent:

    "will sales increase in the next 10 days"
        -> intent=FORECAST, metric="revenue", horizon=10 days

Every value in the resulting answer is then computed by the deterministic
analytics engine, so an unparseable or unsupported question degrades to a
"here is what I can answer" message instead of an invented number.

No LLM, no network, no new dependency: the parsing is plain regex + token
overlap, so it works with zero API keys and gives the same answer every time.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Intent(str, Enum):
    FORECAST = "forecast"
    TREND = "trend"
    AGGREGATE = "aggregate"
    COMPARE = "compare"
    TOP_BOTTOM = "top_bottom"
    CORRELATE = "correlate"
    ANOMALY = "anomaly"
    DESCRIBE = "describe"
    COUNT = "count"
    UNKNOWN = "unknown"


class Aggregate(str, Enum):
    SUM = "sum"
    AVERAGE = "average"
    MAX = "max"
    MIN = "min"
    COUNT = "count"


@dataclass
class ParsedQuestion:
    """The structured reading of a user's question."""

    intent: Intent = Intent.UNKNOWN
    metric: Optional[str] = None
    group_by: Optional[str] = None
    aggregate: Aggregate = Aggregate.SUM
    horizon: Optional[int] = None
    horizon_unit: str = "days"
    top_n: Optional[int] = None
    direction: Optional[str] = None  # "increase" | "decrease" | None
    matched_terms: list[str] = field(default_factory=list)
    original: str = ""

    @property
    def is_actionable(self) -> bool:
        """True when we understood enough to compute a real answer."""
        return self.intent != Intent.UNKNOWN and bool(self.metric)


# ── Vocabulary ────────────────────────────────────────────────────────────────

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "do",
    "does", "did", "have", "has", "had", "of", "in", "on", "at", "to", "for",
    "from", "by", "with", "about", "and", "or", "if", "then", "than", "as",
    "it", "its", "this", "that", "these", "those", "there", "here", "i", "me",
    "my", "we", "our", "you", "your", "he", "she", "they", "them", "their",
    "am", "will", "would", "can", "could", "should", "shall", "may", "might",
    "must", "not", "no", "so", "up", "out", "over", "please", "show", "tell",
    "give", "get", "know", "want", "need", "like", "any", "some", "much",
    "many", "next", "going", "go", "come", "let", "whats", "what", "which",
}

# Business words that mean the same measurement. Lets "will sales increase"
# resolve to whichever revenue-ish column the user's file actually has.
SYNONYMS: dict[str, set[str]] = {
    "sales": {"sale", "sales", "revenue", "income", "turnover", "earnings",
              "gross", "booking", "bookings", "gmv", "billed"},
    "profit": {"profit", "margin", "net", "bottomline"},
    "cost": {"cost", "costs", "expense", "expenses", "spend", "spending",
             "budget", "burn"},
    "price": {"price", "pricing", "rate", "amount", "value", "fee"},
    "users": {"user", "users", "customer", "customers", "client", "clients",
              "visitor", "visitors", "member", "members", "account", "accounts",
              "subscriber", "subscribers", "signup", "signups"},
    "orders": {"order", "orders", "transaction", "transactions", "purchase",
               "purchases", "checkout", "cart", "basket", "order_id"},
    "quantity": {"quantity", "qty", "units", "count", "volume", "items",
                 "item", "sold", "shipped"},
    "traffic": {"traffic", "visit", "visits", "view", "views", "impression",
                "impressions", "click", "clicks", "session", "sessions"},
    "conversion": {"conversion", "conversions", "convert", "conversion_rate"},
    "churn": {"churn", "churned", "cancel", "cancels", "cancellation",
              "attrition", "dropped", "lost"},
    "retention": {"retention", "retained", "loyalty", "repeat"},
    "engagement": {"engagement", "engaged", "active", "activity", "dau", "mau"},
    "revenue_growth": {"growth", "growing", "increase", "rising", "rise",
                       "up", "decrease", "decline", "declining", "fall", "drop"},
    "product": {"product", "products", "sku", "item", "article", "product_name"},
    "region": {"region", "country", "location", "city", "state", "market",
               "territory", "zone", "area"},
    "channel": {"channel", "source", "medium", "platform", "referrer"},
    "time": {"date", "day", "time", "timestamp", "month", "year", "week",
             "created", "created_at", "updated", "weekday", "hour"},
}

# Flattened lookup: token -> set of meaning-groups it belongs to.
_TOKEN_GROUPS: dict[str, set[str]] = {}
for _group, _words in SYNONYMS.items():
    for _word in _words:
        _TOKEN_GROUPS.setdefault(_word, set()).add(_group)


# ── Intent detection ──────────────────────────────────────────────────────────

_INTENT_PATTERNS: list[tuple[Intent, re.Pattern]] = [
    (Intent.FORECAST, re.compile(
        r"\b(will|going to|forecast|predict|projection|projected|expect|"
        r"estimated?|estimate|next|future|upcoming|upcoming|coming)\b"
        r"|\b(in the next|over the next)\b", re.I)),
    (Intent.TOP_BOTTOM, re.compile(
        r"\b(top|bottom|best|worst|highest|lowest|most|least|leading|"
        r"biggest|smallest)\b", re.I)),
    (Intent.AGGREGATE, re.compile(
        r"\b(total|sum|summed|average|avg|mean|maximum|minimum|median|"
        r"how much|overall|altogether|combined|worth)\b", re.I)),
    (Intent.COMPARE, re.compile(
        r"\b(compare|comparison|versus|vs\.?|against|breakdown|by|"
        r"split|across|per|difference)\b", re.I)),
    (Intent.CORRELATE, re.compile(
        r"\b(correlat\w*|relationship|related|relate|depend\w*|"
        r"associated|influence\w*|drives?|affects?)\b", re.I)),
    (Intent.ANOMALY, re.compile(
        r"\b(anomal\w*|outlier\w*|unusual|weird|odd|anomalies|"
        r"missing|null|empty|invalid|error\w*|problem\w*|issue\w*|"
        r"quality|inconsisten\w*)\b", re.I)),
    (Intent.DESCRIBE, re.compile(
        r"\b(column\w*|col\w*|field\w*|header\w*|schema|structure|"
        r"what kind|what type|describe|explain|about the data|overview|"
        r"summar\w*)\b", re.I)),
    (Intent.COUNT, re.compile(
        r"\b(how many|how much|number of|count of|row count|record count|"
        r"size of|how big)\b", re.I)),
    (Intent.TREND, re.compile(
        r"\b(trend\w*|over time|timeline|historical|history|grew|grow|"
        r"change|chang\w*|evolv\w*|progres\w*|pattern|seasonal\w*)\b", re.I)),
]

_AGG_PATTERNS: list[tuple[Aggregate, re.Pattern]] = [
    (Aggregate.AVERAGE, re.compile(r"\b(average|avg|mean|typical|per\s+\w+\s+on\s+average)\b", re.I)),
    (Aggregate.MAX, re.compile(r"\b(max\w*|maximum|highest|largest|biggest|peak|best|top)\b", re.I)),
    (Aggregate.MIN, re.compile(r"\b(min\w*|minimum|lowest|smallest|least|worst)\b", re.I)),
    (Aggregate.COUNT, re.compile(r"\b(count|number of|how many|records|rows|entries)\b", re.I)),
    (Aggregate.SUM, re.compile(r"\b(total|sum|summed|overall|altogether|combined|revenue)\b", re.I)),
]

_HORIZON_RE = re.compile(
    r"\b(?:next|coming|upcoming|in|over|within)\s+(\d{1,4})\s*"
    r"(day|week|month|hour|quarter|year|period)s?\b", re.I)
_TOP_N_RE = re.compile(r"\b(?:top|bottom|best|worst|highest|lowest)\s+(\d{1,3})\b", re.I)
_DIRECTION_RE = re.compile(
    r"\b(increase\w*|rise|rising|rise|grow\w*|grow|up|higher|climb\w*|"
    r"drop\w*|drop|fall\w*|fall|falling|decrease\w*|decline\w*|decline|"
    r"down|lower|shrink\w*|reduc\w*)\b", re.I)

# The join word in "revenue by region" / "units per channel". Longest forms
# first so "grouped by" is consumed whole rather than splitting on "by".
_BY_JOIN_RE = re.compile(
    r"\b(grouped?\s+by|split\s+by|broken\s+down\s+by|for\s+each|versus|vs\.?|"
    r"by|per|across)\b", re.I)

_UNIT_ALIASES = {"hour": "hours", "day": "days", "week": "weeks",
                 "month": "months", "quarter": "quarters", "year": "years",
                 "period": "periods"}


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


def _detect_direction(text: str) -> Optional[str]:
    """Only an *increase* wording is meaningful for a forecast. A question
    like 'will sales fall' is answered by the same forecast; we just record
    the user's framing so the reply can echo it."""
    match = _DIRECTION_RE.search(text)
    if not match:
        return None
    word = match.group(0).lower()
    if word.startswith(("in", "rise", "ris", "grow", "up", "high", "climb")):
        return "increase"
    return "decrease"


def _detect_aggregate(text: str) -> Aggregate:
    for agg, pattern in _AGG_PATTERNS:
        if pattern.search(text):
            return agg
    return Aggregate.SUM


def _detect_horizon(text: str) -> tuple[Optional[int], str]:
    match = _HORIZON_RE.search(text)
    if not match:
        return None, "days"
    return int(match.group(1)), match.group(2).lower() + "s"


def _column_terms(column: str) -> set[str]:
    parts = re.split(r"[_\s\-./]+", column.lower())
    return {p for p in parts if p and p not in STOPWORDS}


def _score_column(question_tokens: set[str], question_groups: set[str], column: str) -> float:
    """Rank a candidate column against the question. Returns 0.0 for no match."""
    col_lower = column.lower()
    col_terms = _column_terms(column)

    # Verbatim appearance of the full column name is the strongest signal.
    score = 0.0
    if col_lower in " ".join(sorted(question_tokens)):
        score += 5.0
    if col_terms & question_tokens:
        score += 3.0 * len(col_terms & question_tokens)
    # A column whose name means the same thing as a word the user typed.
    for term in col_terms:
        if term in _TOKEN_GROUPS and (_TOKEN_GROUPS[term] & question_groups):
            score += 2.0
    return score


def _match_column(
    question_text: str,
    columns: list[str],
    tokens: set[str],
    groups: set[str],
    exclude: Optional[str] = None,
) -> Optional[str]:
    """Best matching column name, or None. 'exclude' skips a column already
    claimed by another slot so metric and group_by never collide."""
    best, best_score = None, 0.0
    for column in columns:
        if exclude and column == exclude:
            continue
        score = _score_column(tokens, groups, column)
        if score > best_score:
            best, best_score = column, score
    return best


def _analyze(text: str) -> tuple[set[str], set[str]]:
    """Content tokens and synonym groups for a span of text."""
    tokens = {t for t in _tokenize(text) if t not in STOPWORDS}
    groups: set[str] = set()
    for token in tokens:
        groups |= _TOKEN_GROUPS.get(token, set())
    return tokens, groups


def parse_question(question: str, columns: Optional[list[str]] = None) -> ParsedQuestion:
    """Turn free text into a ParsedQuestion. Never raises: an unreadable
    question comes back as Intent.UNKNOWN with no metric."""
    question = (question or "").strip()
    parsed = ParsedQuestion(original=question)
    if not question:
        return parsed

    columns = [str(c) for c in (columns or []) if c]
    raw_tokens = _tokenize(question)
    content_tokens = {t for t in raw_tokens if t not in STOPWORDS}
    question_groups: set[str] = set()
    for token in content_tokens:
        question_groups |= _TOKEN_GROUPS.get(token, set())

    # Intent: first matching pattern wins, so "will sales increase in the next
    # 10 days" is a FORECAST rather than being swallowed by TREND.
    for intent, pattern in _INTENT_PATTERNS:
        if pattern.search(question):
            parsed.intent = intent
            break

    parsed.aggregate = _detect_aggregate(question)
    parsed.horizon, parsed.horizon_unit = _detect_horizon(question)
    parsed.direction = _detect_direction(question)

    top_match = _TOP_N_RE.search(question)
    if top_match:
        parsed.top_n = int(top_match.group(1))
        parsed.intent = Intent.TOP_BOTTOM

    # A "will X happen" question is a forecast even if the trigger word is
    # missing, as long as a horizon was actually named.
    if parsed.horizon and parsed.intent in (Intent.UNKNOWN, Intent.TREND, Intent.AGGREGATE):
        parsed.intent = Intent.FORECAST

    if columns:
        # "revenue by region" names two different columns: the measure on the
        # left of the "by" and the grouping on the right. Matching the whole
        # string scores both equally and picks whichever won, which is how
        # "revenue by region" ends up asking to break Revenue down by Region's
        # position in the sentence. Split at the join instead.
        split = _BY_JOIN_RE.search(question)
        if split:
            left_tokens, left_groups = _analyze(question[: split.start()])
            right_tokens, right_groups = _analyze(question[split.end():])
            parsed.metric = _match_column(question[: split.start()], columns, left_tokens, left_groups)
            parsed.group_by = _match_column(
                question[split.end():], columns, right_tokens, right_groups, exclude=parsed.metric
            )
        else:
            parsed.metric = _match_column(question, columns, content_tokens, question_groups)

        # "compare price and quantity" names no join word; fall back to the
        # best remaining column rather than repeating the metric as its own
        # group-by (which produced the nonsense "break Region down by Revenue").
        if parsed.intent == Intent.COMPARE and not parsed.group_by:
            other = _match_column(
                question, columns, content_tokens, question_groups, exclude=parsed.metric
            )
            if other:
                parsed.group_by = other

        # "how many rows" wants the dataset size, but "how many units" wants a
        # count of one column. Only the first is a dataset-level COUNT, so a
        # COUNT that resolved to a real column becomes an AGGREGATE.
        if parsed.intent == Intent.COUNT and parsed.metric:
            parsed.intent = Intent.AGGREGATE
            parsed.aggregate = Aggregate.COUNT

    parsed.matched_terms = sorted(content_tokens)
    return parsed
