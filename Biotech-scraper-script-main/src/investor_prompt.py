INVESTOR_EXTRACTION_PROMPT = """
You are analysing an investor website.

Extract the following information.

Return ONLY valid JSON.

{
  "main_sector": "",
  "therapeutic_areas": "",
  "investment_stage": "",
  "regions": "",
  "investment_thesis": "",
  "min_ticket_size": "",
  "max_ticket_size": "",
  "additional_notes": "",
  "evidence_quote": "",
  "confidence": ""
}

Rules:

- additional_notes should contain useful investor information that does not fit elsewhere.
- confidence should be High, Medium or Low.
- evidence_quote should contain an actual quote from the website supporting the extraction.
- if information is unavailable return empty string.
"""
