import re, fitz, pdfplumber
from django.db.models import Q

from .models import DispatchKeywordDictionary, MelDispatchItem
from manuals.models import ManualPDFPage, ManualFilePDFPage


def _extract_revision_info_from_text(text):
    revision_no = ""
    revision_date_text = ""

    if not text:
        return revision_no, revision_date_text

    rev_match = re.search(
        r"Rev\.?(?:\s*No\.?)?\s*:?([0-9A-Za-z\-]+)",
        text,
        re.IGNORECASE,
    )

    date_match = re.search(
        r"Date\s*:?([0-9]{1,2}\s+[A-Za-z]{3,9}\s+[0-9]{4})",
        text,
        re.IGNORECASE,
    )

    if rev_match:
        revision_no = rev_match.group(1).strip()

    if date_match:
        revision_date_text = date_match.group(1).strip()

    return revision_no, revision_date_text


def extract_manual_file_revision_info(manual_file):
    revision_no = manual_file.revision_no or ""
    revision_date_text = manual_file.revision_date_text or ""

    if revision_no or revision_date_text:
        return revision_no, revision_date_text

    first_page = manual_file.pdf_pages.filter(page_number=1).only("text").first()

    if first_page and first_page.text:
        revision_no, revision_date_text = _extract_revision_info_from_text(
            first_page.text
        )

    if not revision_no and not revision_date_text and manual_file.file:
        try:
            with pdfplumber.open(manual_file.file.path) as pdf:
                if pdf.pages:
                    revision_no, revision_date_text = _extract_revision_info_from_text(
                        pdf.pages[0].extract_text() or ""
                    )
        except Exception:
            return revision_no, revision_date_text

    return revision_no, revision_date_text


def _extract_revision_info_from_text(text):
    revision_no = ""
    revision_date_text = ""

    if not text:
        return revision_no, revision_date_text

    rev_match = re.search(
        r"Rev\.?(?:\s*No\.?)?\s*:?[\s]*([0-9A-Za-z\-]+)",
        text,
        re.IGNORECASE,
    )

    date_match = re.search(
        r"Date\s*:?[\s]*([0-9]{1,2}\s+[A-Za-z]{3,9}\s+[0-9]{4})",
        text,
        re.IGNORECASE,
    )

    if rev_match:
        revision_no = rev_match.group(1).strip()

    if date_match:
        revision_date_text = date_match.group(1).strip()

    return revision_no, revision_date_text


def extract_manual_file_revision_info(manual_file):
    revision_no = manual_file.revision_no or ""
    revision_date_text = manual_file.revision_date_text or ""

    if revision_no or revision_date_text:
        return revision_no, revision_date_text

    first_page = manual_file.pdf_pages.filter(page_number=1).only("text").first()

    if first_page and first_page.text:
        revision_no, revision_date_text = _extract_revision_info_from_text(
            first_page.text
        )

    if not revision_no and not revision_date_text and manual_file.file:
        try:
            with pdfplumber.open(manual_file.file.path) as pdf:
                if pdf.pages:
                    revision_no, revision_date_text = _extract_revision_info_from_text(
                        pdf.pages[0].extract_text() or ""
                    )
        except Exception:
            return revision_no, revision_date_text

    return revision_no, revision_date_text


def parse_search_query(query):
    if not query:
        return "", "exact"

    trimmed = query.strip()

    if trimmed.startswith("*") and trimmed.endswith("*") and len(trimmed) > 2:
        return trimmed[1:-1].strip(), "contains"

    if trimmed.endswith("*") and len(trimmed) > 1:
        return trimmed[:-1].strip(), "startswith"

    return trimmed, "exact"


def parse_dispatch_search_query(query):
    if not query:
        return {"query": "", "mode": "exact", "keywords": []}

    trimmed = query.strip()

    if trimmed.startswith('"') and trimmed.endswith('"') and len(trimmed) > 2:
        return {
            "query": trimmed[1:-1].strip(),
            "mode": "exact_phrase",
            "keywords": [trimmed[1:-1].strip()],
        }

    if trimmed.startswith("*") and trimmed.endswith("*") and len(trimmed) > 2:
        keywords = [word.strip() for word in trimmed[1:-1].split() if word.strip()]
        return {
            "query": trimmed[1:-1].strip(),
            "mode": "contains_words",
            "keywords": keywords,
        }

    if trimmed.endswith("*") and len(trimmed) > 1:
        return {
            "query": trimmed[:-1].strip(),
            "mode": "startswith",
            "keywords": [trimmed[:-1].strip()],
        }

    return {"query": trimmed, "mode": "exact", "keywords": [trimmed]}


