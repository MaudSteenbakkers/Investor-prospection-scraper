"""
Text block classification and keyword/indication extraction.
Logic ported as-is from the original notebook (it works) -- only the
LLM tie-breaker call underneath is_drug_developer() has been fixed.
"""

import re

from config import (
    ALIASES,
    BIO_PHRASES,
    COMPANY_INTENT_PHRASES,
    EXCLUSION_SIGNALS,
    FOCUS_SECTION_LABELS,
    FOCUS_URL_SEGMENTS,
    KEYWORDS,
    MIN_EXCLUSION_SIGNALS,
    REJECTION_PHRASES,
    SAFETY_PHRASES,
    SKIP_SECTION_LABELS,
    WHOLE_WORD_TERMS,
)
from llm_classify import ask_claude_drug_developer


def get_text_blocks(soup):
    blocks = []
    seen = set()
    for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        txt = " ".join(el.get_text(" ", strip=True).split())
        key = txt.lower()
        if txt and key not in seen:
            seen.add(key)
            blocks.append(txt)
    return blocks


def classify_block(text_block, url):
    url_lower = url.lower()
    text_lower = text_block.lower()

    if any(seg in url_lower for seg in [
        "/team", "/about/", "/about#",
        "/leadership", "/founders", "/advisors", "/board",
        "/mission", "/our-mission", "/values", "/history",
    ]):
        return "SKIP"

    if any(p in text_lower for p in SAFETY_PHRASES):
        return "SAFETY"

    if any(p in text_lower for p in SKIP_SECTION_LABELS):
        return "SKIP"

    if sum(1 for p in BIO_PHRASES if p in text_lower) >= 2:
        return "SKIP"

    if any(seg in url_lower for seg in FOCUS_URL_SEGMENTS):
        return "FOCUS"

    if any(p in text_lower for p in FOCUS_SECTION_LABELS):
        return "FOCUS"

    return "OTHER"


def is_drug_developer(homepage_soup):
    if homepage_soup is None:
        return True, "Biotech/Pharma (assumed -- no homepage)"

    blocks = get_text_blocks(homepage_soup)
    homepage_text = " ".join(blocks).lower()

    total_signals = 0
    best_category = None
    best_score = 0

    for category, signals in EXCLUSION_SIGNALS.items():
        score = sum(1 for s in signals if s in homepage_text)
        total_signals += score
        if score > best_score:
            best_score = score
            best_category = category

    if total_signals >= MIN_EXCLUSION_SIGNALS:
        return False, f"Excluded ({best_category})"

    if total_signals == 0:
        return True, "Biotech/Pharma"

    claude_result = ask_claude_drug_developer(homepage_text)
    if claude_result is None:
        return True, f"Biotech/Pharma (assumed -- ambiguous, LLM check unavailable)"
    elif claude_result:
        return True, "Biotech/Pharma (confirmed by Claude)"
    else:
        return False, f"Excluded ({best_category}, confirmed by Claude)"


def match_term(term, text_for_comparison):
    if term in WHOLE_WORD_TERMS:
        pattern = r"\b" + re.escape(term) + r"\b"
        return list(re.finditer(pattern, text_for_comparison))
    else:
        return list(re.finditer(re.escape(term), text_for_comparison))


def get_context_status(text_block, match_start, match_end, word_window=30):
    block_lower = text_block.lower()

    if "world health organization" in block_lower or re.search(r'\bWHO\b', text_block):
        return "rejected", "WHO reference -- likely epidemiology context"

    words = text_block.split()
    char_count = 0
    start_idx = end_idx = -1
    for i, word in enumerate(words):
        if char_count <= match_start < char_count + len(word):
            start_idx = i
        if char_count <= match_end <= char_count + len(word):
            end_idx = i
            break
        char_count += len(word) + 1

    if start_idx == -1 or end_idx == -1:
        return "neutral", None

    context = " ".join(
        words[max(0, start_idx - word_window): end_idx + word_window]
    ).lower()

    for phrase in REJECTION_PHRASES:
        if phrase in context:
            return "rejected", f"Rejection phrase: '{phrase}'"

    for phrase in SAFETY_PHRASES:
        if phrase in context:
            return "rejected", f"Safety/legal context: '{phrase}'"

    for phrase in COMPANY_INTENT_PHRASES:
        if phrase in context:
            return "matched", None

    return "neutral", None


def get_snippet(text, match_start, match_end, char_window=100):
    return text[max(0, match_start - char_window): min(len(text), match_end + char_window)]


def extract_keywords(soup, company_name, url):
    """
    Returns: (found_keywords, audit_results, pipeline_image_flag)
    """
    from crawler import has_pipeline_images

    found_keywords = set()
    audit_results = []

    pipeline_image_flag = has_pipeline_images(soup, url)

    search_terms = []
    for k in KEYWORDS:
        search_terms.append((
            k.lower().replace("'", "").replace("\u2019", ""),
            k,
            "keyword",
        ))
    for alias, canonical in ALIASES.items():
        search_terms.append((
            alias.lower().replace("'", "").replace("\u2019", ""),
            canonical,
            "alias",
        ))

    for text_block in get_text_blocks(soup):
        block_category = classify_block(text_block, url)

        if block_category in ("SKIP", "SAFETY"):
            continue

        text_cmp = (
            text_block.lower()
            .replace("'", "")
            .replace("\u2019", "")
            .replace("\u2018", "")
        )

        for term_cmp, canonical_keyword, term_type in search_terms:
            for match in match_term(term_cmp, text_cmp):
                match_start, match_end = match.start(), match.end()
                snippet = get_snippet(text_block, match_start, match_end)

                status, reason = get_context_status(text_block, match_start, match_end)

                if block_category == "FOCUS":
                    accepted = status != "rejected"
                elif block_category == "OTHER":
                    accepted = status == "matched"
                else:
                    accepted = False

                if accepted:
                    found_keywords.add(canonical_keyword)

                audit_results.append({
                    "company_name": company_name,
                    "page_url": url,
                    "keyword": canonical_keyword,
                    "matched_term": text_block[match_start:match_end],
                    "term_type": term_type,
                    "block_category": block_category,
                    "match_status": "matched" if accepted else "rejected",
                    "reason": reason if reason else (
                        "OTHER block -- no intent phrase found"
                        if block_category == "OTHER" and not accepted else None
                    ),
                    "context_snippet": snippet,
                })

    return sorted(found_keywords), audit_results, pipeline_image_flag
