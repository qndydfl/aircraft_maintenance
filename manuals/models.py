from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
import os

from .revision_utils import extract_revision_info_from_filename
from .constants import AIRCRAFT_MANUAL_TYPES


def get_filename_revision_info(file_field):
    if not file_field:
        return "", ""

    return extract_revision_info_from_filename(file_field.name)


def get_display_revision_info(instance, file_field):
    filename_revision_no, filename_revision_date = get_filename_revision_info(
        file_field
    )

    return (
        (getattr(instance, "revision_no", "") or "").strip()
        or filename_revision_no,
        (getattr(instance, "revision_date_text", "") or "").strip()
        or filename_revision_date,
    )


def build_snippet_from_text(text, query, length=180):
    if not text:
        return ""

    if not query:
        return text[:length]

    text_lower = text.lower()

    if isinstance(query, (list, tuple)):
        words = [str(word).lower().strip() for word in query if str(word).strip()]
    else:
        query_text = str(query).lower().replace("*", " ")
        words = [word.strip() for word in query_text.split() if word.strip()]

    first_index = -1

    for word in words:
        index = text_lower.find(word)

        if index != -1:
            first_index = index
            break

    if first_index == -1:
        return text[:length]

    start = max(first_index - 80, 0)
    end = min(first_index + length, len(text))

    snippet = text[start:end]

    if start > 0:
        snippet = "..." + snippet

    if end < len(text):
        snippet = snippet + "..."

    return snippet


def validate_manual_file(value):
    ext = os.path.splitext(value.name)[1].lower()

    allowed_extensions = [".pdf", ".zip", ".doc", ".docx", ".xls", ".xlsx"]

    if ext not in allowed_extensions:
        raise ValidationError("PDF, ZIP, DOC, DOCX, XLS, 또는 XLSX 파일만 업로드할 수 있습니다.")


class ManualType(models.Model):
    UPLOAD_ZIP = "ZIP"
    UPLOAD_PDF = "PDF"
    UPLOAD_FOLDER = "FOLDER"

    UPLOAD_MODE_CHOICES = [
        (UPLOAD_ZIP, "ZIP package"),
        (UPLOAD_PDF, "PDF file"),
        (UPLOAD_FOLDER, "Folder"),
    ]

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    upload_mode = models.CharField(max_length=20, choices=UPLOAD_MODE_CHOICES)
    sort_order = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)
    is_searchable = models.BooleanField(default=True)
    aircraft = models.ManyToManyField(
        "Aircraft",
        blank=True,
        related_name="manual_types",
        help_text="적용할 항공기. 비워두면 모든 항공기에 적용됩니다.",
    )

    class Meta:
        ordering = ["sort_order", "code"]

    def applies_to(self, aircraft):
        ids = {a.pk for a in self.aircraft.all()}
        return not ids or aircraft.pk in ids

    def clean(self):
        self.code = (self.code or "").strip().upper()
        self.name = (self.name or self.code).strip()

        if self.upload_mode == self.UPLOAD_FOLDER and self.code != "OTHER":
            raise ValidationError("FOLDER 방식은 현재 OTHER 유형에서만 사용할 수 있습니다.")

    def save(self, *args, **kwargs):
        self.code = (self.code or "").strip().upper()
        self.name = (self.name or self.code).strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code


