import re, fitz, pdfplumber, os, tempfile
from django.db.models import Q

from .models import DispatchKeywordDictionary, MelDispatchItem
from manuals.models import ManualPDFPage, ManualFilePDFPage
from manuals.revision_utils import extract_revision_info_from_filename
from manuals.services import build_manual_text_regex, parse_manual_search_query


def extract_manual_file_revision_info(manual_file):
    if not manual_file or not manual_file.file:
        return "", ""

    return extract_revision_info_from_filename(manual_file.file.name)


def parse_search_query(query):
    if not query:
        return "", "exact"

    trimmed = " ".join(query.strip().split())

    if trimmed.startswith("*") and trimmed.endswith("*") and len(trimmed) > 2:
        return trimmed[1:-1].strip(), "contains"

    if "*" in trimmed:
        return trimmed.lower(), "wildcard"

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
        search_value = trimmed[1:-1].strip()

        return {
            "query": search_value,
            "mode": "contains_words",
            "keywords": [search_value],
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

    if not query_upper:
        return False

    if parsed["mode"] == "exact_phrase":
        return query_upper in text_upper

    if parsed["mode"] == "contains_words":
        return query_upper in text_upper

    if parsed["mode"] == "startswith":
        pattern = rf"\b{re.escape(query_upper)}[A-Z0-9/_-]*"
        return bool(re.search(pattern, text_upper))

    if parsed["mode"] == "exact":
        pattern = rf"\b{re.escape(query_upper)}\b"
        return bool(re.search(pattern, text_upper))

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


def build_text_regex(search_value, match_mode):
    escaped = re.escape(search_value)

    if match_mode == "contains":
        return escaped

    if match_mode == "startswith":
        return rf"\b{escaped}[A-Za-z0-9/_-]*"

    return rf"\b{escaped}\b"


def build_structured_search_q(fields, search_value, match_mode):
    if not search_value:
        return Q()

    if match_mode == "wildcard":
        text_regex = build_manual_text_regex(search_value, match_mode)

        if not text_regex:
            return Q()

        filters = Q()

        for field in fields:
            filters |= Q(**{f"{field}__iregex": text_regex})

        return filters

    lookup_suffix = {
        "contains": "icontains",
        "startswith": "istartswith",
        "exact": "iexact",
    }.get(match_mode, "iexact")

    filters = Q()

    for field in fields:
        filters |= Q(**{f"{field}__{lookup_suffix}": search_value})

    return filters


def build_mel_dispatch_search_q(fields, search_value, match_mode):
    if not search_value:
        return Q()

    if match_mode == "contains":
        lookup_suffix = "icontains"
        filters = Q()

        for field in fields:
            filters |= Q(**{f"{field}__{lookup_suffix}": search_value})

        return filters

    text_regex = build_manual_text_regex(search_value, match_mode)

    if not text_regex:
        return Q()

    if match_mode == "exact":
        text_regex = rf"^\s*{text_regex}\s*$"

    filters = Q()

    for field in fields:
        filters |= Q(**{f"{field}__iregex": text_regex})

    return filters


def exclude_noisy_mel_dispatch_rows(queryset):
    return queryset.exclude(
        Q(mel_item="")
        & (
            Q(condition="")
            | Q(condition__iregex=r"^\s*[A-Za-z]\s*$")
        )
    )


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
    value = re.sub(r"\bSTUART\b", "START", value, flags=re.IGNORECASE)

    return value.strip()


def split_mel_messages(value):
    raw_lines = str(value or "").splitlines()
    lines = []

    for raw_line in raw_lines:
        line = clean_message(raw_line)

        if not line or (len(line) == 1 and line.isalpha()):
            continue

        lines.append(line)

    if not lines:
        return []

    first_words = [line.split()[0].upper() for line in lines if line.split()]
    repeated_prefix = len(lines) > 1 and len(set(first_words)) == 1

    if repeated_prefix:
        return lines

    return [clean_message(value)]


def extract_mel_item(value):
    value = clean_cell(value)
    normalized = normalize_dash(value).upper()

    if normalized in ["N/A", "NA"] or re.search(
        r"(?<![A-Z])N\s*/\s*A(?![A-Z])",
        normalized,
    ):
        return "N/A"

    if re.search(r"NONE\s*\(\s*NO\s+DISPATCH\s*\)", normalized):
        return "None(No Dispatch)"

    match = re.search(r"\d{2}-\d{2}-\d{2}(?:-\d{2})?[A-Z]?", value)
    return match.group(0) if match else ""


def clean_condition(value):
    value = clean_noise_text(value)

    # Watermarks can be extracted as several leading lowercase letters on
    # separate lines (for example "t n Proximity...").
    value = re.sub(r"^(?:[a-z]\s+)+(?=[A-Z])", "", value).strip()

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

    if len(value) == 1 and value.isalpha():
        return ""

    return value


def extract_mel_table_fields(row):
    row = [clean_cell(cell) for cell in row]

    if len(row) < 5:
        return None

    message = clean_message(row[0])
    condition = normalize_dash(clean_condition(row[2]))
    mel_item = extract_mel_item(row[3])

    # Respect the table columns first. References such as TIB-777-27-014 in
    # Condition must not replace the actual MEL Item in the next column.
    if not mel_item:
        for cell in row[2:]:
            found_mel = extract_mel_item(cell)
            if found_mel:
                mel_item = found_mel
                break

    return message, condition, mel_item


def extract_mel_table_records(table):
    records = []
    current_messages = []
    current_context = {
        "condition": "",
        "mel_item": "",
    }

    for raw_row in table or []:
        if not raw_row or len(raw_row) < 5:
            continue

        fields = extract_mel_table_fields(raw_row)

        if not fields:
            continue

        _, condition, mel_item = fields
        messages = split_mel_messages(raw_row[0])

        if messages and messages[0].lower() == "message":
            continue

        has_row_context = bool(condition or mel_item)

        if messages:
            current_messages = messages

            if has_row_context:
                current_context = {
                    "condition": condition,
                    "mel_item": mel_item,
                }

        elif not current_messages:
            continue
        elif has_row_context:
            current_context = {
                "condition": condition,
                "mel_item": mel_item,
            }

        if not has_row_context and not any(current_context.values()):
            continue

        for message in current_messages:
            record = {
                "message": message,
                **current_context,
            }

            if record not in records:
                records.append(record)

    return records


def extract_airbus_mel_dispatch_blocks(text):
    text = " ".join((text or "").split())

    if not text:
        return []

    block_pattern = re.compile(
        r"Dispatch\s+Message\s*:\s*(?P<message>.*?)\s+Ident\.\s*:"
        r"(?P<body>.*?)(?=\s+Dispatch\s+Message\s*:|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    condition_pattern = re.compile(
        r"CONDITION\s+OF\s+DISPATCH\s+(?P<condition>.*?)"
        r"(?=\s+Refer\s+to\s+Item\b|\s+NO\s+DISPATCH\b|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    item_pattern = re.compile(
        r"Refer\s+to\s+Item\s+"
        r"(?P<mel_item>\d{2}-\d{2}-\d{2}(?:-\d{2})?[A-Z]?)",
        re.IGNORECASE,
    )
    results = []

    for match in block_pattern.finditer(text):
        message = clean_cell(match.group("message"))
        body = match.group("body") or ""
        condition_match = condition_pattern.search(body)
        item_match = item_pattern.search(body)
        no_dispatch = bool(re.search(r"\bNO\s+DISPATCH\b", body, re.IGNORECASE))

        if not message or (not item_match and not no_dispatch):
            continue

        results.append(
            {
                "message": message,
                "condition": (
                    clean_cell(condition_match.group("condition"))
                    if condition_match
                    else ""
                ),
                "mel_item": (
                    item_match.group("mel_item").upper()
                    if item_match
                    else "None(No Dispatch)"
                ),
                "is_none_dispatch": no_dispatch and not item_match,
            }
        )

    return results


def is_eicas_message_page(text):
    header = (text or "")[:500]
    standalone_heading = re.search(
        r"^\s*EICAS\s+MESSAGES?\s*$",
        header,
        re.IGNORECASE | re.MULTILINE,
    )
    collapsed_heading = re.search(
        r"^\s*(?:PREAMBLE\s+)?(?:\S+\s+){0,3}MEL\s+EICAS\s+MESSAGES?\b",
        " ".join(header.split()),
        re.IGNORECASE,
    )
    return bool(standalone_heading or collapsed_heading)


def storage_file_to_temp_file(django_file, suffix=""):
    temp_file = tempfile.NamedTemporaryFile(
        suffix=suffix,
        delete=False,
    )

    try:
        django_file.open("rb")

        for chunk in django_file.chunks():
            temp_file.write(chunk)

        django_file.close()
        temp_file.flush()
        temp_file.close()

        return temp_file.name

    except Exception:
        temp_file.close()

        if os.path.exists(temp_file.name):
            os.remove(temp_file.name)

        raise


def extract_mel_dispatch_items_from_pdf(manual_file):

    if not manual_file.file:
        return 0

    count = 0

    temp_pdf_path = storage_file_to_temp_file(
        manual_file.file,
        suffix=".pdf",
    )

    try:
        MelDispatchItem.objects.filter(
            aircraft=manual_file.aircraft,
            manual_file=manual_file,
        ).delete()

        with pdfplumber.open(temp_pdf_path) as pdf:

            for page_index, page in enumerate(pdf.pages):

                page_number = page_index + 1
                page_text = page.extract_text() or ""

                if manual_file.aircraft.maker == "AIRBUS":
                    for item in extract_airbus_mel_dispatch_blocks(
                        page_text
                    ):
                        _, created = MelDispatchItem.objects.get_or_create(
                            aircraft=manual_file.aircraft,
                            manual_file=manual_file,
                            message=item["message"],
                            condition=item["condition"],
                            mel_item=item["mel_item"],
                            page_number=page_number,
                        )

                        if created:
                            count += 1

                    continue

                if not is_eicas_message_page(page_text):
                    continue

                tables = page.extract_tables()

                for table in tables:

                    if not table:
                        continue

                    for item in extract_mel_table_records(table):
                        obj, created = MelDispatchItem.objects.get_or_create(
                            aircraft=manual_file.aircraft,
                            manual_file=manual_file,
                            message=item["message"],
                            condition=item["condition"],
                            mel_item=item["mel_item"],
                            page_number=page_number,
                        )

                        if created:
                            count += 1

        return count

    finally:
        if os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)
