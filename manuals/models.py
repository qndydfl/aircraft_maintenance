from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
import os


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


def get_pdf_full_path(self):
    return os.path.join(settings.MEDIA_ROOT, self.pdf_relative_path)


def validate_manual_file(value):
    ext = os.path.splitext(value.name)[1].lower()

    allowed_extensions = [".pdf", ".zip", ".doc", ".docx", ".xls", ".xlsx"]

    if ext not in allowed_extensions:
        raise ValidationError("PDF, ZIP, DOC, DOCX, XLS, 또는 XLSX 파일만 업로드할 수 있습니다.")


class Aircraft(models.Model):
    MAKER_CHOICES = [
        ("BOEING", "Boeing"),
        ("AIRBUS", "Airbus"),
    ]

    name = models.CharField(max_length=50)

    maker = models.CharField(max_length=20, choices=MAKER_CHOICES, default="BOEING")

    airbus_url = models.URLField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["maker", "name"]

    def __str__(self):
        return f"{self.maker} - {self.name}"

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

        manual_types = ["AMM", "FIM", "IPC", "MEL", "CDL", "OTHER"]

        result = []

        for manual_type in manual_types:
            package = self.manual_packages.filter(manual_type=manual_type).first()
            file = self.manuals.filter(manual_type=manual_type).first()

            result.append(
                {
                    "type": manual_type,
                    "package": package,
                    "file": file,
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
    MANUAL_TYPE_CHOICES = [
        ("AMM", "AMM"),
        ("FIM", "FIM"),
        ("IPC", "IPC"),
        ("MEL", "MEL"),
        ("CDL", "CDL"),
        ("OTHER", "Other"),
    ]

    aircraft = models.ForeignKey(
        Aircraft, on_delete=models.CASCADE, related_name="manuals"
    )

    manual_type = models.CharField(max_length=20, choices=MANUAL_TYPE_CHOICES)

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
    MANUAL_TYPE_CHOICES = [
        ("AMM", "AMM"),
        ("FIM", "FIM"),
        ("IPC", "IPC"),
        ("OTHER", "Other"),
    ]

    aircraft = models.ForeignKey(
        Aircraft, on_delete=models.CASCADE, related_name="manual_packages"
    )

    manual_type = models.CharField(max_length=20, choices=MANUAL_TYPE_CHOICES)

    zip_file = models.FileField(upload_to="manual_packages/")

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

    def __str__(self):
        return f"{self.aircraft.name} - {self.manual_type}"


class ManualChapter(models.Model):
    package = models.ForeignKey(
        ManualPackage, on_delete=models.CASCADE, related_name="chapters"
    )

    task = models.CharField(max_length=100)

    subtask = models.CharField(max_length=100, blank=True)

    title = models.CharField(max_length=255, blank=True)

    pdf_relative_path = models.CharField(max_length=500)

    created_at = models.DateTimeField(auto_now_add=True)

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
        ordering = ["title"]

    def __str__(self):
        return f"{self.category} / {self.title}"

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