import os, re, zipfile, fitz, shutil, uuid, pdfplumber, tempfile, subprocess
from datetime import datetime

from django.conf import settings
from bs4 import BeautifulSoup
from .models import ManualChapter, ManualPDFPage, ManualFilePDFPage, OtherManualPDFPage
from django.db import transaction
from pypdf import PdfReader, PdfWriter
from PIL import Image
import pytesseract

from django.core.files import File

IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"]
OFFICE_EXTENSIONS = [".doc", ".docx", ".xls", ".xlsx"]


def get_libreoffice_command():
    windows_path = r"C:\Program Files\LibreOffice\program\soffice.exe"

    if os.path.exists(windows_path):
        return windows_path

    command = shutil.which("libreoffice") or shutil.which("soffice")

    if command:
        return command

    raise Exception("LibreOffice 실행 파일을 찾을 수 없습니다.")


def convert_office_to_pdf(input_path, output_dir):
    libreoffice_command = get_libreoffice_command()

    result = subprocess.run(
        [
            libreoffice_command,
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            "--convert-to",
            "pdf",
            "--outdir",
            output_dir,
            input_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        raise Exception(
            f"PDF 변환 실패: {os.path.basename(input_path)} / {result.stderr}"
        )

    base_name = os.path.splitext(os.path.basename(input_path))[0]
    converted_pdf_path = os.path.join(output_dir, base_name + ".pdf")

    if not os.path.exists(converted_pdf_path):
        raise Exception("변환된 PDF 파일을 찾을 수 없습니다.")

    return converted_pdf_path


def convert_image_to_pdf(input_path, output_dir):
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    pdf_path = os.path.join(output_dir, base_name + ".pdf")

    with Image.open(input_path) as image:
        frames = []

        for frame_index in range(getattr(image, "n_frames", 1)):
            if getattr(image, "is_animated", False):
                image.seek(frame_index)

            frame = image.copy()

            if frame.mode in ("RGBA", "LA") or (
                frame.mode == "P" and "transparency" in frame.info
            ):
                background = Image.new("RGB", frame.size, "white")
                rgba_frame = frame.convert("RGBA")
                background.paste(rgba_frame, mask=rgba_frame.split()[-1])
                frame = background
            else:
                frame = frame.convert("RGB")

            frames.append(frame)

        if not frames:
            raise Exception("이미지 파일을 읽을 수 없습니다.")

        first_frame = frames[0]
        remaining_frames = frames[1:]

        first_frame.save(
            pdf_path,
            "PDF",
            resolution=100.0,
            save_all=bool(remaining_frames),
            append_images=remaining_frames,
        )

    return pdf_path


def delete_stored_file(file_field):
    if file_field:
        file_field.delete(save=False)


def get_ocr_language():
    return getattr(settings, "TESSERACT_OCR_LANG", "eng")


def get_ocr_timeout():
    return getattr(settings, "TESSERACT_OCR_TIMEOUT", 45)


def get_ocr_max_side():
    return getattr(settings, "TESSERACT_OCR_MAX_SIDE", 2400)


def normalize_ocr_text(value):
    return " ".join((value or "").split())


def prepare_image_for_ocr(image):
    max_side = get_ocr_max_side()
    width, height = image.size
    largest_side = max(width, height)

    if largest_side <= max_side:
        return image

    ratio = max_side / largest_side
    resized_size = (
        max(1, int(width * ratio)),
        max(1, int(height * ratio)),
    )

    return image.resize(resized_size, Image.Resampling.LANCZOS)


def extract_image_ocr_texts(input_path):
    texts = []

    with Image.open(input_path) as image:
        frame_count = getattr(image, "n_frames", 1)

        for frame_index in range(frame_count):
            if getattr(image, "is_animated", False):
                image.seek(frame_index)

            frame = image.copy()

            if frame.mode in ("RGBA", "LA") or (
                frame.mode == "P" and "transparency" in frame.info
            ):
                background = Image.new("RGB", frame.size, "white")
                rgba_frame = frame.convert("RGBA")
                background.paste(rgba_frame, mask=rgba_frame.split()[-1])
                frame = background
            else:
                frame = frame.convert("RGB")

            frame = prepare_image_for_ocr(frame)

            text = pytesseract.image_to_string(
                frame,
                lang=get_ocr_language(),
                timeout=get_ocr_timeout(),
            )
            texts.append(normalize_ocr_text(text))

    return texts


def extract_storage_image_ocr_texts(django_file):
    suffix = os.path.splitext(django_file.name.lower())[1]
    temp_input_path = storage_file_to_temp_file(
        django_file,
        suffix=suffix,
    )

    try:
        return extract_image_ocr_texts(temp_input_path)

    finally:
        if temp_input_path and os.path.exists(temp_input_path):
            os.remove(temp_input_path)


def convert_other_manual_file_to_pdf(other_file):
    if not other_file.file:
        return None

    original_name = other_file.file.name.lower()

    if original_name.endswith(".pdf"):
        delete_stored_file(other_file.converted_pdf)
        other_file.save(update_fields=["converted_pdf"])
        return other_file.file

    ext = os.path.splitext(original_name)[1]

    if ext not in OFFICE_EXTENSIONS + IMAGE_EXTENSIONS:
        return None

    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = os.path.join(temp_dir, os.path.basename(other_file.file.name))

        with other_file.file.open("rb") as source:
            with open(input_path, "wb") as target:
                target.write(source.read())

        if ext in OFFICE_EXTENSIONS:
            pdf_path = convert_office_to_pdf(
                input_path=input_path,
                output_dir=temp_dir,
            )
        else:
            pdf_path = convert_image_to_pdf(
                input_path=input_path,
                output_dir=temp_dir,
            )

        if not os.path.exists(pdf_path):
            raise Exception("PDF 변환 파일을 찾을 수 없습니다.")

        save_name = f"{os.path.splitext(os.path.basename(other_file.file.name))[0]}.pdf"

        delete_stored_file(other_file.converted_pdf)

        with open(pdf_path, "rb") as pdf_file:
            other_file.converted_pdf.save(
                save_name,
                File(pdf_file),
                save=True,
            )

    return other_file.converted_pdf


def storage_file_to_temp_file(django_file, suffix=""):
    temp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)

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


def extract_clean_text(page):
    words = page.get_text("words")

    seen = set()
    clean_words = []

    for word in words:
        x0, y0, x1, y1, text, block, line, word_no = word

        clean = " ".join((text or "").split())

        if not clean:
            continue

        key = (
            round(x0, 1),
            round(y0, 1),
            clean,
        )

        if key in seen:
            continue

        seen.add(key)
        clean_words.append(
            (block, line, word_no, clean)
        )

    clean_words.sort()

    return " ".join(
        item[3] for item in clean_words
    )


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
    extract_to = os.path.join(
        settings.MEDIA_ROOT,
        "extracted_manuals",
        str(package.aircraft.id),
        package.manual_type.lower(),
        str(package.id),
    )

    os.makedirs(extract_to, exist_ok=True)

    temp_zip_path = storage_file_to_temp_file(package.zip_file, suffix=".zip")

    try:
        safe_extract_zip(
            zip_file_path=temp_zip_path,
            extract_to=extract_to,
        )
    finally:
        if os.path.exists(temp_zip_path):
            os.remove(temp_zip_path)

        package.zip_file.close()
        temp_zip_path.flush()

        safe_extract_zip(
            zip_file_path=temp_zip_path,
            extract_to=extract_to,
        )

    index_html_path = find_index_html(extract_to)

    if not index_html_path:
        raise Exception("ZIP 안에서 index.html을 찾을 수 없습니다.")

    package.extracted_path = extract_to
    package.save(update_fields=["extracted_path"])

    save_package_revision_info(package, index_html_path)

    chapter_count = parse_index_html(
        package=package,
        index_html_path=index_html_path,
    )

    page_count = index_pdf_pages_for_package(package=package)

    package.processed = True
    package.save(update_fields=["processed"])

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

    new_pages = []

    document = fitz.open(pdf_path)

    try:
        for page_index in range(document.page_count):
            page = document.load_page(page_index)

            text = extract_clean_text(page)

            new_pages.append(
                ManualPDFPage(
                    chapter=chapter,
                    page_number=page_index + 1,
                    text=text,
                )
            )

    finally:
        document.close()

    with transaction.atomic():
        ManualPDFPage.objects.filter(chapter=chapter).delete()
        ManualPDFPage.objects.bulk_create(new_pages, batch_size=200)

    return len(new_pages)


def index_pdf_pages_for_package(package):
    total_pages = 0

    chapters = ManualChapter.objects.filter(package=package)

    for chapter in chapters:
        total_pages += index_pdf_pages_for_chapter(chapter)

    return total_pages


def extract_pdf_revision_info(pdf_path, source_file_name="", manual_type=""):
    manual_type = (manual_type or "").upper()

    is_mel_or_cdl = manual_type in ["MEL", "CDL"] or bool(
        re.search(
            r"(?:^|[\\/_\-])(MEL|CDL)(?:[\\/_\-.]|$)",
            source_file_name or "",
            re.IGNORECASE,
        )
    )

    is_cdl = manual_type == "CDL" or bool(
        re.search(
            r"(?:^|[\\/_\-])CDL(?:[\\/_\-.]|$)",
            source_file_name or "",
            re.IGNORECASE,
        )
    )

    revision_no = ""
    revision_date = ""

    def normalize_date(value):
        normalized = (value or "").upper().replace(" ", "").strip()

        three_digit_year_match = re.match(r"^(\d{1,2}[A-Z]{3})(\d{3})$", normalized)

        if three_digit_year_match:
            normalized = (
                f"{three_digit_year_match.group(1)}2{three_digit_year_match.group(2)}"
            )

        return normalized

    def parse_ddmmmyyyy(value):
        normalized = normalize_date(value)

        if not normalized:
            return None

        for fmt in ("%d%b%Y", "%d%b%y"):
            try:
                return datetime.strptime(normalized, fmt)
            except ValueError:
                continue

        return None

    def pick_latest_date(current, candidate):
        current_norm = normalize_date(current)
        candidate_norm = normalize_date(candidate)

        if not candidate_norm:
            return current_norm

        if not current_norm:
            return candidate_norm

        current_dt = parse_ddmmmyyyy(current_norm)
        candidate_dt = parse_ddmmmyyyy(candidate_norm)

        if current_dt and candidate_dt:
            return candidate_norm if candidate_dt >= current_dt else current_norm

        return candidate_norm

    revision_patterns = [
        r"Revision\s*No\.?\s*:?\s*([0-9][0-9A-Za-z\-]*)",
        r"Rev\.?\s*No\.?\s*:?\s*([0-9][0-9A-Za-z\-]*)",
    ]

    date_patterns = [
        r"Revision\s*Date\s*:?\s*([0-9]{1,2}\s+[A-Za-z]{3,9}\s+[0-9]{2,4})",
        r"Rev\.?\s*Date\s*:?\s*([0-9]{1,2}\s+[A-Za-z]{3,9}\s+[0-9]{2,4})",
        r"Issue\s*Date\s*:?\s*([0-9]{1,2}\s+[A-Za-z]{3,9}\s+[0-9]{2,4})",
        r"Approved\s*Date\s*:?\s*([0-9]{1,2}\s+[A-Za-z]{3,9}\s+[0-9]{2,4})",
        r"Date\s*:?\s*([0-9]{1,2}\s+[A-Za-z]{3,9}\s+[0-9]{2,4})",
        r"\b([0-9]{1,2}[A-Za-z]{3}[0-9]{4})\b",
    ]

    def extract_airbus_rev_date(value):
        if not value:
            return ""

        date_near_label_match = re.search(
            r"Rev\s*Date[^0-9A-Za-z]{0,20}(\d{1,2}[A-Za-z]{3}\d{4})",
            value,
            re.IGNORECASE,
        )

        if date_near_label_match:
            return normalize_date(date_near_label_match.group(1))

        return ""

    def extract_airbus_rev_date_candidates(value):
        if not value:
            return []

        if not re.search(r"Rev\.?\s*Date", value, re.IGNORECASE):
            return []

        matches = re.findall(
            r"\b(\d{1,2}\s*[A-Za-z]{3}\s*\d{2,4})\b",
            value,
            re.IGNORECASE,
        )

        return [normalize_date(match) for match in matches]

    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            return revision_no, revision_date

        latest_airbus_rev_date = ""
        highest_airbus_rev_no = None
        highest_airbus_rev_date = ""

        def update_airbus_rev_from_pairs(value):
            nonlocal highest_airbus_rev_no, highest_airbus_rev_date

            if not value:
                return

            if not re.search(r"Rev\s*No", value, re.IGNORECASE):
                return

            has_date_header = bool(
                re.search(r"Rev\.?\s*Date", value, re.IGNORECASE)
                or re.search(r"Issue\s*Date", value, re.IGNORECASE)
                or re.search(r"Approved\s*Date", value, re.IGNORECASE)
            )

            if not has_date_header:
                return

            rev_rows = re.findall(
                r"\b(\d{1,3})\s+(\d{1,2}\s*[A-Za-z]{3}\s*\d{2,4})\s+(\d{1,2}\s*[A-Za-z]{3}\s*\d{2,4})\b",
                value,
                re.IGNORECASE,
            )

            if rev_rows:
                for rev_no_text, issue_date_text, approved_date_text in rev_rows:
                    try:
                        rev_no_int = int(rev_no_text)
                    except ValueError:
                        continue

                    preferred_date = (
                        approved_date_text
                        if is_cdl and approved_date_text
                        else issue_date_text
                    )

                    if (
                        highest_airbus_rev_no is None
                        or rev_no_int >= highest_airbus_rev_no
                    ):
                        highest_airbus_rev_no = rev_no_int
                        highest_airbus_rev_date = normalize_date(preferred_date)

                return

            rev_pairs = re.findall(
                r"\b(\d{1,3})\s+(\d{1,2}\s*[A-Za-z]{3}\s*\d{2,4})\b",
                value,
                re.IGNORECASE,
            )

            for rev_no_text, rev_date_text in rev_pairs:
                try:
                    rev_no_int = int(rev_no_text)
                except ValueError:
                    continue

                if highest_airbus_rev_no is None or rev_no_int >= highest_airbus_rev_no:
                    highest_airbus_rev_no = rev_no_int
                    highest_airbus_rev_date = normalize_date(rev_date_text)

        for page in pdf.pages[:8]:
            text = page.extract_text() or ""
            normalized_text = re.sub(r"\s+", " ", text).strip()

            if is_mel_or_cdl:
                update_airbus_rev_from_pairs(normalized_text)

                for candidate in extract_airbus_rev_date_candidates(normalized_text):
                    latest_airbus_rev_date = pick_latest_date(
                        latest_airbus_rev_date,
                        candidate,
                    )

            if not revision_date:
                revision_date = extract_airbus_rev_date(
                    normalized_text
                ) or extract_airbus_rev_date(text)

            airbus_inline_match = re.search(
                r"Rev\s*No\s+Rev\s*Date\s+(\d{1,3})\s+(\d{1,2}[A-Za-z]{3}\d{4})",
                normalized_text,
                re.IGNORECASE,
            )

            if airbus_inline_match:
                revision_no = airbus_inline_match.group(1).strip()
                revision_date = normalize_date(airbus_inline_match.group(2))
                break

            if not revision_no:
                rev_match = re.search(
                    r"Rev\s*No.*?\b(\d{1,3})\b",
                    text,
                    re.IGNORECASE | re.DOTALL,
                )

                if rev_match:
                    revision_no = rev_match.group(1).strip()

            if not revision_no:
                for pattern in revision_patterns:
                    rev_match = re.search(pattern, text, re.IGNORECASE)

                    if rev_match:
                        revision_no = rev_match.group(1).strip()
                        break

            # 핵심 수정:
            # MEL/CDL이어도 Boeing 앞쪽의 "Date: 21 OCT 2025"를 읽도록 함.
            if not revision_date:
                for pattern in date_patterns:
                    date_match = re.search(pattern, text, re.IGNORECASE)

                    if date_match:
                        revision_date = normalize_date(date_match.group(1))
                        break

            if not revision_no or not revision_date:
                tables = page.extract_tables() or []

                for table in tables:
                    for row in table:
                        row_text = " ".join(
                            str(cell or "").strip() for cell in row if cell is not None
                        )

                        row_text = re.sub(r"\s+", " ", row_text).strip()

                        if is_mel_or_cdl:
                            update_airbus_rev_from_pairs(row_text)

                            for candidate in extract_airbus_rev_date_candidates(
                                row_text
                            ):
                                latest_airbus_rev_date = pick_latest_date(
                                    latest_airbus_rev_date,
                                    candidate,
                                )

                        table_match = re.search(
                            r"\b(\d{1,3})\b.*?\b(\d{1,2}[A-Za-z]{3}\d{4})\b",
                            row_text,
                            re.IGNORECASE,
                        )

                        if table_match:
                            revision_no = revision_no or table_match.group(1).strip()
                            revision_date = revision_date or normalize_date(
                                table_match.group(2)
                            )
                            break

                    if revision_no and revision_date:
                        break

            if revision_no and revision_date and not is_mel_or_cdl:
                break

        if is_mel_or_cdl:
            for page in pdf.pages[:120]:
                text = page.extract_text() or ""
                normalized_text = re.sub(r"\s+", " ", text).strip()

                update_airbus_rev_from_pairs(normalized_text)

                for candidate in extract_airbus_rev_date_candidates(normalized_text):
                    latest_airbus_rev_date = pick_latest_date(
                        latest_airbus_rev_date,
                        candidate,
                    )

            if highest_airbus_rev_no is not None:
                revision_no = str(highest_airbus_rev_no)

            if highest_airbus_rev_date:
                revision_date = highest_airbus_rev_date

            if not highest_airbus_rev_date and latest_airbus_rev_date:
                revision_date = latest_airbus_rev_date

        if not revision_date:
            for page in pdf.pages[:50]:
                text = page.extract_text() or ""
                normalized_text = re.sub(r"\s+", " ", text).strip()

                revision_date = extract_airbus_rev_date(
                    normalized_text
                ) or extract_airbus_rev_date(text)

                if not revision_date:
                    for pattern in date_patterns:
                        date_match = re.search(pattern, text, re.IGNORECASE)

                        if date_match:
                            revision_date = normalize_date(date_match.group(1))
                            break

                if revision_date:
                    break

    if not revision_no:
        file_name = os.path.basename(source_file_name) or os.path.basename(pdf_path)

        file_name_match = re.search(
            r"(?:^|[_\-])R(\d{1,3})(?=[_\-.]|$)",
            file_name,
            re.IGNORECASE,
        )

        if file_name_match:
            revision_no = file_name_match.group(1).strip()

    return revision_no, revision_date


def save_manual_file_revision_info(manual_file):
    if manual_file.manual_type not in ["MEL", "CDL", "OTHER"]:
        return "", ""

    if not manual_file.file:
        return "", ""

    temp_pdf_path = storage_file_to_temp_file(
        manual_file.file,
        suffix=".pdf",
    )

    try:
        revision_no, revision_date = extract_pdf_revision_info(
            temp_pdf_path,
            source_file_name=getattr(manual_file.file, "name", ""),
            manual_type=manual_file.manual_type,
        )

        manual_file.revision_no = revision_no
        manual_file.revision_date_text = revision_date
        manual_file.save(
            update_fields=[
                "revision_no",
                "revision_date_text",
            ]
        )

        return revision_no, revision_date

    finally:
        if os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)


