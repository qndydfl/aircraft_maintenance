import os, re, zipfile, fitz, shutil, uuid, pdfplumber

from django.conf import settings
from bs4 import BeautifulSoup
from .models import ManualChapter, ManualPDFPage, ManualFilePDFPage
from django.db import transaction
from django.core.files import File
from pypdf import PdfReader, PdfWriter


def safe_extract_zip(zip_file_path, extract_to):
    os.makedirs(extract_to, exist_ok=True)

    with zipfile.ZipFile(zip_file_path, "r") as zip_ref:
        for member in zip_ref.infolist():
            member_path = os.path.abspath(os.path.join(extract_to, member.filename))

            extract_root = os.path.abspath(extract_to)

            if os.path.commonpath([extract_root, member_path]) != extract_root:
                raise Exception("안전하지 않은 ZIP 경로가 감지되었습니다.")

        zip_ref.extractall(extract_to)


def find_index_html(extract_to):
    for root, dirs, files in os.walk(extract_to):
        for file_name in files:
            if file_name.lower() == "index.html":
                return os.path.join(root, file_name)

    return None


def process_manual_package(package):
    zip_file_path = package.zip_file.path

    extract_to = os.path.join(
        settings.MEDIA_ROOT,
        "extracted_manuals",
        str(package.aircraft.id),
        package.manual_type.lower(),
        str(package.id),
    )

    safe_extract_zip(zip_file_path=zip_file_path, extract_to=extract_to)

    index_html_path = find_index_html(extract_to)

    if not index_html_path:
        raise Exception("ZIP 안에서 index.html을 찾을 수 없습니다.")

    package.extracted_path = extract_to
    package.save(
        update_fields=[
            "extracted_path",
        ]
    )

    save_package_revision_info(package, index_html_path)

    chapter_count = parse_index_html(package=package, index_html_path=index_html_path)

    page_count = index_pdf_pages_for_package(package=package)

    package.processed = True
    package.save(
        update_fields=[
            "processed",
        ]
    )

    return {
        "index_html_path": index_html_path,
        "chapter_count": chapter_count,
        "page_count": page_count,
    }


def extract_task_from_text(text):
    import re

    pattern = r"\b\d{2}-\d{2}-\d{2}\b"
    match = re.search(pattern, text)

    if match:
        return match.group(0)

    return ""


def parse_index_html(package, index_html_path):
    base_dir = os.path.dirname(index_html_path)

    with open(index_html_path, "r", encoding="utf-8", errors="ignore") as file:
        soup = BeautifulSoup(file, "html.parser")

    links = soup.find_all("a")

    created_count = 0

    ManualChapter.objects.filter(package=package).delete()

    for link in links:
        href = link.get("href", "").strip()

        if not href:
            continue

        if not href.lower().endswith(".pdf"):
            continue

        title = link.get_text(" ", strip=True)

        if not title:
            title = os.path.basename(href)

        task = extract_task_from_text(title + " " + href)

        if not task:
            task = os.path.splitext(os.path.basename(href))[0]

        pdf_abs_path = os.path.abspath(os.path.join(base_dir, href))

        extracted_root = os.path.abspath(package.extracted_path)

        if os.path.commonpath([extracted_root, pdf_abs_path]) != extracted_root:
            continue

        pdf_relative_path = os.path.relpath(pdf_abs_path, package.extracted_path)

        ManualChapter.objects.create(
            package=package,
            task=task,
            subtask="",
            title=title,
            pdf_relative_path=pdf_relative_path,
        )

        created_count += 1

    return created_count


def index_pdf_pages_for_chapter(chapter):
    pdf_path = os.path.abspath(
        os.path.join(chapter.package.extracted_path, chapter.pdf_relative_path)
    )

    extracted_root = os.path.abspath(chapter.package.extracted_path)

    if os.path.commonpath([extracted_root, pdf_path]) != extracted_root:
        return 0

    if not os.path.exists(pdf_path):
        return 0

    ManualPDFPage.objects.filter(chapter=chapter).delete()

    created_count = 0

    document = fitz.open(pdf_path)

    try:
        for page_index in range(document.page_count):
            page = document.load_page(page_index)

            text = page.get_text("text")

            ManualPDFPage.objects.create(
                chapter=chapter, page_number=page_index + 1, text=text
            )

            created_count += 1

    finally:
        document.close()

    return created_count


