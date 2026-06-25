import re

from django import template
from django.utils.safestring import mark_safe
from django.utils.html import escape


register = template.Library()


def build_highlight_regex(query):
    query = " ".join((query or "").lower().split())

    if not query:
        return ""

    token_chars = r"0-9A-Za-z가-힣"
    token_body = rf"[{token_chars}/_-]*"

    def build_token_regex(token):
        escaped_parts = [
            re.escape(part)
            for part in token.split("*")
        ]
        body = token_body.join(escaped_parts)

        if not token.startswith("*"):
            body = rf"(?<![{token_chars}])" + body

        if not token.endswith("*"):
            body = body + rf"(?![{token_chars}])"

        return body

    tokens = [
        token
        for token in query.split()
        if token.strip()
    ]

    if not tokens:
        return ""

    if "*" in query:
        return r"\s+".join(build_token_regex(token) for token in tokens)

    exact_tokens = [
        rf"(?<![{token_chars}]){re.escape(token)}(?![{token_chars}])"
        for token in tokens
    ]

    return r"\s+".join(exact_tokens)


@register.filter
def highlight_query(text, query):
    if not text or not query:
        return text

    pattern_text = build_highlight_regex(query)

    if not pattern_text:
        return escape(text)

    pattern = re.compile(pattern_text, re.IGNORECASE)
    pieces = []
    last_index = 0

    for match in pattern.finditer(text):
        pieces.append(escape(text[last_index:match.start()]))
        pieces.append(f"<mark>{escape(match.group(0))}</mark>")
        last_index = match.end()

    pieces.append(escape(text[last_index:]))

    return mark_safe("".join(pieces))
