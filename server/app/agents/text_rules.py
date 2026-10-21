"""Deterministic text rules for model output (no LLM, no network).

These exist because the models write fast and glue words together
("todiscuss", "daysoverdue") or leave their own self-corrections in the final
text ("... 33,045? (no)"). Both are pure text problems, so code checks them
instead of trusting the model.

The glued-word test is dictionary based: a token is glued when it is NOT a
known word but splits cleanly into 2-3 known words. Three-letter pieces are
enough on their own; a two-letter glue word ('to', 'of', 'in') is only
accepted when the rest of the token is a known word of at least five letters,
so 'today' or 'online' never split. Proper nouns are safe because the pipeline
ignores words that appear in the item's own input payload.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# Common English words (lowercase). Only used to recognise pieces of a glued
# token, so plain single words are never flagged by being in here.
_WORDS = """
a about above across after again against all almost alone along already also
although always among an and another any anyone anything around as at away
back be because been before begin behind being below best better between both
bring but by call called calling can cannot come could day days deal decide
did do does done down during each either else end even ever every everything
except few find first for from further get give go good got great had has
have he her here hers him his how however i if in inside instead into is it
its just keep kept know known last late later least let like list look made
make making many may me might more most much must my near need never new next
no none nor not nothing now number of off often once one only onto or other
our out over own part perhaps put rather really receive recent regarding
same see seems set several shall she should since so some something still
such take than that the their them then there these they thing this those
though through thus to today too took toward turn two under until up upon us
use used very want was way we well went were what when where whether which
while who whom why will with within without would year yes yet you your
""".split()

# Business / printing vocabulary the agents actually write with. Missing a
# real word here would make it look glued, so this list is the safety net.
_DOMAIN_WORDS = """
adjustment advance advice artwork arrive attachment available average balance
bank bill boxed brand briefing budget bundle business cash cheque colour
client collect confirmed consist contact contract cost courier current
customer deadline deliver deposit design discount discussion dl due estimate
evidence escalation existing explain follow freight fulfillment glance
glued hold invoice issue late lateness lead listing manager material monica
negotiation order overdue payable payment pouch produce production proof
provide purchase quantity quote receipt record refund reminder rep report
request reprint response refund rework sale saved screen seal send service
shipment ship sole spec spend statement status stock stuck supplier terms
timeline transfer update upload verify vial wait walk workflow write
whenever whoever whatever wherever whichever somehow somewhat sometime
sometimes everywhere elsewhere moreover furthermore otherwise meanwhile
afternoon everyday notebook paperwork framework hardware software network
deadline feedback turnaround backup download upfront standpoint
accept advise agree arrange ask attach confirm copy cover create deliver
discuss discussed discussing discussion email expect follow forward handle
include issue notify offer order payment phone price produce provide quote
receive record review schedule send share sign update verify work today
""".split()

KNOWN_WORDS = frozenset(
    {w.strip(".").lower() for w in _WORDS} | {w.lower() for w in _DOMAIN_WORDS}
)

# Words that legitimately contain other words. Kept out of the glued-word
# check even when every piece is a known word.
NEVER_GLUED = frozenset(
    {
        "afternoon", "altogether", "anyone", "anything", "alongside", "although",
        "androgen", "anybody", "artwork", "background", "beforehand", "everyday",
        "everything", "foreclosure", "notebook", "nobody", "overall", "overflow",
        "paperwork", "perhaps", "someone", "something", "sometime", "standby",
        "therefore", "throughout", "whereas", "wherefore", "workflow",
        "writeup", "yourself",
    }
)

_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_TOKEN_SPLIT_MIN = 3  # each piece needs this many letters

# Short words the models glue in front of a real word ("todiscuss"). A 2-letter
# piece is only accepted when the rest of the token is a known word of at least
# 5 letters, and the words that really are one word are listed in NEVER_GLUED.
SHORT_GLUE_PREFIXES = frozenset({"to", "of", "in", "on", "at", "by", "as", "is"})
_SHORT_GLUE_REST_MIN = 5

NEVER_GLUED |= frozenset(
    {
        "ashley", "assemble", "assembly", "assign", "assure", "attache", "attempt",
        "attack", "attorney", "authority", "byline", "byproduct", "bystander",
        "inbound", "indeed", "indirect", "inform", "inhouse", "insight", "insist",
        "install", "instance", "instant", "insecure", "inspect", "inspire", "instance",
        "island", "kernel", "online", "onto", "opener", "outfit", "outset", "output",
        "outreach", "overall", "overdue", "overhead", "overlay", "oversight", "overtake",
        "owner", "token", "together", "tomorrow", "tonight",
    }
)


def _segmentations(token: str, max_parts: int = 3) -> Optional[list[str]]:
    """Split a token into 2-3 known words (fewest pieces), else None.

    The whole token can never be one piece: a known word is not glued.
    """
    lowered = token.lower()
    length = len(lowered)
    spans: list[Optional[list[str]]] = [None] * (length + 1)
    spans[0] = []
    for end in range(1, length + 1):
        for start in range(0, end):
            prefix = spans[start]
            if prefix is None or len(prefix) >= max_parts:
                continue
            piece = lowered[start:end]
            if len(piece) < _TOKEN_SPLIT_MIN or piece not in KNOWN_WORDS:
                continue
            if start == 0 and end == length:
                continue
            candidate = prefix + [piece]
            if spans[end] is None or len(candidate) < len(spans[end]):
                spans[end] = candidate
    result = spans[length]
    return result if result and len(result) > 1 else None


def _glued_prefix(lowered: str) -> Optional[list[str]]:
    """'todiscuss' -> ['to', 'discuss'] for the short glue words only.

    The rest must be (almost) a whole known word: only a trailing plural 's'
    may be dropped, so 'online' or 'income' never split.
    """
    for prefix in sorted(SHORT_GLUE_PREFIXES, key=len, reverse=True):
        if not lowered.startswith(prefix):
            continue
        rest = lowered[len(prefix):]
        for candidate in (rest, rest[:-1] if rest.endswith("s") else ""):
            if len(candidate) >= _SHORT_GLUE_REST_MIN and candidate in KNOWN_WORDS:
                return [prefix, candidate]
    return None


def split_glued(token: str) -> Optional[list[str]]:
    """Pieces of a glued token, or None when the token is not glued."""
    lowered = token.lower()
    if lowered in NEVER_GLUED or lowered in KNOWN_WORDS or len(lowered) < 8:
        return None
    return _segmentations(lowered) or _glued_prefix(lowered)


def glued_words(text: str, payload: Optional[dict] = None, payload_blob: str = "") -> list[str]:
    """Tokens that are two or more known words written without a space.

    Words copied from the input (company names, refs) are ignored: they are
    data, not typos.
    """
    if not text:
        return []
    blob = payload_blob or (str(payload) if payload else "")
    blob_lower = blob.lower()
    found: list[str] = []
    for match in _WORD_RE.finditer(text):
        token = match.group(0)
        lowered = token.lower()
        if lowered in blob_lower:
            continue  # verbatim input data
        if split_glued(lowered) and token not in found:
            found.append(token)
    return found


def repair_glued_words(text: str, payload: Optional[dict] = None, payload_blob: str = "") -> str:
    """Insert the missing spaces the model dropped ('todiscuss' -> 'to discuss')."""
    if not text:
        return text
    blob_lower = (payload_blob or (str(payload) if payload else "")).lower()

    def replace(match: "re.Match") -> str:
        token = match.group(0)
        lowered = token.lower()
        if lowered in blob_lower:
            return token
        pieces = split_glued(lowered)
        if not pieces:
            return token
        fixed = " ".join(pieces)
        return fixed[0].upper() + fixed[1:] if token[0].isupper() else fixed

    return _WORD_RE.sub(replace, text)


# ---------------------------------------------------------------------------
# leftover reasoning / self-correction
# ---------------------------------------------------------------------------

_ARTIFACT_RE = re.compile(
    r"""(?ix)
      \?\s*\(\s*no\s*\)            # '... 33,045? (no)' - the model arguing with itself
    | \(\s*no\s*\)                 # a bare '(no)' aside
    | \b(?:wait|actually|hmm|oops|sorry|correction|reconsidering)\b
    | \b(?:or\s+maybe|never\s+mind|i\s+mean|on\s+second\s+thought|let\s+me\s+(?:re-?)?think)\b
    | \b(?:ignore\s+(?:that|the\s+previous)|scratch\s+that|edit:)\b
    """,
)

# the brief is a set of statements - a question mark means the model was still
# arguing with itself when it wrote the bullet
_QUESTION_RE = re.compile(r"[?？]")
_UNMATCHED_PAREN_RE = re.compile(r"\([^()]*$|^[^()]*\)", re.M)


def reasoning_artifacts(text: str) -> list[str]:
    """Self-corrections / thinking leftovers in one line of model text."""
    if not text:
        return []
    found = [match.group(0).strip() for match in _ARTIFACT_RE.finditer(text)]
    if _QUESTION_RE.search(text):
        found.append("?")
    opens, closes = text.count("("), text.count(")")
    if opens != closes or _UNMATCHED_PAREN_RE.search(text):
        found.append("unbalanced parentheses")
    return [item for item in dict.fromkeys(found) if item]


def artifacts_in_texts(texts: Iterable[str]) -> list[str]:
    """Reasoning artifacts across a list of strings, as 'text: artifact'."""
    problems: list[str] = []
    for text in texts:
        for artifact in reasoning_artifacts(str(text or "")):
            problems.append(f"{str(text)[:60]!r} has leftover reasoning: {artifact}")
    return problems


def prompt_echo(text: str, system_prompt: str, shingle_words: int = 6) -> list[str]:
    """Verbatim sentences copied out of the system prompt.

    A model that ran out of ideas repeats its own instructions ('If every
    customer appears only once, state that explicitly'). Six consecutive words
    of prose never line up by accident.
    """
    if not text or not system_prompt:
        return []
    words = re.findall(r"[A-Za-z']+", system_prompt.lower())
    haystack = " ".join(re.findall(r"[A-Za-z']+", text.lower()))
    found: list[str] = []
    for start in range(0, max(0, len(words) - shingle_words + 1)):
        shingle = " ".join(words[start : start + shingle_words])
        if shingle in haystack:
            found.append(shingle)
            break
    return found


def number_tokens(text: str) -> list[str]:
    """Digit runs (with optional thousands commas) in model text."""
    return re.findall(r"\d[\d,]*", text or "")


def _as_number(token: str) -> float:
    return float(token.replace(",", ""))


def invented_numbers(texts: Iterable[str], allowed: set[float]) -> list[float]:
    """Numbers of 3+ digits in the texts that exist nowhere in `allowed`.

    Years and date parts (1900-2199) are always allowed, so ISO dates and
    invoice refs like '33349-2026-10' do not trip the check.
    """
    bad: list[float] = []
    for text in texts:
        for token in number_tokens(str(text or "")):
            value = _as_number(token)
            if value < 100:
                continue
            if 1900 <= value <= 2199 and len(token) == 4:
                continue
            if not any(abs(value - known) < 0.005 for known in allowed):
                if value not in bad:
                    bad.append(value)
    return bad