def index_pdf_pages_for_package(package):
    total_pages = 0

    chapters = ManualChapter.objects.filter(package=package)

    for chapter in chapters:
        total_pages += index_pdf_pages_for_chapter(chapter)

    return total_pages


def index_pdf_pages_for_manual_file(manual_file):
    if manual_file.manual_type not in ["MEL", "CDL"]:
        return 0

    if not manual_file.file:
        return 0

    pdf_path = manual_file.file.path

    if not os.path.exists(pdf_path):
        return 0

    ManualFilePDFPage.objects.filter(manual_file=manual_file).delete()

    created_count = 0

    document = fitz.open(pdf_path)

    try:
        for page_index in range(document.page_count):
            page = document.load_page(page_index)

            text = page.get_text("text")

            ManualFilePDFPage.objects.create(
                manual_file=manual_file, page_number=page_index + 1, text=text
            )

            created_count += 1

    finally:
        document.close()

    return created_count


def process_manual_package_safely(package):
    zip_file_path = package.zip_file.path

    final_extract_to = os.path.join(
        settings.MEDIA_ROOT,
        "extracted_manuals",
        str(package.aircraft.id),
        package.manual_type.lower(),
        str(package.id),
    )

    temp_extract_to = os.path.join(
        settings.MEDIA_ROOT,
        "temp_extracted_manuals",
        str(uuid.uuid4()),
    )

    old_extracted_path = package.extracted_path

    try:
        safe_extract_zip(zip_file_path=zip_file_path, extract_to=temp_extract_to)

        index_html_path = find_index_html(temp_extract_to)

        if not index_html_path:
            raise Exception("ZIP 안에서 index.html을 찾을 수 없습니다.")

        with transaction.atomic():

            ManualChapter.objects.filter(package=package).delete()

            package.extracted_path = temp_extract_to
            package.processed = False
            package.save(
                update_fields=[
                    "extracted_path",
                    "processed",
                ]
            )

            save_package_revision_info(package, index_html_path)

            chapter_count = parse_index_html(
                package=package, index_html_path=index_html_path
            )

            page_count = index_pdf_pages_for_package(package=package)

            if os.path.exists(final_extract_to):
                shutil.rmtree(final_extract_to, ignore_errors=True)

            os.makedirs(os.path.dirname(final_extract_to), exist_ok=True)

            shutil.move(temp_extract_to, final_extract_to)

            package.extracted_path = final_extract_to
            package.processed = True
            package.save(
                update_fields=[
                    "extracted_path",
                    "processed",
                ]
            )

        if old_extracted_path and old_extracted_path != final_extract_to:
            shutil.rmtree(old_extracted_path, ignore_errors=True)

        return {
            "chapter_count": chapter_count,
            "page_count": page_count,
        }

    except Exception:
        shutil.rmtree(temp_extract_to, ignore_errors=True)

        raise


def index_pdf_pages_for_manual_file_safely(manual_file):
    if manual_file.manual_type not in ["MEL", "CDL"]:
        return 0

    if not manual_file.file:
        return 0

    pdf_path = manual_file.file.path

    if not os.path.exists(pdf_path):
        return 0

    new_pages = []

    document = fitz.open(pdf_path)

    try:
        for page_index in range(document.page_count):
            page = document.load_page(page_index)

            text = page.get_text("text")

            new_pages.append(
                ManualFilePDFPage(
                    manual_file=manual_file, page_number=page_index + 1, text=text
                )
            )

    finally:
        document.close()

    with transaction.atomic():
        ManualFilePDFPage.objects.filter(manual_file=manual_file).delete()

        ManualFilePDFPage.objects.bulk_create(new_pages)

    return len(new_pages)


def calculate_package_score(page, query):
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


def calculate_file_score(page, query):
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


def parse_search_query(query):
    if not query:
        return "", "exact"

    trimmed = query.strip()

    if trimmed.startswith("*") and trimmed.endswith("*") and len(trimmed) > 2:
        return trimmed[1:-1].strip(), "contains"

    if trimmed.endswith("*") and len(trimmed) > 1:
        return trimmed[:-1].strip(), "startswith"

    return trimmed, "exact"


