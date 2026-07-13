import re

from django import template
from django.utils.safestring import mark_safe
from django.utils.html import escape
from manuals.services import build_manual_text_regex, parse_manual_search_query


register = template.Library()


def build_highlight_regex(query):
    search_value, match_mode = parse_manual_search_query(query)
    return build_manual_text_regex(search_value, match_mode)


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