def expand_dispatch_query(query):
    if not query:
        return []

    parsed_query, mode = parse_search_query(query)

    if mode == "exact":
        base_words = [parsed_query.upper()]
    else:
        base_words = [word.upper() for word in parsed_query.split()]

    expanded = set(base_words)

    dictionaries = DispatchKeywordDictionary.objects.all()

    for dictionary in dictionaries:
        keyword = dictionary.keyword.upper()

        if keyword in parsed_query.upper():
            expanded.add(keyword)

            for related in dictionary.get_related_list():
                expanded.add(related.upper())

    return list(expanded)


def calculate_recommendation_score(text, keywords):
    if not text:
        return 0

    score = 0
    text_upper = text.upper()

    for keyword in keywords:
        keyword_upper = keyword.upper()

        if keyword_upper in text_upper:
            score += 20

            count = text_upper.count(keyword_upper)

            if count > 1:
                score += count * 5

    return score


def text_matches_query(text, parsed):
    if not text:
        return False

    text_upper = text.upper()
    query_upper = parsed["query"].upper()

    if parsed["mode"] == "exact_phrase":
        return query_upper in text_upper

    if parsed["mode"] == "contains_words":
        return all(word.upper() in text_upper for word in parsed["keywords"])

    return False


def recommend_package_pages(aircraft, manual_type, query, limit=10):
    parsed = parse_dispatch_search_query(query)

    queryset = ManualPDFPage.objects.select_related(
        "chapter",
        "chapter__package",
        "chapter__package__aircraft",
    ).filter(
        chapter__package__aircraft=aircraft,
        chapter__package__manual_type=manual_type,
    )

    results = []

    for page in queryset[:1000]:
        combined_text = " ".join(
            [
                page.chapter.task or "",
                page.chapter.subtask or "",
                page.chapter.title or "",
                page.text or "",
            ]
        )

        if not text_matches_query(combined_text, parsed):
            continue

        page.recommend_score = calculate_recommendation_score(
            combined_text, parsed["keywords"]
        )

        page.snippet = page.get_snippet(parsed["query"])

        revision_no, revision_date_text = extract_manual_file_revision_info(
            page.manual_file
        )
        page.revision_no = revision_no
        page.revision_date_text = revision_date_text
        page.revision_label = ""

        if revision_no or revision_date_text:
            revision_parts = []

            if revision_no:
                revision_parts.append(f"Rev. No : {revision_no}")

            if revision_date_text:
                revision_parts.append(f"Date : {revision_date_text}")

            page.revision_label = " / ".join(revision_parts)

        results.append(page)

    results.sort(key=lambda item: item.recommend_score, reverse=True)

    return results[:limit]


def recommend_file_pages(aircraft, manual_type, query, limit=10):
    parsed = parse_dispatch_search_query(query)

    queryset = ManualFilePDFPage.objects.select_related(
        "manual_file",
        "manual_file__aircraft",
    ).filter(
        manual_file__aircraft=aircraft,
        manual_file__manual_type=manual_type,
    )

    results = []

    for page in queryset[:1000]:
        combined_text = " ".join(
            [
                page.manual_file.description or "",
                page.text or "",
            ]
        )

        if not text_matches_query(combined_text, parsed):
            continue

        page.recommend_score = calculate_recommendation_score(
            combined_text, parsed["keywords"]
        )

        page.snippet = page.get_snippet(parsed["query"])

        results.append(page)

    results.sort(key=lambda item: item.recommend_score, reverse=True)

    return results[:limit]


def score_package_page(page, query):
    if not query:
        return 0

    score = 0
    words = query.lower().split()

    task = page.chapter.task.lower()
    subtask = page.chapter.subtask.lower()
    title = page.chapter.title.lower()
    text = page.text.lower()

    for word in words:
        if word in task:
            score += 100

        if word in subtask:
            score += 80

        if word in title:
            score += 50

        if word in text:
            score += 10

    return score


def score_file_page(page, query):
    if not query:
        return 0

    score = 0
    words = query.lower().split()

    manual_type = page.manual_file.manual_type.lower()
    description = page.manual_file.description.lower()
    text = page.text.lower()

    for word in words:
        if word in manual_type:
            score += 60

        if word in description:
            score += 40

        if word in text:
            score += 10

    return score


