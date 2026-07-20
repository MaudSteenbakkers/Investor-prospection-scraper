"""
LLM tie-breaker for ambiguous company-type classification.

FIX (July 2026): the original notebook's version of this call had no
x-api-key or anthropic-version header at all, so every call failed
silently and fell back to "assume it's a drug developer". This version
adds the required headers and reads the key from the environment
(ANTHROPIC_API_KEY), never hardcoded.

Uses Haiku, not Sonnet -- this is a trivial yes/no classification on a
short homepage excerpt, so the cheaper/faster model is the right fit.
Rough cost: ~$0.001 per call. This only runs for companies with exactly
one ambiguous exclusion signal (see MIN_EXCLUSION_SIGNALS in config.py),
so it's a small fraction of a full run, not every company.
"""

import os

import requests

from config import EXCLUSION_CHECK_PROMPT

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODEL = "claude-haiku-4-5-20251001"


def ask_claude_drug_developer(homepage_text, max_chars=3000):
    """
    Returns True if Claude judges the company a drug developer (Biotech/Pharma),
    False if it judges them a service/tools/academic org, or None if the
    check could not be performed at all (e.g. no API key set) -- callers
    should treat None as "not actually checked", not as a real "yes".
    Fails safe (returns True, i.e. "don't exclude") on API errors, same as
    the original notebook's behavior -- so a transient failure degrades
    gracefully instead of silently mis-excluding real biotechs.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("    \u26a0 ANTHROPIC_API_KEY not set -- skipping LLM tie-breaker, defaulting to include")
        return None

    truncated = homepage_text[:max_chars]
    prompt = EXCLUSION_CHECK_PROMPT.format(homepage_text=truncated)

    try:
        response = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            },
            json={
                "model": MODEL,
                "max_tokens": 5,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        answer = data["content"][0]["text"].strip().lower()
        return answer.startswith("yes")
    except Exception as e:
        print(f"    \u26a0 Claude classification failed: {e}")
        return True