class Aircraft(models.Model):
    MAKER_CHOICES = [
        ("BOEING", "Boeing"),
        ("AIRBUS", "Airbus"),
    ]

    name = models.CharField(max_length=50)

    maker = models.CharField(max_length=20, choices=MAKER_CHOICES, default="BOEING")

    airbus_url = models.URLField(blank=True, null=True)

    image = models.ImageField(
        upload_to="aircraft_images/",
        blank=True,
        null=True,
        help_text="Aircraft image shown on the Manual Library card.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["maker", "name"]

    def __str__(self):
        return f"{self.maker} - {self.name}"

    @property
    def image_url(self):
        return self.image.url if self.image else ""

    def get_ordered_manuals(self):
        manuals = list(self.manuals.all())

        return sorted(manuals, key=lambda manual: manual.manual_order())

    def get_amm(self):
        return self.get_manual_by_type("AMM")

    def get_fim(self):
        return self.get_manual_by_type("FIM")

    def get_ipc(self):
        return self.get_manual_by_type("IPC")

    def get_mel(self):
        return self.get_manual_by_type("MEL")

    def get_cdl(self):
        return self.get_manual_by_type("CDL")

    def get_wdm(self):
        return self.get_package_by_type("WDM")

    def get_ssm(self):
        return self.get_package_by_type("SSM")

    def has_manual(self, manual_type):
        has_file = any(
            manual.manual_type == manual_type for manual in self.manuals.all()
        )
        has_package = any(
            package.manual_type == manual_type for package in self.manual_packages.all()
        )

        return has_file or has_package

    def has_amm(self):
        return self.has_manual("AMM")

    def has_fim(self):
        return self.has_manual("FIM")

    def has_ipc(self):
        return self.has_manual("IPC")

    def has_mel(self):
        return self.has_manual("MEL")

    def has_cdl(self):
        return self.has_manual("CDL")

    def has_wdm(self):
        return self.has_manual("WDM")

    def has_ssm(self):
        return self.has_manual("SSM")

    def has_any_manual(self):
        return self.manuals.exists() or self.manual_packages.exists()

    def get_manual_by_type(self, manual_type):
        return next(
            (
                manual
                for manual in self.manuals.all()
                if manual.manual_type == manual_type
            ),
            None,
        )

    def get_manual_status_list(self):
        all_definitions = list(
            ManualType.objects.filter(is_active=True).prefetch_related("aircraft")
        )
        definitions = [d for d in all_definitions if d.applies_to(self)]

        if not all_definitions:
            definitions = [
                type(
                    "FallbackManualType",
                    (),
                    {
                        "code": code,
                        "name": code,
                        "upload_mode": (
                            ManualType.UPLOAD_ZIP
                            if code in {"AMM", "FIM", "IPC", "WDM", "SSM"}
                            else ManualType.UPLOAD_PDF
                            if code in {"MEL", "CDL"}
                            else ManualType.UPLOAD_FOLDER
                        ),
                    },
                )()
                for code in AIRCRAFT_MANUAL_TYPES
            ]

        packages = {
            package.manual_type: package for package in self.manual_packages.all()
        }
        files = {manual.manual_type: manual for manual in self.manuals.all()}
        other_category = next(iter(self.other_categories.all()), None)
        other_available = bool(
            other_category and any(True for _ in other_category.files.all())
        )
        result = []

        for definition in definitions:
            package = packages.get(definition.code)
            file = files.get(definition.code)
            available = bool(package or file)

            if definition.code == "OTHER":
                available = other_available

            result.append(
                {
                    "type": definition.code,
                    "name": definition.name,
                    "upload_mode": definition.upload_mode,
                    "package": package,
                    "file": file,
                    "available": available,
                }
            )

        return result

    def get_package_by_type(self, manual_type):
        return next(
            (
                package
                for package in self.manual_packages.all()
                if package.manual_type == manual_type
            ),
            None,
        )
    
    @property
    def has_boarding(self):
        return self.manuals.filter(manual_type="BOARDING").exists()


class ManualFile(models.Model):
    aircraft = models.ForeignKey(
        Aircraft, on_delete=models.CASCADE, related_name="manuals"
    )

    manual_type = models.CharField(max_length=20)

    file = models.FileField(upload_to="manuals/", validators=[validate_manual_file])

    description = models.TextField(blank=True)

    uploaded_at = models.DateTimeField(auto_now_add=True)

    revision_no = models.CharField(max_length=50, blank=True, default="")

    revision_date_text = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        ordering = ["aircraft", "manual_type"]
        verbose_name = "Manual File"
        verbose_name_plural = "Manual Files"
        unique_together = [["aircraft", "manual_type"]]

    def indexed_page_count(self):
        return self.pdf_pages.count()

    def is_indexed_pdf(self):
        return self.indexed_page_count() > 0

    @property
    def display_revision_no(self):
        revision_no, _ = get_display_revision_info(self, self.file)
        return revision_no

    @property
    def display_revision_date_text(self):
        _, revision_date = get_display_revision_info(self, self.file)
        return revision_date

    def manual_order(self):
        order_map = {
            "AMM": 1,
            "FIM": 2,
            "IPC": 3,
            "MEL": 4,
            "CDL": 5,
        }

        return order_map.get(self.manual_type, 99)


class AirbusManualLink(models.Model):
    MANUAL_TYPE_CHOICES = [
        ("AMM", "AMM"),
        ("FIM", "FIM"),
        ("IPC", "IPC"),
        ("OTHER", "Other"),
    ]

    aircraft = models.ForeignKey(
        Aircraft,
        on_delete=models.CASCADE,
        related_name="airbus_links",
    )
    manual_type = models.CharField(
        max_length=20,
        choices=MANUAL_TYPE_CHOICES,
    )

    title = models.CharField(max_length=100, blank=True)
    url = models.URLField()
    description = models.TextField(blank=True)

    class Meta:
        unique_together = ("aircraft", "manual_type")

    def __str__(self):
        return f"{self.aircraft.name} {self.manual_type}"


class ManualPackage(models.Model):
    aircraft = models.ForeignKey(
        Aircraft, on_delete=models.CASCADE, related_name="manual_packages"
    )

    manual_type = models.CharField(max_length=20)

    zip_file = models.FileField(upload_to="manual_packages/")

    original_zip_file_name = models.CharField(max_length=255, blank=True, default="")

    extracted_path = models.CharField(max_length=500, blank=True)

    uploaded_at = models.DateTimeField(auto_now_add=True)

    processed = models.BooleanField(default=False)

    revision_no = models.CharField(max_length=50, blank=True, default="")

    revision_date_text = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        ordering = ["aircraft", "manual_type"]

        unique_together = [["aircraft", "manual_type"]]

    def chapter_count(self):
        annotated_count = getattr(self, "chapter_total", None)

        if annotated_count is not None:
            return annotated_count

        return self.chapters.count()

    def page_count(self):
        annotated_count = getattr(self, "page_total", None)

        if annotated_count is not None:
            return annotated_count

        return self.chapters.aggregate(total=models.Count("pages")).get("total") or 0

    def revision_source_file_name(self):
        chapters = self.chapters.exclude(pdf_relative_path="").order_by(
            "task",
            "subtask",
            "pdf_relative_path",
        )

        first_pdf_name = ""

        for chapter in chapters:
            if not first_pdf_name:
                first_pdf_name = chapter.pdf_relative_path

            revision_no, revision_date = extract_revision_info_from_filename(
                chapter.pdf_relative_path
            )

            if revision_no or revision_date:
                return chapter.pdf_relative_path

        zip_file_names = [
            self.original_zip_file_name,
            self.zip_file.name if self.zip_file else "",
        ]

        for zip_file_name in zip_file_names:
            revision_no, revision_date = extract_revision_info_from_filename(
                zip_file_name
            )

            if revision_no or revision_date:
                return zip_file_name

        return first_pdf_name or next((name for name in zip_file_names if name), "")

    @property
    def display_revision_no(self):
        if self.revision_no.strip():
            return self.revision_no.strip()

        revision_no, _ = extract_revision_info_from_filename(
            self.revision_source_file_name()
        )
        return revision_no

    @property
    def display_revision_date_text(self):
        if self.revision_date_text.strip():
            return self.revision_date_text.strip()

        _, revision_date = extract_revision_info_from_filename(
            self.revision_source_file_name()
        )
        return revision_date

    def __str__(self):
        return f"{self.aircraft.name} - {self.manual_type}"


class ManualPackageFile(models.Model):
    package = models.ForeignKey(
        ManualPackage,
        on_delete=models.CASCADE,
        related_name="package_files",
    )
    relative_path = models.CharField(max_length=500)
    folder_path = models.CharField(max_length=500, blank=True, default="")
    file_name = models.CharField(max_length=255)
    extension = models.CharField(max_length=20, blank=True, default="")
    file_size = models.PositiveBigIntegerField(default=0)
    preview_pdf_relative_path = models.CharField(
        max_length=500,
        blank=True,
        default="",
    )

    class Meta:
        ordering = ["folder_path", "file_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["package", "relative_path"],
                name="unique_manual_package_file_path",
            )
        ]

    @property
    def is_pdf(self):
        return self.extension.lower() == ".pdf"

    @property
    def is_image(self):
        return self.extension.lower() in {
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".bmp",
            ".tif",
            ".tiff",
        }

    @property
    def can_preview(self):
        return self.is_pdf or self.is_image or bool(self.preview_pdf_relative_path)

    def __str__(self):
        return f"{self.package} - {self.relative_path}"