def build_text_regex(search_value, match_mode):
    escaped = re.escape(search_value)

    if match_mode == "contains":
        return escaped

    if match_mode == "startswith":
        return rf"\b{escaped}[A-Za-z0-9/_-]*"

    return rf"\b{escaped}\b"


def safe_int(value, default=1):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_match_navigation(pages, current_page):
    matching_pages = list(pages)

    prev_match = None
    next_match = None

    for page_number in reversed(matching_pages):
        if page_number < current_page:
            prev_match = page_number
            break

    for page_number in matching_pages:
        if page_number > current_page:
            next_match = page_number
            break

    if current_page in matching_pages:
        current_match_index = matching_pages.index(current_page) + 1
    else:
        current_match_index = 0

    return {
        "prev_match": prev_match,
        "next_match": next_match,
        "current_match_index": current_match_index,
        "match_count": len(matching_pages),
    }


def merge_package_chapter_pdfs(package):
    chapters = package.chapters.all().order_by("task", "subtask")

    if not chapters.exists():
        return None

    writer = PdfWriter()
    page_offset = 0

    for chapter in chapters:
        if not chapter.pdf_relative_path:
            continue

        pdf_path = chapter.get_pdf_full_path()

        if not os.path.exists(pdf_path):
            continue

        reader = PdfReader(pdf_path)

        writer.append(pdf_path)

        title_parts = [chapter.task]

        if chapter.subtask:
            title_parts.append(chapter.subtask)

        if chapter.title:
            title_parts.append(chapter.title)

        bookmark_title = " / ".join(title_parts)

        writer.add_outline_item(title=bookmark_title, page_number=page_offset)

        page_offset += len(reader.pages)

    if len(writer.pages) == 0:
        return None

    output_dir = os.path.join("media", "merged_manuals")
    os.makedirs(output_dir, exist_ok=True)

    filename = f"{package.aircraft.name}_{package.manual_type}_{package.id}.pdf"
    filename = filename.replace(" ", "_")

    output_path = os.path.join(output_dir, filename)

    with open(output_path, "wb") as f:
        writer.write(f)

    with open(output_path, "rb") as f:
        package.merged_pdf.save(filename, File(f), save=True)

    return package.merged_pdf


def get_pdf_full_path(self):
    return os.path.join(settings.MEDIA_ROOT, self.pdf_relative_path)


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


def extract_package_revision_info(package_dir):
    revision_no = ""
    revision_date = ""

    index_path = os.path.join(package_dir, "index.html")

    if not os.path.exists(index_path):
        return revision_no, revision_date

    with open(index_path, "r", encoding="utf-8", errors="ignore") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    match = re.search(
        r"\bRev(?:ision)?\.?\s*(?:No\.?)?\s*:?\s*(\d+)\s*[-–]\s*([0-9]{1,2}\s+[A-Za-z]{3,9}\s+[0-9]{4})",
        text,
        re.IGNORECASE,
    )

    if match:
        revision_no = match.group(1).strip()
        revision_date = match.group(2).strip()

    return revision_no, revision_date


def save_package_revision_info(package, index_html_path):
    package_dir = os.path.dirname(index_html_path)
    revision_no, revision_date = extract_package_revision_info(package_dir)

    package.revision_no = revision_no
    package.revision_date_text = revision_date
    package.save(
        update_fields=[
            "revision_no",
            "revision_date_text",
        ]
    )

    return revision_no, revision_date


def extract_pdf_revision_info(pdf_path):
    revision_no = ""
    revision_date = ""

    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            return revision_no, revision_date

        text = pdf.pages[0].extract_text() or ""

    rev_match = re.search(
        r"Rev\.?\s*No\.?\s*:?\s*([0-9A-Za-z\-]+)",
        text,
        re.IGNORECASE,
    )

    date_match = re.search(
        r"Date\s*:?\s*([0-9]{1,2}\s+[A-Za-z]{3,9}\s+[0-9]{4})",
        text,
        re.IGNORECASE,
    )

    if rev_match:
        revision_no = rev_match.group(1).strip()

    if date_match:
        revision_date = date_match.group(1).strip()

    return revision_no, revision_date
