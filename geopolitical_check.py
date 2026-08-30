"""
Checks recent global/local news for a genuinely NEW, market-moving
geopolitical escalation (not routine day-to-day coverage of an already-known
ongoing conflict) -- used as a pre-entry hold-back filter in live_alerts.py.
Withholds a fresh BUY entry only, never a SELL; a protective exit on an
existing position should still fire during a crisis, not be suppressed by
one.

Keyword matching alone can't tell "new shock" from "ongoing war, routine
update" -- both use identical words (war, attack, strike, sanctions): tested
live, a plain keyword match on global feeds returned 17 hits, almost all
routine ongoing Ukraine/Iran coverage. An LLM classification pass on the
matched headlines makes that call instead, same Claude-then-Gemini fallback
pattern as MODI7's ai_synthesis.py. If BOTH providers fail, this fails OPEN
(treats it as no escalation) rather than silently blocking all future
entries whenever the LLM APIs are down -- this is a supplementary safety
filter, not something that should be able to halt the whole system on its
own outage.
"""

import time

import anthropic
import feedparser
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel

CLAUDE_MODEL = "claude-opus-5"
GEMINI_MODEL = "gemini-3.6-flash"

# Two separate feeds rather than one broad query: a "China trade tension"
# story and an "India-Pakistan border" story are both market-moving but
# wouldn't both surface from a single generic search.
#
# Ukraine and Iran are excluded from the query itself (-ukraine -iran) --
# both are long-running, already-priced-in conflicts that dominated every
# test run of this checker with routine day-to-day coverage, at the cost of
# a wasted LLM call each time. _EXCLUDED_TERMS below is a second, belt-and-
# suspenders filter on the results in case Google's query exclusion misses
# a headline that mentions either country only in passing.
RSS_FEEDS = {
    "Global": (
        "https://news.google.com/rss/search?q=(%22declares+war%22+OR+ceasefire"
        "+OR+sanctions+OR+geopolitical+OR+%22military+strike%22+OR+invasion"
        "+OR+%22missile+attack%22+OR+%22state+of+emergency%22+OR+coup)"
        "+-ukraine+-iran+when:6h&hl=en-US&gl=US&ceid=US:en"
    ),
    "India": (
        "https://news.google.com/rss/search?q=India+(%22declares+war%22+OR"
        "+%22terror+attack%22+OR+%22border+clash%22+OR+%22security+crisis%22"
        "+OR+%22state+of+emergency%22)"
        "+-ukraine+-iran+when:6h&hl=en-IN&gl=IN&ceid=IN:en"
    ),
}

# Bare "war"/"attack"/"terror" are too broad -- they match idioms ("culture
# war"), historical retrospectives, sports/entertainment headlines, and
# unrelated local news (tested live: "Warhammer", "Vietnam War" pieces,
# "asphalt plant war" all matched a looser list). Compound, specific phrases
# only, so obvious noise doesn't burn an LLM call before it even gets there.
GEOPOLITICAL_KEYWORDS = [
    "declares war", "erupts into war", "ceasefire collapse", "ceasefire",
    "sanctions", "invasion", "military strike", "airstrike",
    "missile attack", "border clash", "terror attack", "geopolitical",
    "conflict escalation", "martial law", "coup", "state of emergency",
    "security crisis",
]

_EXCLUDED_TERMS = ["ukraine", "iran"]

# Only re-run the (paid) LLM classification this often -- headlines are
# refetched every call, but the verdict itself doesn't need re-judging on
# every 5-min live_alerts cycle if nothing's materially changed.
_VERDICT_CACHE_TTL_SECONDS = 1800
_verdict_cache = {"result": None, "at": 0}


class GeopoliticalVerdict(BaseModel):
    is_new_escalation: bool
    reasoning: str


SYSTEM_PROMPT = (
    "You are a cautious markets analyst reviewing recent news headlines for "
    "a signal used to briefly hold back new options trades. Distinguish a "
    "GENUINELY NEW, surprising geopolitical escalation likely to shock "
    "markets today (a new war breaking out, a major attack on a new "
    "country/target, a coup, a nuclear incident, a sudden ceasefire "
    "collapse, a major new sanctions regime) from ROUTINE ongoing coverage "
    "of an already-known, already-priced-in conflict (continuing Ukraine or "
    "Iran war reporting, incremental updates, analysis pieces, anniversary "
    "retrospectives). Only mark is_new_escalation true for the former."
)


def _matches_geopolitical(text):
    text_lower = text.lower()
    if any(term in text_lower for term in _EXCLUDED_TERMS):
        return False
    return any(kw in text_lower for kw in GEOPOLITICAL_KEYWORDS)


def get_recent_geopolitical_headlines():
    """Returns a list of (source, title) for matched items -- freshness
    (~6h) is enforced by the feed query itself via when:6h. Ukraine/Iran
    headlines are excluded (see RSS_FEEDS comment)."""
    matches = []
    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
        except Exception:
            continue
        for entry in feed.entries[:15]:
            title = entry.get("title", "")
            if _matches_geopolitical(title):
                matches.append((source, title))
    return matches


def _call_claude(prompt):
    try:
        client = anthropic.Anthropic()
        response = client.messages.parse(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_format=GeopoliticalVerdict,
        )
        return response.parsed_output, None
    except Exception as e:
        return None, str(e)


def _call_gemini(prompt):
    try:
        client = genai.Client()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=GeopoliticalVerdict,
            ),
        )
        if response.parsed is None:
            return None, "Gemini returned a response that didn't match the expected schema."
        return response.parsed, None
    except Exception as e:
        return None, str(e)


def has_breaking_geopolitical_event(use_cache=True):
    """
    Returns (is_new_escalation: bool, detail: str). Fails OPEN (returns
    False, ...) if no keyword-matched headlines are found, or BOTH LLM
    providers fail to classify -- this is a supplementary filter, not
    something that should be able to halt all trading on its own outage.
    """
    cached = _verdict_cache["result"]
    if use_cache and cached and (time.time() - _verdict_cache["at"]) < _VERDICT_CACHE_TTL_SECONDS:
        return cached

    headlines = get_recent_geopolitical_headlines()
    if not headlines:
        result = (False, "No geopolitical-keyword headlines found.")
        _verdict_cache["result"] = result
        _verdict_cache["at"] = time.time()
        return result

    headline_lines = "\n".join(f"- [{source}] {title}" for source, title in headlines[:20])
    prompt = (
        f"Recent headlines matching geopolitical/conflict keywords:\n{headline_lines}\n\n"
        "Is there a genuinely NEW escalation here (not routine ongoing-conflict "
        "coverage)? Give your verdict and a one-sentence reason."
    )

    verdict, claude_error = _call_claude(prompt)
    if verdict is None:
        verdict, gemini_error = _call_gemini(prompt)
        if verdict is None:
            result = (
                False,
                f"LLM classification unavailable (Claude: {claude_error}; "
                f"Gemini: {gemini_error}) -- failing open.",
            )
            _verdict_cache["result"] = result
            _verdict_cache["at"] = time.time()
            return result

    result = (verdict.is_new_escalation, verdict.reasoning)
    _verdict_cache["result"] = result
    _verdict_cache["at"] = time.time()
    return result


if __name__ == "__main__":
    is_escalation, detail = has_breaking_geopolitical_event(use_cache=False)
    print(f"New escalation: {is_escalation}")
    print(f"Detail: {detail}")