def index_pdf_pages_for_manual_file(manual_file):
    if manual_file.manual_type not in ["MEL", "CDL", "OTHER"]:
        return 0

    if not manual_file.file:
        return 0

    temp_pdf_path = storage_file_to_temp_file(
        manual_file.file,
        suffix=".pdf",
    )

    try:
        revision_no, revision_date = extract_pdf_revision_info(
            temp_pdf_path,
            source_file_name=getattr(manual_file.file, "name", ""),
            manual_type=manual_file.manual_type,
        )

        manual_file.revision_no = revision_no
        manual_file.revision_date_text = revision_date
        manual_file.save(
            update_fields=[
                "revision_no",
                "revision_date_text",
            ]
        )

        new_pages = []

        document = fitz.open(temp_pdf_path)

        try:
            for page_index in range(document.page_count):
                page = document.load_page(page_index)

                text = extract_clean_text(page)

                new_pages.append(
                    ManualFilePDFPage(
                        manual_file=manual_file,
                        page_number=page_index + 1,
                        text=text,
                    )
                )

        finally:
            document.close()

        with transaction.atomic():
            ManualFilePDFPage.objects.filter(manual_file=manual_file).delete()
            ManualFilePDFPage.objects.bulk_create(new_pages, batch_size=200)

        return len(new_pages)

    finally:
        if os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)


