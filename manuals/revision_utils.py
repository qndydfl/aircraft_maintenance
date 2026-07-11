import os
import re


def extract_revision_info_from_filename(source_file_name):
    file_name = os.path.basename(source_file_name or "")
    stem = os.path.splitext(file_name)[0]

    if not stem:
        return "", ""

    match = re.search(
        r"(?P<rev>R\d{1,3}(?:(?:\s+|[_-]+)TR\d{1,3})?)\s*[_-]\s*(?P<date>\d{1,2}(?:[A-Za-z]{3}|[\s_-]+[A-Za-z]{3,9}[\s_-]+)\d{4})(?:\b|(?=[\s_-]))",
        stem,
        re.IGNORECASE,
    )

    if not match:
        return "", ""

    revision_no = re.sub(r"[\s_-]+", " ", match.group("rev").upper()).strip()
    revision_date = re.sub(r"[\s_-]+", "", match.group("date").upper()).strip()

    return revision_no, revision_date
