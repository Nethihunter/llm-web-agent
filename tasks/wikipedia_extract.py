"""
Demo task: Wikipedia article extraction.

Demonstrates:
- Heavy DOM parsing (Wikipedia articles = 500KB+)
- HTML reduction (96% reduction is most visible here)
- Targeted text extraction
- No login, no form, pure navigation + extraction

Site: https://en.wikipedia.org
Legal: Wikipedia content is CC-BY-SA. Extraction of text is allowed
       with attribution. No account, no form, no ToS violation.
"""

TASK = {
    "name": "extract",
    "url": "https://en.wikipedia.org/wiki/Web_scraping",
    "goal": (
        "Read the Wikipedia article on Web scraping. Extract: "
        "1) the first paragraph (definition), "
        "2) the list of legal/ethical concerns (bullet points), "
        "3) the year the article was last edited."
    ),
    "variables": {},
    "success_hint": "Legal issues",
    "max_steps": 8,
    "extract_schema": {
        "definition": "string",
        "legal_concerns": ["string"],
        "last_edited": "string",
    },
}