def index_pdf_pages_for_manual_file_safely(manual_file):
    return index_pdf_pages_for_manual_file(manual_file)


def index_pdf_pages_for_common_manual_file_safely(common_file):
    if not common_file.file:
        return 0

    original_name = common_file.file.name.lower()
    suffix = os.path.splitext(original_name)[1]

    temp_input_path = storage_file_to_temp_file(
        common_file.file,
        suffix=suffix,
    )

    temp_dir = tempfile.mkdtemp()
    temp_pdf_path = None
    ocr_texts = []

    try:
        if suffix == ".pdf":
            temp_pdf_path = temp_input_path
            delete_stored_file(common_file.pdf_file)

        elif suffix in OFFICE_EXTENSIONS:
            temp_pdf_path = convert_office_to_pdf(
                input_path=temp_input_path,
                output_dir=temp_dir,
            )

            pdf_name = (
                os.path.splitext(os.path.basename(common_file.file.name))[0]
                + ".pdf"
            )

            delete_stored_file(common_file.pdf_file)

            with open(temp_pdf_path, "rb") as pdf_file:
                common_file.pdf_file.save(
                    pdf_name,
                    File(pdf_file),
                    save=False,
                )

        elif suffix in IMAGE_EXTENSIONS:
            temp_pdf_path = convert_image_to_pdf(
                input_path=temp_input_path,
                output_dir=temp_dir,
            )
            ocr_texts = extract_image_ocr_texts(temp_input_path)

            pdf_name = (
                os.path.splitext(os.path.basename(common_file.file.name))[0]
                + ".pdf"
            )

            delete_stored_file(common_file.pdf_file)

            with open(temp_pdf_path, "rb") as pdf_file:
                common_file.pdf_file.save(
                    pdf_name,
                    File(pdf_file),
                    save=False,
                )

        else:
            raise Exception("지원하지 않는 파일 형식입니다.")

        revision_no, revision_date = extract_pdf_revision_info(
            temp_pdf_path,
            source_file_name=getattr(common_file.file, "name", ""),
            manual_type="COMMON",
        )

        if revision_no and not common_file.revision_no:
            common_file.revision_no = revision_no

        if revision_date and not common_file.revision_date_text:
            common_file.revision_date_text = revision_date

        common_file.save()

        from .models import CommonManualPDFPage

        new_pages = []

        document = fitz.open(temp_pdf_path)

        try:
            for page_index in range(document.page_count):
                page = document.load_page(page_index)
                text = extract_clean_text(page)

                if ocr_texts and page_index < len(ocr_texts):
                    text = ocr_texts[page_index] or text

                new_pages.append(
                    CommonManualPDFPage(
                        common_file=common_file,
                        page_number=page_index + 1,
                        text=text,
                    )
                )

        finally:
            document.close()

        with transaction.atomic():
            CommonManualPDFPage.objects.filter(common_file=common_file).delete()
            CommonManualPDFPage.objects.bulk_create(new_pages, batch_size=200)

        return len(new_pages)

    finally:
        if temp_input_path and os.path.exists(temp_input_path):
            os.remove(temp_input_path)

        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


