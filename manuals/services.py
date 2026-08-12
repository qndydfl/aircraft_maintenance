import os, re, zipfile, fitz, shutil, uuid, tempfile, subprocess

from django.conf import settings
from bs4 import BeautifulSoup
from .models import ManualChapter, ManualPDFPage, ManualFilePDFPage, OtherManualPDFPage
from django.db import transaction
from PIL import Image
import pytesseract

from django.core.files import File
from .revision_utils import extract_revision_info_from_filename

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
        clean_words.append((block, line, word_no, clean))

    clean_words.sort()

    return " ".join(item[3] for item in clean_words)


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


def extract_task_from_text(text):
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

    document = fitz.open(pdf_path)
    page_count = document.page_count

    try:
        ManualPDFPage.objects.filter(chapter=chapter).delete()
        page_batch = []

        for page_index in range(page_count):
            page = document.load_page(page_index)

            text = extract_clean_text(page)

            page_batch.append(
                ManualPDFPage(
                    chapter=chapter,
                    page_number=page_index + 1,
                    text=text,
                )
            )

            if len(page_batch) >= 200:
                ManualPDFPage.objects.bulk_create(page_batch, batch_size=200)
                page_batch.clear()

        if page_batch:
            ManualPDFPage.objects.bulk_create(page_batch, batch_size=200)

    finally:
        document.close()

    return page_count


def index_pdf_pages_for_package(package):
    total_pages = 0
    last_chapter_id = 0

    while True:
        chapter_ids = list(
            ManualChapter.objects.filter(
                package=package,
                pk__gt=last_chapter_id,
            )
            .order_by("pk")
            .values_list("pk", flat=True)[:50]
        )

        if not chapter_ids:
            break

        chapters = ManualChapter.objects.filter(pk__in=chapter_ids).order_by("pk")

        for chapter in chapters:
            total_pages += index_pdf_pages_for_chapter(chapter)

        last_chapter_id = chapter_ids[-1]

    return total_pages


def extract_pdf_revision_info(pdf_path, source_file_name="", manual_type=""):
    return extract_revision_info_from_filename(source_file_name or pdf_path)

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

        manual_file.revision_no = revision_no or manual_file.revision_no.strip()
        manual_file.revision_date_text = (
            revision_date or manual_file.revision_date_text.strip()
        )
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
                os.path.splitext(os.path.basename(common_file.file.name))[0] + ".pdf"
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
                os.path.splitext(os.path.basename(common_file.file.name))[0] + ".pdf"
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

        common_file.revision_no = common_file.revision_no.strip() or revision_no
        common_file.revision_date_text = (
            common_file.revision_date_text.strip() or revision_date
        )

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
    temp_zip = None

    try:
        if not package.zip_file or not package.zip_file.name:
            raise FileNotFoundError(
                "업로드된 ZIP 정보가 없습니다. ZIP 파일을 다시 업로드해 주세요."
            )

        if not package.zip_file.storage.exists(package.zip_file.name):
            raise FileNotFoundError(
                f"저장소에서 ZIP 파일을 찾을 수 없습니다: {package.zip_file.name}. "
                "현재 저장소에 ZIP 파일을 다시 업로드해 주세요."
            )

        os.makedirs(temp_extract_to, exist_ok=True)

        temp_zip = tempfile.NamedTemporaryFile(
            suffix=".zip",
            delete=False,
        )

        temp_zip_path = temp_zip.name

        try:
            package.zip_file.open("rb")

            for chunk in package.zip_file.chunks():
                temp_zip.write(chunk)

            temp_zip.flush()
        finally:
            package.zip_file.close()

            if temp_zip is not None and not temp_zip.closed:
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
                chapter_count = parse_index_html(
                    package=package,
                    index_html_path=index_html_path,
                )
            else:
                chapter_count = parse_pdf_files_without_index(
                    package=package,
                    extract_to=temp_extract_to,
                )

            save_package_revision_info(package, index_html_path)

            if os.path.exists(final_extract_to):
                shutil.rmtree(final_extract_to, ignore_errors=True)

            os.makedirs(os.path.dirname(final_extract_to), exist_ok=True)
            shutil.move(temp_extract_to, final_extract_to)

            package.extracted_path = final_extract_to
            package.processed = False
            package.save(update_fields=["extracted_path", "processed"])

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
        if temp_zip is not None and not temp_zip.closed:
            temp_zip.close()

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

    maintenance_message_match = re.fullmatch(
        r"maintenance\s+messages?\s*[:：]\s*(\d{2}-\d{3,5})",
        search_value,
        re.IGNORECASE,
    )

    if maintenance_message_match:
        message_number = re.escape(maintenance_message_match.group(1))
        return (
            r"(?<![0-9A-Za-z가-힣])maintenance\s+messages?\s*[:：]\s*"
            r"(?:\d{2}-\d{3,5}\s*,\s*)*"
            rf"{message_number}(?![0-9A-Za-z가-힣])"
        )

    token_chars = r"0-9A-Za-z가-힣"
    token_separator = r"[\s:：]+"

    if match_mode == "wildcard":
        literal_parts = []

        for part in search_value.split("*"):
            words = [word for word in part.split() if word]

            if words:
                literal_parts.append(
                    token_separator.join(re.escape(word) for word in words)
                )

        if not literal_parts:
            return ""

        body = r"[\s\S]{0,240}?".join(literal_parts)

        if search_value.startswith("*"):
            body = r"\S*?" + body
        else:
            body = rf"(?<![{token_chars}])" + body

        if search_value.endswith("*"):
            body += r"\S*?"
        else:
            body += rf"(?![{token_chars}])"

        return body

    tokens = [token for token in search_value.split() if token.strip()]

    if not tokens:
        return ""

    exact_words = [
        rf"(?<![{token_chars}]){re.escape(token)}(?![{token_chars}])"
        for token in tokens
    ]
    return token_separator.join(exact_words)


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