def find_best_package_page(aircraft, manual_type, query):
    if not query:
        return None

    pages = (
        ManualPDFPage.objects.select_related(
            "chapter",
            "chapter__package",
            "chapter__package__aircraft",
        )
        .filter(
            chapter__package__aircraft=aircraft,
            chapter__package__manual_type=manual_type,
        )
        .filter(
            Q(text__icontains=query)
            | Q(chapter__task__icontains=query)
            | Q(chapter__subtask__icontains=query)
            | Q(chapter__title__icontains=query)
        )[:200]
    )

    pages = list(pages)

    if not pages:
        return None

    for page in pages:
        page.match_score = score_package_page(page, query)

    pages.sort(key=lambda page: page.match_score, reverse=True)

    return pages[0]


def find_best_file_page(manual_file, query):
    if not manual_file or not query:
        return None

    pages = ManualFilePDFPage.objects.filter(manual_file=manual_file).filter(
        Q(text__icontains=query) | Q(manual_file__description__icontains=query)
    )[:200]

    pages = list(pages)

    if not pages:
        return None

    for page in pages:
        page.match_score = score_file_page(page, query)

    pages.sort(key=lambda page: page.match_score, reverse=True)

    return pages[0]


def calculate_auto_score(text, query):
    if not text or not query:
        return 0

    score = 0

    text_lower = text.lower()
    words = query.lower().split()

    for word in words:
        count = text_lower.count(word)

        if count > 0:
            score += count * 10

    return score


def build_text_regex(search_value, match_mode):
    escaped = re.escape(search_value)

    if match_mode == "contains":
        return escaped

    if match_mode == "startswith":
        return rf"\b{escaped}[A-Za-z0-9/_-]*"

    return rf"\b{escaped}\b"


def resolve_saved_package_page(dispatch, manual_type):
    if manual_type == "AMM":
        task_ref = dispatch.amm_task_ref or dispatch.amm_chapter
        page_number = dispatch.amm_page_number
        keyword = dispatch.amm_search_keyword or dispatch.amm_chapter

    elif manual_type == "FIM":
        task_ref = dispatch.fim_task_ref or dispatch.fim_chapter
        page_number = dispatch.fim_page_number
        keyword = dispatch.fim_search_keyword or dispatch.fim_chapter

    else:
        return None

    if not task_ref:
        return None

    queryset = ManualPDFPage.objects.select_related(
        "chapter",
        "chapter__package",
        "chapter__package__aircraft",
    ).filter(
        chapter__package__aircraft=dispatch.aircraft,
        chapter__package__manual_type=manual_type,
        chapter__task__icontains=task_ref,
    )

    if page_number:
        page = queryset.filter(page_number=page_number).first()

        if page:
            return page

    return find_best_package_page(dispatch.aircraft, manual_type, keyword)


def resolve_saved_file_page(dispatch):
    mel_file = dispatch.aircraft.get_mel()
    keyword = dispatch.mel_search_keyword or dispatch.mel_ref or dispatch.mel_number

    if not mel_file:
        return None

    if dispatch.mel_page_number:
        page = ManualFilePDFPage.objects.filter(
            manual_file=mel_file,
            page_number=dispatch.mel_page_number,
        ).first()

        if page:
            return page

    return find_best_file_page(mel_file, keyword)


def paginate_result_group(request, results, prefix):
    page_param = f"{prefix}_page"

    try:
        current_index = int(request.GET.get(page_param, 1))
    except ValueError:
        current_index = 1

    total = len(results)

    if total == 0:
        return {
            "item": None,
            "current": 0,
            "total": 0,
            "has_previous": False,
            "has_next": False,
            "previous_page": None,
            "next_page": None,
        }

    if current_index < 1:
        current_index = 1

    if current_index > total:
        current_index = total

    return {
        "item": results[current_index - 1],
        "current": current_index,
        "total": total,
        "has_previous": current_index > 1,
        "has_next": current_index < total,
        "previous_page": current_index - 1,
        "next_page": current_index + 1,
    }


def parse_manual_search_query(query):
    if not query:
        return "", "exact"

    value = query.strip()

    if value.startswith("*") and value.endswith("*") and len(value) > 2:
        return value[1:-1].strip(), "contains"

    if value.endswith("*") and len(value) > 1:
        return value[:-1].strip(), "startswith"

    return value, "exact"


def build_manual_text_regex(search_value, match_mode):
    escaped = re.escape(search_value)

    if match_mode == "contains":
        return escaped

    if match_mode == "startswith":
        return rf"\b{escaped}[A-Za-z0-9/_-]*"

    return rf"\b{escaped}\b"


def clean_cell(value):
    return " ".join((value or "").split())


def normalize_dash(value):
    value = clean_cell(value)
    return "" if value in ["-", "—", "–"] else value