def index_pdf_pages_for_other_manual_file_safely(other_file):
    original_ext = ""
    ocr_texts = []

    if other_file.file:
        original_ext = os.path.splitext(other_file.file.name.lower())[1]

    if original_ext in IMAGE_EXTENSIONS:
        ocr_texts = extract_storage_image_ocr_texts(other_file.file)

    pdf_file = convert_other_manual_file_to_pdf(other_file)

    if not pdf_file:
        return 0

    return index_pdf_pages_for_other_manual_file(
        other_file,
        ocr_texts=ocr_texts,
    )


def index_pdf_pages_for_other_manual_file(other_file, ocr_texts=None):
    import fitz

    ocr_texts = ocr_texts or []
    pdf_file = other_file.pdf_file

    if not pdf_file:
        return 0

    new_pages = []

    with pdf_file.open("rb") as file:
        pdf_document = fitz.open(stream=file.read(), filetype="pdf")

        try:
            for page_index in range(pdf_document.page_count):
                page = pdf_document.load_page(page_index)
                text = page.get_text("text") or ""

                if ocr_texts and page_index < len(ocr_texts):
                    text = ocr_texts[page_index] or text

                new_pages.append(
                    OtherManualPDFPage(
                        other_file=other_file,
                        page_number=page_index + 1,
                        text=text,
                    )
                )

        finally:
            pdf_document.close()

    with transaction.atomic():
        OtherManualPDFPage.objects.filter(other_file=other_file).delete()
        OtherManualPDFPage.objects.bulk_create(new_pages, batch_size=200)

    return len(new_pages)