def is_reference_index_page(text):
    normalized = " ".join((text or "").lower().split())
    reference_markers = (
        "table of contents",
        "list of effective pages",
        "list of effective sections",
        "mel item - index",
        "eicas message - index",
    )

    return any(marker in normalized for marker in reference_markers)


def prefer_content_pages(pages):
    pages = list(pages)
    content_pages = [page for page in pages if not is_reference_index_page(page.text)]
    return content_pages or pages


def prefer_group_content_pages(pages, group_key):
    grouped_pages = {}

    for page in pages:
        grouped_pages.setdefault(group_key(page), []).append(page)

    preferred_pages = []

    for group in grouped_pages.values():
        preferred_pages.extend(prefer_content_pages(group))

    return preferred_pages


def get_manual_search_prefilter(search_value):
    tokens = re.findall(
        r"[0-9A-Za-z가-힣][0-9A-Za-z가-힣/_-]*",
        search_value or "",
    )

    if not tokens:
        return ""

    numeric_tokens = [
        token
        for token in tokens
        if len(token) >= 4 and any(char.isdigit() for char in token)
    ]
    candidates = numeric_tokens or tokens
    return max(candidates, key=len)


def summarize_group_content_pages(pages, group_key, page_order_key):
    grouped = {}

    iterator = pages.iterator(chunk_size=250) if hasattr(pages, "iterator") else pages

    for page in iterator:
        key = group_key(page)
        group = grouped.setdefault(
            key,
            {
                "content_count": 0,
                "content_first": None,
                "content_order": None,
                "reference_count": 0,
                "reference_first": None,
                "reference_order": None,
            },
        )
        order = page_order_key(page)
        prefix = "reference" if is_reference_index_page(page.text) else "content"
        group[f"{prefix}_count"] += 1

        if group[f"{prefix}_order"] is None or order < group[f"{prefix}_order"]:
            group[f"{prefix}_first"] = page
            group[f"{prefix}_order"] = order

    summaries = []

    for group in grouped.values():
        if group["content_count"]:
            page = group["content_first"]
            count = group["content_count"]
        else:
            page = group["reference_first"]
            count = group["reference_count"]

        if page is not None:
            page.search_match_count = count
            summaries.append(page)

    return summaries


def save_package_revision_info(package, index_html_path):
    revision_no, revision_date = extract_revision_info_from_filename(
        package.revision_source_file_name()
    )

    package.revision_no = package.revision_no.strip() or revision_no
    package.revision_date_text = package.revision_date_text.strip() or revision_date
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