def clean_noise_text(value):
    value = clean_cell(value)

    noise_patterns = [
        r"^\s*n\s+",
        r"\bn U\b",
        r"\bcA A\b",
        r"^l l\s+",
        r"^n o\s*",
        r"^o\s+",
    ]

    for pattern in noise_patterns:
        value = re.sub(pattern, "", value, flags=re.IGNORECASE).strip()

    return value


def clean_message(value):
    value = clean_noise_text(value)

    value = re.sub(r"^[a-zA-Z]\s+(?=[A-Z]{2,})", "", value)

    return value.strip()


def extract_level(value):
    value = clean_cell(value).upper()
    levels = re.findall(r"[AS]", value)
    return "".join(levels)


def extract_mel_item(value):
    value = clean_cell(value)
    match = re.search(r"\d{2}-\d{2}-\d{2}(?:-\d{2})?[A-Z]?", value)
    return match.group(0) if match else ""


def extract_adc(value):
    value = normalize_dash(value).upper()

    if value in ["CD", "RTG", "A", "D", "C"]:
        return value

    return ""


def clean_condition(value):
    value = clean_noise_text(value)

    noise_patterns = [
        r"^l\s+l\s+",
        r"^n\s+o\s*",
        r"^o\s+",
        r"^cA\s+A\s+",
        r"\bn\s+U\b",
        r"\bp\s+o\s+",
        r"^r\s+t\s+",
        r"^r\s",
        r"^l\s",
        r"^e\s",
        r"^l\s+o\s+",
        r"^l\s+l\s+o\s+",
    ]

    for pattern in noise_patterns:
        value = re.sub(pattern, "", value, flags=re.IGNORECASE).strip()

    # 앞뒤 단독 문자 제거
    value = re.sub(r"^[a-zA-Z]\s+", "", value)
    value = re.sub(r"\s+[a-zA-Z]$", "", value)

    # 뒤에 붙은 단독 소문자 제거
    value = re.sub(r"\s+[a-z]$", "", value).strip()

    return value


def extract_mel_dispatch_items_from_pdf(manual_file):

    if not manual_file.file:
        return 0

    count = 0

    MelDispatchItem.objects.filter(
        aircraft=manual_file.aircraft,
        manual_file=manual_file,
    ).delete()

    with pdfplumber.open(manual_file.file.path) as pdf:

        for page_index, page in enumerate(pdf.pages):

            page_number = page_index + 1
            tables = page.extract_tables()

            for table in tables:

                if not table:
                    continue

                current_message = ""
                current_level = ""
                current_condition = ""
                current_mel_item = ""
                current_adc = ""

                for row in table:

                    if not row:
                        continue

                    raw_message = row[0] or ""

                    message_line_count = len(
                        [line for line in raw_message.splitlines() if line.strip()]
                    )

                    row = [clean_cell(cell) for cell in row]

                    if len(row) < 5:
                        continue

                    message = clean_message(row[0])
                    level = extract_level(row[1])
                    condition = clean_condition(row[2])
                    mel_item = extract_mel_item(row[3])
                    adc = extract_adc(row[4])

                    if level in ["A", "S"] and message_line_count >= 2:
                        level = level + level

                    if (
                        message == "Message"
                        or level == "L"
                        or condition == "Condition"
                        or mel_item == "MEL Item"
                        or adc == "A/D/C"
                    ):
                        continue

                    if message:
                        current_message = message
                    else:
                        message = current_message

                    if level:
                        current_level = level
                    else:
                        level = current_level

                    for cell in row[2:]:
                        found_mel = extract_mel_item(cell)
                        if found_mel:
                            mel_item = found_mel
                            break

                    for cell in row[2:]:
                        found_adc = extract_adc(cell)
                        if found_adc:
                            adc = found_adc
                            break

                    condition = normalize_dash(condition)

                    if condition:
                        current_condition = condition
                    else:
                        condition = current_condition

                    if mel_item:
                        current_mel_item = mel_item
                    else:
                        mel_item = current_mel_item

                    if adc:
                        current_adc = adc
                    else:
                        adc = current_adc

                    if not message:
                        continue

                    if not condition and not mel_item and not adc:
                        continue

                    obj, created = MelDispatchItem.objects.get_or_create(
                        aircraft=manual_file.aircraft,
                        manual_file=manual_file,
                        message=message,
                        level=level,
                        condition=condition,
                        mel_item=mel_item,
                        adc=adc,
                        page_number=page_number,
                    )

                    if created:
                        count += 1

    return count


def is_fault_code_query(query):
    query = (query or "").strip()

    if not query:
        return False

    return bool(re.search(r"\d", query))


def is_message_query(query):
    query = (query or "").strip()

    if not query:
        return False

    return not is_fault_code_query(query)