def process_manual_package_safely(package):
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
    temp_zip_path = None

    try:
        os.makedirs(temp_extract_to, exist_ok=True)

        temp_zip = tempfile.NamedTemporaryFile(
            suffix=".zip",
            delete=False,
        )

        temp_zip_path = temp_zip.name

        package.zip_file.open("rb")

        for chunk in package.zip_file.chunks():
            temp_zip.write(chunk)

        package.zip_file.close()

        temp_zip.flush()
        temp_zip.close()

        safe_extract_zip(
            zip_file_path=temp_zip_path,
            extract_to=temp_extract_to,
        )

        for root, dirs, files in os.walk(temp_extract_to):
            for file_name in files:
                ext = os.path.splitext(file_name)[1].lower()

                if ext in [".xlsx", ".xls"]:
                    excel_path = os.path.join(root, file_name)

                    try:
                        convert_office_to_pdf(
                            input_path=excel_path,
                            output_dir=root,
                        )
                    except Exception as error:
                        raise Exception(f"{file_name} 변환 실패: {error}")

        if temp_zip_path and os.path.exists(temp_zip_path):
            os.remove(temp_zip_path)
            temp_zip_path = None

        index_html_path = find_index_html(temp_extract_to)

        # if not index_html_path:
        #     raise Exception("ZIP 안에서 index.html을 찾을 수 없습니다.")

        with transaction.atomic():
            ManualChapter.objects.filter(package=package).delete()

            package.extracted_path = temp_extract_to
            package.processed = False
            package.save(update_fields=["extracted_path", "processed"])

            if index_html_path:
                save_package_revision_info(package, index_html_path)

                chapter_count = parse_index_html(
                    package=package,
                    index_html_path=index_html_path,
                )
            else:
                chapter_count = parse_pdf_files_without_index(
                    package=package,
                    extract_to=temp_extract_to,
                )

            if os.path.exists(final_extract_to):
                shutil.rmtree(final_extract_to, ignore_errors=True)

            os.makedirs(os.path.dirname(final_extract_to), exist_ok=True)
            shutil.move(temp_extract_to, final_extract_to)

            package.extracted_path = final_extract_to
            package.processed = False
            package.save(update_fields=["extracted_path", "processed"])

            if not package.revision_no and not package.revision_date_text:
                first_pdf = ManualChapter.objects.filter(package=package).first()

                if first_pdf:
                    pdf_path = os.path.join(
                        package.extracted_path, first_pdf.pdf_relative_path
                    )

                    rev_no, rev_date = extract_pdf_revision_info(
                        pdf_path, manual_type=first_pdf.manual_file.manual_type
                    )

                    package.revision_no = rev_no
                    package.revision_date_text = rev_date
                    package.save(update_fields=["revision_no", "revision_date_text"])

        page_count = index_pdf_pages_for_package(package=package)

        package.processed = True
        package.save(update_fields=["processed"])

        if old_extracted_path and old_extracted_path != final_extract_to:
            shutil.rmtree(old_extracted_path, ignore_errors=True)

        return {
            "chapter_count": chapter_count,
            "page_count": page_count,
        }

    except Exception:
        shutil.rmtree(temp_extract_to, ignore_errors=True)
        raise

    finally:
        if temp_zip_path and os.path.exists(temp_zip_path):
            os.remove(temp_zip_path)


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