class ManualChapter(models.Model):
    package = models.ForeignKey(
        ManualPackage, on_delete=models.CASCADE, related_name="chapters"
    )

    task = models.CharField(max_length=100)

    subtask = models.CharField(max_length=100, blank=True)

    title = models.CharField(max_length=255, blank=True)

    pdf_relative_path = models.CharField(max_length=500)

    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def display_revision_no(self):
        return self.package.display_revision_no

    @property
    def display_revision_date_text(self):
        return self.package.display_revision_date_text

    def get_pdf_full_path(self):
        if self.package and self.package.extracted_path:
            return os.path.join(
                self.package.extracted_path,
                self.pdf_relative_path,
            )

        return os.path.join(
            settings.MEDIA_ROOT,
            self.pdf_relative_path,
        )

    class Meta:
        ordering = ["task", "subtask"]

    def __str__(self):
        return f"{self.package} - {self.task} {self.subtask}"


class ManualPDFPage(models.Model):
    chapter = models.ForeignKey(
        ManualChapter, on_delete=models.CASCADE, related_name="pages"
    )

    page_number = models.PositiveIntegerField()

    text = models.TextField(blank=True)

    class Meta:
        ordering = ["chapter", "page_number"]

        unique_together = [["chapter", "page_number"]]

    def get_snippet(self, query, length=180):
        return build_snippet_from_text(self.text, query, length)

    def __str__(self):
        return f"{self.chapter} - Page {self.page_number}"


