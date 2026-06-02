import re

from django import template
from django.utils.safestring import mark_safe
from django.utils.html import escape


register = template.Library()


@register.filter
def highlight_query(text, query):
    if not text or not query:
        return text

    escaped_text = escape(text)

    words = query.split()

    for word in words:
        pattern = re.compile(
            re.escape(word),
            re.IGNORECASE
        )

        escaped_text = pattern.sub(
            lambda match: (
                f'<mark>{match.group(0)}</mark>'
            ),
            escaped_text
        )

    return mark_safe(escaped_text)