def get_pdf_full_path(self):
    return os.path.join(settings.MEDIA_ROOT, self.pdf_relative_path)


def parse_manual_search_query(query):
    query = (query or "").strip()

    if not query:
        return "", "exact"

    query = " ".join(query.split())

    if "*" in query:
        return query.lower(), "wildcard"

    return query.lower(), "exact"


def build_manual_text_regex(search_value, match_mode):
    search_value = (search_value or "").strip().lower()

    if not search_value:
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
        for token in search_value.split()
        if token.strip()
    ]

    if not tokens:
        return ""

    if match_mode == "wildcard":
        return r"\s+".join(build_token_regex(token) for token in tokens)

    exact_words = [
        rf"(?<![{token_chars}]){re.escape(token)}(?![{token_chars}])"
        for token in tokens
    ]
    return r"\s+".join(exact_words)


def build_snippet_from_regex(text, text_regex, length=180):
    if not text:
        return ""

    if not text_regex:
        return text[:length]

    match = re.search(text_regex, text, re.IGNORECASE)

    if not match:
        return text[:length]

    start = max(match.start() - 80, 0)
    end = min(start + length, len(text))

    if end < match.end():
        end = min(match.end() + 40, len(text))
        start = max(end - length, 0)

    snippet = text[start:end]

    if start > 0:
        snippet = "..." + snippet

    if end < len(text):
        snippet = snippet + "..."

    return snippet


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


def parse_pdf_files_without_index(package, extract_to):
    ManualChapter.objects.filter(package=package).delete()

    extracted_root = os.path.abspath(extract_to)
    created_count = 0

    pdf_files = []

    for root, dirs, files in os.walk(extract_to):
        for file_name in files:
            if not file_name.lower().endswith(".pdf"):
                continue

            pdf_abs_path = os.path.abspath(os.path.join(root, file_name))

            if os.path.commonpath([extracted_root, pdf_abs_path]) != extracted_root:
                continue

            pdf_files.append(pdf_abs_path)

    pdf_files.sort()

    for index, pdf_abs_path in enumerate(pdf_files, start=1):
        file_name = os.path.basename(pdf_abs_path)
        title = os.path.splitext(file_name)[0]

        task = extract_task_from_text(title)

        if not task:
            task = f"PDF-{index:03d}"

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