class ManualFilePDFPage(models.Model):
    manual_file = models.ForeignKey(
        ManualFile, on_delete=models.CASCADE, related_name="pdf_pages"
    )

    page_number = models.PositiveIntegerField()

    text = models.TextField(blank=True)

    class Meta:
        ordering = [
            "manual_file",
            "page_number",
        ]

        unique_together = [["manual_file", "page_number"]]

    def get_snippet(self, query, length=180):
        return build_snippet_from_text(self.text, query, length)

    def __str__(self):
        return f"{self.manual_file} - Page {self.page_number}"


class CommonManualCategory(models.Model):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    image = models.ImageField(
        upload_to="common_manual_images/",
        blank=True,
        null=True,
        help_text="Image shown on the Common Manuals library card.",
    )

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


class CommonManualFile(models.Model):
    category = models.ForeignKey(
        CommonManualCategory,
        on_delete=models.CASCADE,
        related_name="files",
    )

    title = models.CharField(max_length=150)
    file = models.FileField(upload_to="common_manuals/")
    pdf_file = models.FileField(
        upload_to="common_manuals/pdf/",
        blank=True,
        null=True,
    )
    description = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    revision_no = models.CharField(max_length=50, blank=True, default="")
    revision_date_text = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        ordering = ["category", "title"]

    def __str__(self):
        return f"{self.category.name} - {self.title}"

    def indexed_page_count(self):
        return self.pdf_pages.count()

    def is_indexed_pdf(self):
        return self.indexed_page_count() > 0

    @property
    def display_revision_no(self):
        revision_no, _ = get_display_revision_info(self, self.file)
        return revision_no

    @property
    def display_revision_date_text(self):
        _, revision_date = get_display_revision_info(self, self.file)
        return revision_date


class CommonManualPDFPage(models.Model):
    common_file = models.ForeignKey(
        CommonManualFile,
        on_delete=models.CASCADE,
        related_name="pdf_pages",
    )

    page_number = models.PositiveIntegerField()
    text = models.TextField(blank=True)

    class Meta:
        ordering = ["common_file", "page_number"]
        unique_together = [["common_file", "page_number"]]

    def get_snippet(self, query, length=180):
        return build_snippet_from_text(self.text, query, length)

    def __str__(self):
        return f"{self.common_file} - Page {self.page_number}"
    

class OtherManualCategory(models.Model):
    aircraft = models.ForeignKey(
        Aircraft,
        on_delete=models.CASCADE,
        related_name="other_categories",
    )
    name = models.CharField(max_length=100, default="OTHER")
    code = models.CharField(max_length=60, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["aircraft__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["aircraft"],
                name="unique_other_folder_per_aircraft",
            )
        ]

    def __str__(self):
        return f"{self.aircraft.name} / OTHER"


class OtherManualFile(models.Model):
    category = models.ForeignKey(
        OtherManualCategory,
        on_delete=models.CASCADE,
        related_name="files",
    )
    title = models.CharField(max_length=200)
    relative_path = models.CharField(max_length=500, blank=True, default="")
    folder_path = models.CharField(max_length=500, blank=True, default="")
    file = models.FileField(upload_to="manuals/other/")
    converted_pdf = models.FileField(
        upload_to="manuals/other/converted/",
        blank=True,
        null=True,
    )
    description = models.TextField(blank=True)
    revision_no = models.CharField(max_length=50, blank=True)
    revision_date_text = models.CharField(max_length=100, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["folder_path", "title"]

    def __str__(self):
        return f"{self.category} / {self.title}"

    @property
    def display_revision_no(self):
        revision_no, _ = get_display_revision_info(self, self.file)
        return revision_no

    @property
    def display_revision_date_text(self):
        _, revision_date = get_display_revision_info(self, self.file)
        return revision_date

    @property
    def is_pdf(self):
        if self.converted_pdf:
            return True

        if not self.file:
            return False

        return self.file.name.lower().endswith(".pdf")

    @property
    def pdf_file(self):
        if self.converted_pdf:
            return self.converted_pdf

        if self.file and self.file.name.lower().endswith(".pdf"):
            return self.file

        return None

    @property
    def indexed_page_count(self):
        return self.pages.count()

    @property
    def is_indexed_pdf(self):
        return self.is_pdf and self.indexed_page_count > 0


class OtherManualPDFPage(models.Model):
    other_file = models.ForeignKey(
        OtherManualFile,
        on_delete=models.CASCADE,
        related_name="pages",
    )
    page_number = models.PositiveIntegerField()
    text = models.TextField(blank=True)

    class Meta:
        ordering = ["other_file", "page_number"]
        unique_together = ("other_file", "page_number")

    def __str__(self):
        return f"{self.other_file.title} - Page {self.page_number}"

    def get_snippet(self, query, radius=80):
        if not self.text or not query:
            return ""

        text_lower = self.text.lower()
        query_lower = query.lower()

        index = text_lower.find(query_lower)

        if index == -1:
            return self.text[:radius * 2]

        start = max(index - radius, 0)
        end = min(index + len(query) + radius, len(self.text))

        return self.text[start:end]


class ReindexJob(models.Model):
    TARGET_MANUAL_PACKAGE = "manual_package"
    TARGET_MANUAL_FILE = "manual_file"
    TARGET_COMMON_FILE = "common_file"
    TARGET_OTHER_FILE = "other_file"

    TARGET_CHOICES = [
        (TARGET_MANUAL_PACKAGE, "Manual Package"),
        (TARGET_MANUAL_FILE, "Manual File"),
        (TARGET_COMMON_FILE, "Common File"),
        (TARGET_OTHER_FILE, "Other File"),
    ]

    STATUS_QUEUED = "queued"
    STATUS_PROCESSING = "processing"
    STATUS_DONE = "done"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_DONE, "Done"),
        (STATUS_FAILED, "Failed"),
    ]

    target_type = models.CharField(max_length=30, choices=TARGET_CHOICES)
    target_id = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_QUEUED,
        db_index=True,
    )
    message = models.CharField(max_length=255, blank=True, default="")
    page_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["target_type", "target_id", "-created_at"],
                name="manuals_rei_target_f70ac5_idx",
            ),
            models.Index(
                fields=["status", "created_at"],
                name="manuals_rei_status_9b0c60_idx",
            ),
        ]

    def __str__(self):
        return f"{self.target_type}:{self.target_id} {self.status}"

    @property
    def is_active(self):
        return self.status in {
            self.STATUS_QUEUED,
            self.STATUS_PROCESSING,
        }
