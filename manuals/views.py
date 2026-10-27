import os, shutil, json, re, logging, time, mimetypes, tempfile
from datetime import timedelta

from django.urls import reverse_lazy, reverse
from django.conf import settings
from django.views.generic import (
    TemplateView,
    ListView,
    DetailView,
    CreateView,
    UpdateView,
    DeleteView,
    View,
    FormView,
)
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

from .models import (
    Aircraft,
    ManualFile,
    ManualPackage,
    ManualPackageFile,
    ManualChapter,
    ManualPDFPage,
    ManualFilePDFPage,
    CommonManualCategory,
    CommonManualFile,
    CommonManualPDFPage,
    OtherManualFile,
    OtherManualCategory,
    OtherManualPDFPage,
    ReindexJob,
    ManualType,
)
from .forms import (
    ManualFileForm,
    AircraftForm,
    ManualPackageForm,
    CommonManualCategoryForm,
    CommonManualFileForm,
    OtherManualCategoryForm,
    OtherManualFileForm,
    OtherManualFolderUploadForm,
    DOCUMENT_EXTENSIONS,
    IMAGE_EXTENSIONS,
)
from .constants import (
    AIRCRAFT_MANUAL_TYPES,
    PDF_MANUAL_TYPES,
    ZIP_MANUAL_TYPES,
)

from django.contrib import messages
from .services import (
    calculate_package_score,
    calculate_file_score,
    safe_int,
    parse_manual_search_query,
    build_manual_text_regex,
    build_snippet_from_regex,
    prefer_content_pages,
    prefer_group_content_pages,
    get_manual_search_prefilter,
    summarize_group_content_pages,
    index_pdf_pages_for_common_manual_file_safely,
    index_pdf_pages_for_manual_file_safely,
    index_pdf_pages_for_other_manual_file_safely,
    process_manual_package_safely,
    find_index_html,
    extract_ipc_chapter_titles,
    safe_extract_zip,
)
from django.http import FileResponse, Http404
from django.core.files import File
from django.db.models import Q, Count, Min, Prefetch
from django.shortcuts import redirect, get_object_or_404
from dispatch.services import extract_mel_dispatch_items_from_pdf
from django.utils.dateparse import parse_date
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.clickjacking import xframe_options_sameorigin
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

from .merged_package import load_merged_package, merged_pdf_response


def refresh_uploaded_at(instance):
    instance.uploaded_at = timezone.now()
    instance.save(update_fields=["uploaded_at"])


def delete_file_field(file_field):
    if file_field:
        file_field.delete(save=False)


def queue_reindex_job(target_type, target_id, message=""):
    existing_job = ReindexJob.objects.filter(
        target_type=target_type,
        target_id=target_id,
        status__in=[
            ReindexJob.STATUS_QUEUED,
            ReindexJob.STATUS_PROCESSING,
        ],
    ).first()

    if existing_job:
        return existing_job, False

    job = ReindexJob.objects.create(
        target_type=target_type,
        target_id=target_id,
        message=message,
    )

    return job, True


def process_reindex_job_inline(job):
    if not getattr(settings, "REINDEX_INLINE", False):
        return False

    if job.status != ReindexJob.STATUS_QUEUED:
        return False

    job.status = ReindexJob.STATUS_PROCESSING
    job.started_at = timezone.now()
    job.message = "Processing re-index job..."
    job.save(update_fields=["status", "started_at", "message"])

    try:
        if job.target_type == ReindexJob.TARGET_MANUAL_PACKAGE:
            package = ManualPackage.objects.get(pk=job.target_id)
            result = process_manual_package_safely(package)
            page_count = result["page_count"]
            message = f"{package.manual_type} re-index complete: {page_count} pages"
        elif job.target_type == ReindexJob.TARGET_MANUAL_FILE:
            manual = ManualFile.objects.get(pk=job.target_id)
            page_count = index_pdf_pages_for_manual_file_safely(manual)

            if manual.manual_type == "MEL":
                mel_count = extract_mel_dispatch_items_from_pdf(manual)
                message = (
                    f"{manual.manual_type} re-index complete: {page_count} pages, "
                    f"{mel_count} MEL messages"
                )
            else:
                message = f"{manual.manual_type} re-index complete: {page_count} pages"
        elif job.target_type == ReindexJob.TARGET_COMMON_FILE:
            common_file = CommonManualFile.objects.get(pk=job.target_id)
            page_count = index_pdf_pages_for_common_manual_file_safely(common_file)
            message = f"Common manual re-index complete: {page_count} pages"
        elif job.target_type == ReindexJob.TARGET_OTHER_FILE:
            other_file = OtherManualFile.objects.get(pk=job.target_id)
            page_count = index_pdf_pages_for_other_manual_file_safely(other_file)
            message = f"OTHER file re-index complete: {page_count} pages"
        else:
            raise ValueError(f"Unsupported job target type: {job.target_type}")

        job.status = ReindexJob.STATUS_DONE
        job.page_count = page_count
        job.message = message
        job.finished_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "page_count",
                "message",
                "finished_at",
            ]
        )
        return True
    except Exception as error:
        job.status = ReindexJob.STATUS_FAILED
        job.message = str(error)[:255]
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "message", "finished_at"])
        raise


def queue_reindex_message(request, target_type, target_id, label):
    job, created = queue_reindex_job(
        target_type,
        target_id,
        message=f"{label} indexing queued.",
    )

    if created or getattr(settings, "REINDEX_INLINE", False):
        if not created and job.status == ReindexJob.STATUS_PROCESSING:
            job.status = ReindexJob.STATUS_QUEUED
            job.started_at = None
            job.finished_at = None
            job.message = f"{label} indexing queued."
            job.save(
                update_fields=[
                    "status",
                    "started_at",
                    "finished_at",
                    "message",
                ]
            )

        try:
            processed_inline = process_reindex_job_inline(job)
        except Exception as error:
            messages.error(
                request,
                f"{label} 인덱싱 실패: {error}",
            )
            return job

        if processed_inline:
            messages.success(
                request,
                f"{label} 인덱싱을 완료했습니다.",
            )
        else:
            messages.success(
                request,
                f"{label} 업로드 완료. 인덱싱은 백그라운드에서 진행됩니다.",
            )
    else:
        messages.info(
            request,
            f"{label} 업로드 완료. 이미 진행 중인 인덱싱 작업이 있습니다.",
        )

    return job


def attach_latest_reindex_jobs(items, item_getter, target_type):
    item_list = list(items)
    target_ids = [
        item_getter(item).pk for item in item_list if item_getter(item) is not None
    ]

    if not target_ids:
        return item_list

    jobs = ReindexJob.objects.filter(
        target_type=target_type,
        target_id__in=target_ids,
    ).order_by("target_type", "target_id", "-created_at")

    latest_by_target = {}

    for job in jobs:
        if job.target_id not in latest_by_target:
            latest_by_target[job.target_id] = job

    for item in item_list:
        target = item_getter(item)

        if target is not None:
            target.latest_reindex_job = latest_by_target.get(target.pk)

    return item_list


class StaffRequiredMixin(UserPassesTestMixin):

    def test_func(self):
        return self.request.user.is_staff


class R2DirectUploadContextMixin:
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["use_r2_direct_upload"] = bool(getattr(settings, "USE_R2", False))
        return context


def get_manual_library_context():
    return {
        "boeing_aircrafts": Aircraft.objects.filter(maker="BOEING")
        .order_by("name")
        .prefetch_related(
            "manuals",
            "manual_packages",
            "other_categories",
            "other_categories__files",
        ),
        "airbus_aircrafts": Aircraft.objects.filter(maker="AIRBUS")
        .order_by("name")
        .prefetch_related(
            "manuals",
            "manual_packages",
            "other_categories",
            "other_categories__files",
        ),
        "common_categories": CommonManualCategory.objects.prefetch_related("files"),
    }


def get_aircraft_manual_items(aircraft):
    """Build the same manual status list used by the aircraft detail page."""
    manual_items = aircraft.get_manual_status_list()
    manual_items = attach_latest_reindex_jobs(
        manual_items,
        lambda item: item.get("package"),
        ReindexJob.TARGET_MANUAL_PACKAGE,
    )
    return attach_latest_reindex_jobs(
        manual_items,
        lambda item: item.get("file"),
        ReindexJob.TARGET_MANUAL_FILE,
    )


class PortalHomeView(LoginRequiredMixin, TemplateView):
    """
    로그인 후 처음 표시되는 Manual Portal 메인 Dashboard.
    """

    template_name = "portal_home.html"


class ManualHomeView(LoginRequiredMixin, TemplateView):
    template_name = "manual_library.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(get_manual_library_context())
        return context


class AircraftManualDetailView(LoginRequiredMixin, DetailView):
    model = Aircraft
    template_name = "manuals/aircraft_manual_detail.html"
    context_object_name = "aircraft"

    def get_queryset(self):
        package_queryset = ManualPackage.objects.annotate(
            chapter_total=Count("chapters", distinct=True),
            page_total=Count("chapters__pages", distinct=True),
        )

        return Aircraft.objects.order_by("name").prefetch_related(
            "manuals",
            Prefetch("manual_packages", queryset=package_queryset),
            "other_categories",
            "other_categories__files",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(get_manual_library_context())
        manual_items = get_aircraft_manual_items(self.object)
        context["manual_items"] = manual_items
        context["manual_type_summary"] = " / ".join(
            item["type"] for item in manual_items
        )
        return context


class DateCalculatorView(LoginRequiredMixin, TemplateView):
    template_name = "manuals/calculator.html"

    fixed_days = [3, 10, 120, 240]
    english_months = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]

    def format_datetime_parts_en(self, value, suffix=""):
        time_text = f"{value:%H:%M:%S}"

        if suffix:
            time_text = f"{time_text} {suffix}"

        return {
            "date": (
                f"{value.day:02d} "
                f"{self.english_months[value.month - 1]} "
                f"{value.year}"
            ),
            "time": time_text,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()
        today = now.date()
        base_date = parse_date(self.request.GET.get("base_date", "")) or today
        selected_days = safe_int(self.request.GET.get("days"), 0)

        if selected_days < 0:
            selected_days = 0

        target_date = base_date + timedelta(days=selected_days)
        target_date_en = (
            f"{target_date.day:02d} "
            f"{self.english_months[target_date.month - 1]} "
            f"{target_date.year}"
        )
        local_now = timezone.localtime(now)
        local_now_parts = self.format_datetime_parts_en(local_now, "KST")
        utc_now_parts = self.format_datetime_parts_en(now, "UTC")

        context.update(
            {
                "today": today,
                "base_date": base_date,
                "fixed_days": self.fixed_days,
                "selected_days": selected_days,
                "target_date": target_date,
                "target_date_en": target_date_en,
                "local_now_date": local_now_parts["date"],
                "local_now_time": local_now_parts["time"],
                "utc_now_date": utc_now_parts["date"],
                "utc_now_time": utc_now_parts["time"],
            }
        )

        return context


class ManualFileCreateView(
    R2DirectUploadContextMixin,
    LoginRequiredMixin,
    StaffRequiredMixin,
    CreateView,
):
    model = ManualFile
    form_class = ManualFileForm
    template_name = "manuals/manual_file_form.html"

    def get_initial(self):
        initial = super().get_initial()

        aircraft_id = self.request.GET.get("aircraft")
        manual_type = self.request.GET.get("manual_type")

        if aircraft_id:
            initial["aircraft"] = aircraft_id

        if manual_type:
            initial["manual_type"] = manual_type

        return initial

    def get_form(self, form_class=None):
        form = super().get_form(form_class)

        if self.request.GET.get("aircraft"):
            form.fields["aircraft"].disabled = True

        if self.request.GET.get("manual_type"):
            form.fields["manual_type"].disabled = True

        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        aircraft_id = self.request.GET.get("aircraft")

        if aircraft_id:
            context["back_url"] = reverse_lazy(
                "aircraft_manual_detail",
                kwargs={"pk": aircraft_id},
            )
        else:
            context["back_url"] = reverse_lazy("home")

        return context

    def form_valid(self, form):
        response = super().form_valid(form)

        queue_reindex_message(
            self.request,
            ReindexJob.TARGET_MANUAL_FILE,
            self.object.pk,
            f"{self.object.manual_type} PDF",
        )

        return response

    def get_success_url(self):
        return reverse_lazy(
            "aircraft_manual_detail",
            kwargs={"pk": self.object.aircraft.pk},
        )


class ManualFileUpdateView(
    R2DirectUploadContextMixin,
    LoginRequiredMixin,
    StaffRequiredMixin,
    UpdateView,
):
    model = ManualFile
    form_class = ManualFileForm
    template_name = "manuals/manual_file_form.html"
    context_object_name = "manual"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["back_url"] = reverse_lazy(
            "aircraft_manual_detail",
            kwargs={"pk": self.object.aircraft.pk},
        )

        return context

    def form_valid(self, form):
        old_file = None
        file_replaced = bool(
            self.request.FILES.get("file") or form.cleaned_data.get("r2_object_key")
        )

        if self.object.pk:
            old_instance = ManualFile.objects.get(pk=self.object.pk)
            old_file = old_instance.file

        response = super().form_valid(form)

        if old_file and self.object.file and old_file.name != self.object.file.name:
            delete_file_field(old_file)

        if file_replaced:
            refresh_uploaded_at(self.object)
            queue_reindex_message(
                self.request,
                ReindexJob.TARGET_MANUAL_FILE,
                self.object.pk,
                f"{self.object.manual_type} PDF",
            )
        else:
            messages.success(self.request, "매뉴얼 정보를 수정했습니다.")

        return response

    def get_success_url(self):
        return reverse_lazy(
            "aircraft_manual_detail",
            kwargs={"pk": self.object.aircraft.pk},
        )


class ManualFileDeleteView(LoginRequiredMixin, StaffRequiredMixin, DeleteView):
    model = ManualFile
    template_name = "manuals/manual_file_confirm_delete.html"
    context_object_name = "manual"

    def form_valid(self, form):
        delete_file_field(self.object.file)

        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy(
            "aircraft_manual_detail",
            kwargs={"pk": self.object.aircraft.pk},
        )


class ManualPackageDeleteView(LoginRequiredMixin, StaffRequiredMixin, DeleteView):
    model = ManualPackage
    template_name = "manuals/manual_package_confirm_delete.html"
    context_object_name = "package"

    def form_valid(self, form):
        extracted_path = self.object.extracted_path

        delete_file_field(self.object.zip_file)

        if extracted_path and os.path.exists(extracted_path):
            shutil.rmtree(extracted_path, ignore_errors=True)

        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy(
            "aircraft_manual_detail",
            kwargs={"pk": self.object.aircraft.pk},
        )


class AircraftManageListView(LoginRequiredMixin, StaffRequiredMixin, ListView):
    model = Aircraft
    template_name = "manuals/aircraft_manage_list.html"
    context_object_name = "aircrafts"

    def get_queryset(self):
        return Aircraft.objects.prefetch_related(
            "manuals",
            "manual_packages",
            "other_categories__files",
        ).order_by("maker", "name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        active_tab = self.request.GET.get("tab", "aircraft").strip().lower()

        if active_tab not in {"aircraft", "common"}:
            active_tab = "aircraft"

        context["common_categories"] = CommonManualCategory.objects.prefetch_related(
            "files"
        )
        context["active_tab"] = active_tab
        context["aircraft_manage_aircraft_url"] = reverse("aircraft_manage")
        context["aircraft_manage_common_url"] = (
            f"{reverse('aircraft_manage')}?tab=common"
        )
        return context


class AircraftCreateView(LoginRequiredMixin, StaffRequiredMixin, CreateView):
    model = Aircraft
    form_class = AircraftForm
    template_name = "manuals/aircraft_form.html"
    success_url = reverse_lazy("aircraft_manage")


class AircraftUpdateView(LoginRequiredMixin, StaffRequiredMixin, UpdateView):
    model = Aircraft
    form_class = AircraftForm
    template_name = "manuals/aircraft_form.html"
    context_object_name = "aircraft"
    success_url = reverse_lazy("aircraft_manage")


class AircraftDeleteView(LoginRequiredMixin, StaffRequiredMixin, DeleteView):
    model = Aircraft
    template_name = "common/delete_confirm.html"
    context_object_name = "aircraft"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "delete_type": "AIRCRAFT",
                "delete_title": self.object.name,
                "delete_message": "기종을 삭제하시겠습니까?",
                "warning_message": "연결된 매뉴얼도 함께 제거될 수 있습니다.",
                "back_url": reverse("aircraft_manage"),
            }
        )
        return context

    def get_success_url(self):
        return reverse("aircraft_manage")


class ManualPackageCreateView(
    R2DirectUploadContextMixin,
    LoginRequiredMixin,
    StaffRequiredMixin,
    CreateView,
):
    model = ManualPackage
    form_class = ManualPackageForm
    template_name = "manuals/manual_package_form.html"

    def get_initial(self):
        initial = super().get_initial()

        aircraft_id = self.request.GET.get("aircraft")
        manual_type = self.request.GET.get("manual_type")

        if aircraft_id:
            initial["aircraft"] = aircraft_id

        if manual_type:
            initial["manual_type"] = manual_type

        return initial

    def get_form(self, form_class=None):
        form = super().get_form(form_class)

        if self.request.GET.get("aircraft"):
            form.fields["aircraft"].disabled = True

        if self.request.GET.get("manual_type"):
            form.fields["manual_type"].disabled = True

        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        aircraft_id = self.request.GET.get("aircraft")

        if aircraft_id:
            context["back_url"] = reverse_lazy(
                "aircraft_manual_detail", kwargs={"pk": aircraft_id}
            )
        else:
            context["back_url"] = reverse_lazy("home")

        return context

    def form_valid(self, form):
        uploaded_zip = self.request.FILES.get("zip_file")

        if uploaded_zip:
            form.instance.original_zip_file_name = uploaded_zip.name

        response = super().form_valid(form)

        self.object.processed = False
        self.object.save(update_fields=["processed"])

        queue_reindex_message(
            self.request,
            ReindexJob.TARGET_MANUAL_PACKAGE,
            self.object.pk,
            f"{self.object.manual_type} ZIP",
        )

        return response

    def get_success_url(self):
        return reverse_lazy(
            "aircraft_manual_detail", kwargs={"pk": self.object.aircraft.pk}
        )


class ManualChapterListView(
    LoginRequiredMixin,
    ListView,
):
    model = ManualChapter
    template_name = "manuals/manual_chapter_list.html"
    context_object_name = "chapters"

    def get_queryset(self):
        self.package = ManualPackage.objects.get(pk=self.kwargs["package_pk"])

        return ManualChapter.objects.filter(package=self.package).order_by(
            "task",
            "subtask",
        )

    def get_context_data(
        self,
        **kwargs,
    ):
        context = super().get_context_data(**kwargs)

        # =====================================================
        # Request Parameters
        # =====================================================

        q = self.request.GET.get(
            "q",
            "",
        ).strip()

        requested_chapter_id = safe_int(
            self.request.GET.get(
                "chapter",
                "",
            ),
            default=None,
        )

        requested_page_number = safe_int(
            self.request.GET.get(
                "page",
                "",
            ),
            default=None,
        )

        context["package"] = self.package
        context["q"] = q

        package_files = list(self.package.package_files.all())
        package_file_root = {"folders": {}, "files": []}

        for package_file in package_files:
            node = package_file_root

            for folder_name in filter(None, package_file.folder_path.split("/")):
                node = node["folders"].setdefault(
                    folder_name,
                    {"folders": {}, "files": []},
                )

            node["files"].append(package_file)

        def serialize_file_tree(node, parent_path=""):
            folders = []

            for folder_name, child in sorted(
                node["folders"].items(),
                key=lambda item: item[0].lower(),
            ):
                folder_path = "/".join(
                    part for part in [parent_path, folder_name] if part
                )
                folders.append(
                    {
                        "name": folder_name,
                        "path": folder_path,
                        "folders": serialize_file_tree(child, folder_path),
                        "files": sorted(
                            child["files"],
                            key=lambda item: item.file_name.lower(),
                        ),
                    }
                )

            return folders

        context["package_file_tree"] = serialize_file_tree(package_file_root)
        context["package_root_files"] = sorted(
            package_file_root["files"],
            key=lambda item: item.file_name.lower(),
        )
        context["package_file_count"] = len(package_files)

        # =====================================================
        # All Chapters
        # =====================================================

        # all_chapters = ManualChapter.objects.filter(package=self.package).order_by(
        #     "task",
        #     "subtask",
        # )

        all_chapters = list(
            ManualChapter.objects.filter(package=self.package).order_by(
                "task",
                "subtask",
            )
        )

        for chapter in all_chapters:
            viewer_params = {
                "page": 1,
                "embedded": "1",
                "back": self.request.get_full_path(),
            }

            chapter.viewer_url = (
                reverse(
                    "manual_chapter_pdf_viewer",
                    kwargs={"pk": chapter.pk},
                )
                + "?"
                + urlencode(viewer_params)
            )

        # Normal mode:
        # show all chapters.
        context["chapter_bookmarks"] = all_chapters

        # Search mode:
        # grouped PDF page results.
        context["matching_chapters"] = []

        context["total_match_count"] = 0

        # =====================================================
        # Initial Viewer State
        # =====================================================

        initial_chapter_id = None
        initial_page_number = 1

        # =====================================================
        # Search
        # =====================================================

        if q:
            (
                search_value,
                match_mode,
            ) = parse_manual_search_query(q)

            if search_value:
                text_regex = build_manual_text_regex(
                    search_value,
                    match_mode,
                )

                # =============================================
                # PDF Page Filter
                # =============================================

                if match_mode == "contains":
                    page_filter = Q(text__icontains=search_value)

                else:
                    page_filter = Q(text__iregex=text_regex)

                # =============================================
                # PDF Page Search
                # =============================================

                matching_page_records = list(
                    ManualPDFPage.objects.select_related("chapter")
                    .filter(chapter__package=self.package)
                    .filter(page_filter)
                    .only(
                        "id",
                        "chapter_id",
                        "page_number",
                        "text",
                        "chapter__task",
                        "chapter__subtask",
                        "chapter__title",
                    )
                    .order_by(
                        "chapter__task",
                        "chapter__subtask",
                        "page_number",
                    )
                )

                # =============================================
                # Keep exactly the same page set as
                # package PDF Viewer search.
                # =============================================

                matching_page_records = prefer_content_pages(matching_page_records)

                # =============================================
                # Group Results by Chapter
                # =============================================

                matching_chapter_map = {}

                for page in matching_page_records:
                    chapter = page.chapter

                    if chapter.pk not in matching_chapter_map:
                        matching_chapter_map[chapter.pk] = {
                            "pk": chapter.pk,
                            "chapter_id": chapter.pk,
                            "chapter__task": chapter.task,
                            "chapter__subtask": chapter.subtask,
                            "chapter__title": chapter.title,
                            "match_count": 0,
                            "first_page": page.page_number,
                        }

                    chapter_data = matching_chapter_map[chapter.pk]

                    chapter_data["match_count"] += 1

                    if page.page_number < chapter_data["first_page"]:
                        chapter_data["first_page"] = page.page_number

                matching_chapters = list(matching_chapter_map.values())

                # -----------------------------------------------------
                # Viewer URL은 Python에서 안전하게 생성
                # -----------------------------------------------------
                for item in matching_chapters:
                    viewer_params = {
                        "page": item["first_page"],
                        "q": q,
                        "from_search_page": "1",
                        "embedded": "1",
                        "back": self.request.get_full_path(),
                    }

                    item["viewer_url"] = (
                        reverse(
                            "manual_chapter_pdf_viewer",
                            kwargs={"pk": item["chapter_id"]},
                        )
                        + "?"
                        + urlencode(viewer_params)
                    )

                context["matching_chapters"] = matching_chapters

                # =============================================
                # Total Match Count
                # =============================================

                context["total_match_count"] = sum(
                    item["match_count"] for item in matching_chapters
                )

                # =============================================
                # Search Navigator
                #
                # Only chapters containing real PDF matches.
                # =============================================

                matching_chapter_ids = [
                    item.get("pk", item.get("chapter_id"))
                    for item in matching_chapters
                    if item.get("pk") is not None or item.get("chapter_id") is not None
                ]

                context["chapter_bookmarks"] = ManualChapter.objects.filter(
                    package=self.package,
                    pk__in=matching_chapter_ids,
                ).order_by(
                    "task",
                    "subtask",
                )

        # =====================================================
        # Chapter hierarchy
        #
        # AMM/FIM tasks such as 06___107 stay as direct entries. IPC follows
        # the Boeing index.html hierarchy: chapter heading, then PDF sections.
        # =====================================================

        manual_type = (self.package.manual_type or "").strip().upper()
        ipc_chapter_titles = {}
        amm_chapter_titles = {}

        if manual_type == "IPC":
            index_html_path = find_index_html(self.package.extracted_path)
            ipc_chapter_titles = extract_ipc_chapter_titles(index_html_path)

            amm_chapters = ManualChapter.objects.filter(
                package__aircraft=self.package.aircraft,
                package__manual_type="AMM",
            ).only("task", "title")

            for amm_chapter in amm_chapters:
                chapter_match = re.match(
                    r"^(?P<chapter>\d{2})(?:_{2,}|$)",
                    (amm_chapter.task or "").strip(),
                )
                title = (amm_chapter.title or "").strip()

                if chapter_match and title:
                    amm_chapter_titles.setdefault(
                        chapter_match.group("chapter"),
                        title,
                    )

        def build_chapter_groups(items, task_getter):
            """
            Chapter navigator grouping.

            AMM/FIM:
                기존 task를 그대로 개별 chapter로 표시.

            IPC:
                11-00___116
                11-10___...
                11-20___...
                같은 IPC section 형식만 Chapter 11 그룹으로 묶는다.

            중요한 점:
                - -00 항목을 children에서 제거하지 않는다.
                - group header는 실제 ManualChapter를 parent로 사용하지 않는다.
                - 따라서 11-00 자체도 정상적인 클릭 가능한 section으로 남는다.
            """

            items = list(items)

            # ---------------------------------------------------------
            # IPC가 아니면 기존 chapter를 그대로 표시
            # ---------------------------------------------------------

            if manual_type != "IPC":
                return [
                    {
                        "key": f"item-{index}",
                        "is_group": False,
                        "item": item,
                    }
                    for index, item in enumerate(items)
                ]

            # ---------------------------------------------------------
            # IPC
            # ---------------------------------------------------------

            entries = []
            group_map = {}

            def ipc_task_info(item):
                task = (task_getter(item) or "").strip()

                match = re.match(
                    r"^(?P<chapter>\d{2})-"
                    r"(?P<section>[0-9A-Za-z]{2})"
                    r"(?:_{2,}.*)?$",
                    task,
                    re.IGNORECASE,
                )

                if not match:
                    return None

                return {
                    "task": task,
                    "chapter": match.group("chapter"),
                    "section": match.group("section"),
                }

            def ipc_item_title(item):
                if isinstance(item, dict):
                    return (
                        item.get("chapter__title") or item.get("title") or ""
                    ).strip()

                return (getattr(item, "title", "") or "").strip()

            # AMM/index.html may not be available yet on an older deployment.
            # In that case, use the IPC 00 section title as a reliable fallback.
            ipc_section_titles = {}
            for item in items:
                info = ipc_task_info(item)
                if not info or info["section"] != "00":
                    continue

                title = ipc_item_title(item)
                description_match = re.match(
                    r"^(?:SECTION\s+)?\d{2}-00\s*[,:-]\s*(.+)$",
                    title,
                    re.IGNORECASE,
                )
                if description_match:
                    ipc_section_titles.setdefault(
                        info["chapter"],
                        f"CHAPTER {info['chapter']} - "
                        f"{description_match.group(1).strip()}",
                    )

            def ipc_sort_key(item):
                info = ipc_task_info(item)

                if not info:
                    return (
                        1,
                        10**9,
                        (task_getter(item) or "").lower(),
                    )

                section = info["section"]

                if section.lower() == "fm":
                    return (
                        0,
                        -1,
                        info["task"].lower(),
                    )

                if section.isdigit():
                    return (
                        0,
                        int(section),
                        info["task"].lower(),
                    )

                number_match = re.match(r"^(\d+)", section)

                if number_match:
                    return (
                        0,
                        int(number_match.group(1)),
                        section.lower(),
                    )

                return (
                    0,
                    10**9,
                    section.lower(),
                )

            # ---------------------------------------------------------
            # Group by ATA chapter
            #
            # 11-00
            # 11-10
            # 11-20
            #     ↓
            # Chapter 11
            # ---------------------------------------------------------

            for item in items:
                info = ipc_task_info(item)

                # IPC section 형식이 아니면 독립 항목으로 유지
                if not info:
                    entries.append(
                        {
                            "key": f"item-{len(entries)}",
                            "is_group": False,
                            "item": item,
                        }
                    )
                    continue

                chapter_no = info["chapter"]

                if chapter_no not in group_map:
                    group = {
                        "key": chapter_no,
                        "is_group": True,
                        # 중요:
                        # 실제 chapter object를 group parent로 사용하지 않는다.
                        "parent": None,
                        "chapter_no": chapter_no,
                        "label": amm_chapter_titles.get(
                            chapter_no,
                            ipc_chapter_titles.get(
                                chapter_no,
                                ipc_section_titles.get(
                                    chapter_no,
                                    f"CHAPTER {chapter_no}",
                                ),
                            ),
                        ),
                        "items": [],
                        "count": 0,
                    }

                    group_map[chapter_no] = group
                    entries.append(group)

                group_map[chapter_no]["items"].append(item)

            # ---------------------------------------------------------
            # Sort each IPC group
            # ---------------------------------------------------------

            result = []

            for entry in entries:
                if not entry["is_group"]:
                    result.append(entry)
                    continue

                entry["items"].sort(
                    key=ipc_sort_key,
                )

                entry["count"] = len(entry["items"])

                # section 하나뿐이라도 IPC chapter 구조는 유지
                result.append(entry)

            return result

        context["chapter_groups"] = build_chapter_groups(
            context["chapter_bookmarks"],
            lambda chapter: chapter.task,
        )
        context["matching_chapter_groups"] = build_chapter_groups(
            context["matching_chapters"],
            lambda chapter: chapter["chapter__task"],
        )

        # =====================================================
        # Restore Workspace URL State
        #
        # ?q=PACK&chapter=123&page=17
        # =====================================================

        requested_chapter = None

        if requested_chapter_id:
            requested_chapter = ManualChapter.objects.filter(
                pk=requested_chapter_id,
                package=self.package,
            ).first()

        if requested_chapter:
            initial_chapter_id = requested_chapter.pk

            if requested_page_number and requested_page_number >= 1:
                initial_page_number = requested_page_number

        # =====================================================
        # Keep the workspace unselected on first entry.
        # A chapter/page in the URL restores an existing selection.
        # =====================================================

        # =====================================================
        # Initial Chapter Object
        # =====================================================

        initial_chapter = None

        if initial_chapter_id:
            initial_chapter = ManualChapter.objects.filter(
                pk=initial_chapter_id,
                package=self.package,
            ).first()

        context["initial_chapter"] = initial_chapter

        # =====================================================
        # Workspace Back URL
        #
        # chapter/page are intentionally excluded.
        # =====================================================

        workspace_back_url = reverse(
            "manual_chapter_list",
            kwargs={
                "package_pk": self.package.pk,
            },
        )

        workspace_back_params = {}

        if q:
            workspace_back_params["q"] = q

        if workspace_back_params:
            workspace_back_url += "?" + urlencode(workspace_back_params)

        context["workspace_back_url"] = workspace_back_url

        # =====================================================
        # Initial Viewer URL
        # =====================================================

        context["initial_viewer_url"] = ""

        if initial_chapter:
            viewer_params = {
                "page": initial_page_number,
                "embedded": "1",
                "back": workspace_back_url,
            }

            # Package-wide search.
            #
            # IMPORTANT:
            # Do not add scope=chapter here.
            if q:
                viewer_params.update(
                    {
                        "q": q,
                        "from_search_page": "1",
                    }
                )

            context["initial_viewer_url"] = (
                reverse(
                    "manual_chapter_pdf_viewer",
                    kwargs={
                        "pk": initial_chapter.pk,
                    },
                )
                + "?"
                + urlencode(viewer_params)
            )

        return context


class ManualPackageMergedPDFView(LoginRequiredMixin, DetailView):
    model = ManualPackage

    def get(self, request, *args, **kwargs):
        from pathlib import Path

        package = self.get_object()
        root = Path(package.extracted_path)
        version = request.GET.get("version", "")
        if version:
            generation = request.GET.get("generation", root.name)
            if not re.fullmatch(r"merged-[0-9a-f]{32}\.pdf", version) or (
                generation != root.name
                and not re.fullmatch(rf"{package.pk}(?:-[0-9a-f]{{32}})?", generation)
            ):
                raise Http404("Invalid merged PDF version")
            path = root.parent / generation / version
        else:
            merged = load_merged_package(package)
            if not merged:
                raise Http404("Merged PDF not available")
            path = root / merged["file"]
        if not path.is_file():
            raise Http404("Merged PDF not available")
        return merged_pdf_response(request, path)


class ManualChapterPDFView(LoginRequiredMixin, DetailView):
    model = ManualChapter

    def get(self, request, *args, **kwargs):
        chapter = self.get_object()

        pdf_path = os.path.abspath(
            os.path.join(chapter.package.extracted_path, chapter.pdf_relative_path)
        )

        extracted_root = os.path.abspath(chapter.package.extracted_path)

        if os.path.commonpath([extracted_root, pdf_path]) != extracted_root:
            raise Http404("Invalid PDF path")

        if not os.path.exists(pdf_path):
            raise Http404("PDF file not found")

        if request.GET.get("outline") == "1":
            import fitz
            from django.http import JsonResponse

            with fitz.open(pdf_path) as document:
                outline = [
                    {"depth": level - 1, "title": title, "pageNumber": page}
                    for level, title, page in document.get_toc()
                    if page > 0
                ]
            return JsonResponse({"outline": outline})

        return FileResponse(open(pdf_path, "rb"), content_type="application/pdf")


def resolve_manual_package_file(package_file, relative_path):
    extracted_root = os.path.abspath(package_file.package.extracted_path)
    file_path = os.path.abspath(os.path.join(extracted_root, relative_path))

    try:
        if os.path.commonpath([extracted_root, file_path]) != extracted_root:
            raise Http404("Invalid file path")
    except ValueError:
        raise Http404("Invalid file path")

    if not os.path.isfile(file_path):
        raise Http404("File not found")

    return file_path


class ManualPackageFileDownloadView(LoginRequiredMixin, DetailView):
    model = ManualPackageFile

    def get(self, request, *args, **kwargs):
        package_file = self.get_object()
        file_path = resolve_manual_package_file(
            package_file,
            package_file.relative_path,
        )
        content_type = mimetypes.guess_type(package_file.file_name)[0]

        return FileResponse(
            open(file_path, "rb"),
            as_attachment=True,
            filename=package_file.file_name,
            content_type=content_type or "application/octet-stream",
        )


class ManualPackageFilePreviewView(LoginRequiredMixin, DetailView):
    model = ManualPackageFile

    def get(self, request, *args, **kwargs):
        package_file = self.get_object()

        if package_file.preview_pdf_relative_path:
            relative_path = package_file.preview_pdf_relative_path
            content_type = "application/pdf"
            preview_name = os.path.splitext(package_file.file_name)[0] + ".pdf"
        elif package_file.is_pdf or package_file.is_image:
            relative_path = package_file.relative_path
            content_type = (
                "application/pdf"
                if package_file.is_pdf
                else mimetypes.guess_type(package_file.file_name)[0]
            )
            preview_name = package_file.file_name
        else:
            raise Http404("Preview is not available")

        file_path = resolve_manual_package_file(package_file, relative_path)
        return FileResponse(
            open(file_path, "rb"),
            filename=preview_name,
            content_type=content_type or "application/octet-stream",
        )


class ManualSearchView(LoginRequiredMixin, TemplateView):
    template_name = "manuals/manual_search.html"

    def get(self, request, *args, **kwargs):
        started_at = time.perf_counter()

        try:
            response = super().get(request, *args, **kwargs)
        except Exception:
            logger.exception(
                "Manual search failed after %.2fs (aircraft=%s, manual_types=%s)",
                time.perf_counter() - started_at,
                request.GET.get("aircraft", ""),
                request.GET.getlist("manual_type"),
            )
            raise

        logger.info(
            "Manual search completed in %.2fs (aircraft=%s, manual_types=%s)",
            time.perf_counter() - started_at,
            request.GET.get("aircraft", ""),
            request.GET.getlist("manual_type"),
        )
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        query = self.request.GET.get("q", "").strip()
        aircraft_id = self.request.GET.get("aircraft", "").strip()
        manual_type_definitions = list(
            ManualType.objects.filter(is_active=True, is_searchable=True)
        )
        manual_types = [definition.code for definition in manual_type_definitions]

        if not manual_types:
            manual_types = list(AIRCRAFT_MANUAL_TYPES)

        manual_types.append("COMMON")
        zip_manual_types = {
            definition.code
            for definition in manual_type_definitions
            if definition.upload_mode == ManualType.UPLOAD_ZIP
        } or set(ZIP_MANUAL_TYPES)
        pdf_manual_types = {
            definition.code
            for definition in manual_type_definitions
            if definition.upload_mode == ManualType.UPLOAD_PDF
        } or set(PDF_MANUAL_TYPES)
        selected_manual_types = []

        for value in self.request.GET.getlist("manual_type"):
            for manual_type in value.split(","):
                manual_type = manual_type.strip().upper()

                if (
                    manual_type in manual_types
                    and manual_type not in selected_manual_types
                ):
                    selected_manual_types.append(manual_type)

        package_pages = []
        file_pages = []
        common_pages = []
        other_pages = []

        if query:
            package_pages_qs = ManualPDFPage.objects.select_related(
                "chapter",
                "chapter__package",
                "chapter__package__aircraft",
            )

            file_pages_qs = ManualFilePDFPage.objects.select_related(
                "manual_file",
                "manual_file__aircraft",
            )

            common_pages_qs = CommonManualPDFPage.objects.select_related(
                "common_file",
                "common_file__category",
            )

            other_pages_qs = OtherManualPDFPage.objects.select_related(
                "other_file",
                "other_file__category",
                "other_file__category__aircraft",
            )

            if aircraft_id:
                package_pages_qs = package_pages_qs.filter(
                    chapter__package__aircraft_id=aircraft_id
                )
                file_pages_qs = file_pages_qs.filter(
                    manual_file__aircraft_id=aircraft_id
                )
                other_pages_qs = other_pages_qs.filter(
                    other_file__category__aircraft_id=aircraft_id
                )

            if selected_manual_types:
                selected_package_types = [
                    value
                    for value in selected_manual_types
                    if value in zip_manual_types
                ]
                selected_file_types = [
                    value for value in selected_manual_types if value in pdf_manual_types
                ]

                if selected_package_types:
                    package_pages_qs = package_pages_qs.filter(
                        chapter__package__manual_type__in=selected_package_types
                    )
                else:
                    package_pages_qs = ManualPDFPage.objects.none()

                if selected_file_types:
                    file_pages_qs = file_pages_qs.filter(
                        manual_file__manual_type__in=selected_file_types
                    )
                else:
                    file_pages_qs = ManualFilePDFPage.objects.none()

                selected_other_types = [
                    value
                    for value in selected_manual_types
                    if value not in zip_manual_types
                    and value not in pdf_manual_types
                    and value != "COMMON"
                ]

                if selected_other_types:
                    other_pages_qs = other_pages_qs.filter(
                        other_file__category__manual_type__in=selected_other_types
                    )
                else:
                    other_pages_qs = OtherManualPDFPage.objects.none()

                if "COMMON" not in selected_manual_types:
                    common_pages_qs = CommonManualPDFPage.objects.none()

            search_value, match_mode = parse_manual_search_query(query)

            if search_value:
                text_regex = build_manual_text_regex(search_value, match_mode)

                if match_mode == "contains":
                    package_pages_qs = package_pages_qs.filter(
                        text__icontains=search_value
                    )
                    file_pages_qs = file_pages_qs.filter(text__icontains=search_value)
                    common_pages_qs = common_pages_qs.filter(
                        text__icontains=search_value
                    )
                    other_pages_qs = other_pages_qs.filter(text__icontains=search_value)

                else:
                    prefilter = get_manual_search_prefilter(search_value)

                    if prefilter:
                        package_pages_qs = package_pages_qs.filter(
                            text__icontains=prefilter
                        )
                        file_pages_qs = file_pages_qs.filter(text__icontains=prefilter)
                        common_pages_qs = common_pages_qs.filter(
                            text__icontains=prefilter
                        )
                        other_pages_qs = other_pages_qs.filter(
                            text__icontains=prefilter
                        )

                    package_pages_qs = package_pages_qs.filter(text__iregex=text_regex)
                    file_pages_qs = file_pages_qs.filter(text__iregex=text_regex)
                    common_pages_qs = common_pages_qs.filter(text__iregex=text_regex)
                    other_pages_qs = other_pages_qs.filter(text__iregex=text_regex)

                package_pages = summarize_group_content_pages(
                    package_pages_qs,
                    lambda page: page.chapter.package_id,
                    lambda page: (
                        page.chapter.task or "",
                        page.chapter.subtask or "",
                        page.page_number,
                        page.pk,
                    ),
                )
                file_pages = summarize_group_content_pages(
                    file_pages_qs,
                    lambda page: page.manual_file_id,
                    lambda page: (page.page_number, page.pk),
                )
                common_pages = summarize_group_content_pages(
                    common_pages_qs,
                    lambda page: page.common_file_id,
                    lambda page: (page.page_number, page.pk),
                )
                other_pages = summarize_group_content_pages(
                    other_pages_qs,
                    lambda page: page.other_file_id,
                    lambda page: (page.page_number, page.pk),
                )

                for page in package_pages:
                    page.snippet = build_snippet_from_regex(page.text, text_regex)
                    page.score = calculate_package_score(page, search_value)

                for page in file_pages:
                    page.snippet = build_snippet_from_regex(page.text, text_regex)
                    page.score = calculate_file_score(page, search_value)

                for page in common_pages:
                    page.snippet = build_snippet_from_regex(page.text, text_regex)
                    page.score = 50

                for page in other_pages:
                    page.snippet = build_snippet_from_regex(page.text, text_regex)
                    page.score = 50

                package_pages = sorted(
                    package_pages,
                    key=lambda page: (
                        page.chapter.package.manual_type,
                        page.chapter.package.aircraft.name,
                        page.chapter.task or "",
                        page.chapter.subtask or "",
                        page.page_number,
                    ),
                )

                file_pages = sorted(
                    file_pages,
                    key=lambda page: (
                        page.manual_file.manual_type,
                        page.manual_file.aircraft.name,
                        page.page_number,
                    ),
                )

                common_pages = sorted(
                    common_pages,
                    key=lambda page: (
                        page.common_file.category.name,
                        page.common_file.title,
                        page.page_number,
                    ),
                )

                other_pages = sorted(
                    other_pages,
                    key=lambda page: (
                        page.other_file.category.aircraft.name,
                        page.other_file.category.name,
                        page.other_file.title,
                        page.page_number,
                    ),
                )

        grouped_results = {
            manual_type: []
            for manual_type in manual_types
            if manual_type != "COMMON"
        }

        for page in package_pages:
            page_manual_type = page.chapter.package.manual_type

            if page_manual_type in grouped_results:
                grouped_results[page_manual_type].append(page)

        for page in file_pages:
            page_manual_type = page.manual_file.manual_type

            if page_manual_type in grouped_results:
                grouped_results[page_manual_type].append(page)

        for page in other_pages:
            other_type = page.other_file.category.manual_type

            if other_type in grouped_results:
                grouped_results[other_type].append(page)

        def build_viewer_url(url_name, pk, page_number):
            params = {
                "page": page_number,
                "q": query,
                "from_search_page": "1",
                "back": self.request.get_full_path(),
            }

            return reverse(url_name, kwargs={"pk": pk}) + "?" + urlencode(params)

        result_group_map = {}

        def append_result_group(key, page, group_data):
            page_match_count = getattr(page, "search_match_count", 1)

            if key not in result_group_map:
                result_group_map[key] = {
                    **group_data,
                    "pages": [],
                    "first_page": page,
                    "match_count": 0,
                }

            result_group_map[key]["pages"].append(page)
            result_group_map[key]["match_count"] += page_match_count

        for page in package_pages:
            package = page.chapter.package
            append_result_group(
                ("package", package.pk),
                page,
                {
                    "manual_type": package.manual_type,
                    "eyebrow": f"{package.manual_type} MANUAL",
                    "title": f"{package.aircraft.name} / {package.manual_type}",
                    "aircraft": package.aircraft.name,
                    "manual": package.manual_type,
                    "first_match": f"{page.chapter.task} / Page {page.page_number}",
                    "viewer_url": build_viewer_url(
                        "manual_chapter_pdf_viewer",
                        page.chapter.pk,
                        page.page_number,
                    ),
                    "button_class": "btn-primary",
                    "badge_class": "bg-primary",
                },
            )

        for page in file_pages:
            manual_file = page.manual_file
            append_result_group(
                ("file", manual_file.pk),
                page,
                {
                    "manual_type": manual_file.manual_type,
                    "eyebrow": f"{manual_file.manual_type} MANUAL",
                    "title": f"{manual_file.aircraft.name} / {manual_file.manual_type}",
                    "aircraft": manual_file.aircraft.name,
                    "manual": manual_file.manual_type,
                    "first_match": f"Page {page.page_number}",
                    "viewer_url": build_viewer_url(
                        "manual_file_pdf_viewer",
                        manual_file.pk,
                        page.page_number,
                    ),
                    "button_class": (
                        "btn-danger"
                        if manual_file.manual_type in {"MEL", "CDL"}
                        else "btn-primary"
                    ),
                    "badge_class": "bg-primary",
                },
            )

        for page in other_pages:
            other_file = page.other_file
            append_result_group(
                ("other", other_file.pk),
                page,
                {
                    "manual_type": other_file.category.manual_type,
                    "eyebrow": f"{other_file.category.manual_type} MANUAL",
                    "title": f"{other_file.category.aircraft.name} / {other_file.title}",
                    "aircraft": other_file.category.aircraft.name,
                    "manual": other_file.category.name,
                    "first_match": f"Page {page.page_number}",
                    "viewer_url": build_viewer_url(
                        "other_manual_file_pdf_viewer",
                        other_file.pk,
                        page.page_number,
                    ),
                    "button_class": "btn-primary",
                    "badge_class": "bg-primary",
                },
            )

        for page in common_pages:
            common_file = page.common_file
            append_result_group(
                ("common", common_file.pk),
                page,
                {
                    "manual_type": "COMMON",
                    "eyebrow": "COMMON MANUAL",
                    "title": f"{common_file.category.name} / {common_file.title}",
                    "aircraft": "",
                    "manual": common_file.category.name,
                    "first_match": f"Page {page.page_number}",
                    "viewer_url": build_viewer_url(
                        "common_manual_file_pdf_viewer",
                        common_file.pk,
                        page.page_number,
                    ),
                    "button_class": "btn-success",
                    "badge_class": "bg-success",
                },
            )

        result_groups = list(result_group_map.values())

        context["package_pages"] = package_pages
        context["file_pages"] = file_pages
        context["common_pages"] = common_pages
        context["other_pages"] = other_pages
        context["grouped_results"] = grouped_results
        context["result_groups"] = result_groups

        context["aircrafts"] = Aircraft.objects.all().order_by("maker", "name")

        context["manual_types"] = manual_types

        context["query"] = query
        context["selected_aircraft"] = aircraft_id
        context["selected_manual_types"] = selected_manual_types

        return context


@method_decorator(xframe_options_sameorigin, name="dispatch")
class ManualChapterPDFViewerView(
    LoginRequiredMixin,
    DetailView,
):
    model = ManualChapter
    template_name = "manuals/manual_pdf_viewer.html"
    context_object_name = "chapter"

    def get_context_data(
        self,
        **kwargs,
    ):
        context = super().get_context_data(**kwargs)

        chapter = self.object

        # =====================================================
        # Request Parameters
        # =====================================================

        page_number = safe_int(
            self.request.GET.get(
                "page",
                "1",
            ),
            default=1,
        )

        query = self.request.GET.get(
            "q",
            "",
        ).strip()

        view_mode = self.request.GET.get(
            "mode",
            "single",
        )

        back_url = self.request.GET.get(
            "back",
            "",
        )

        match_scope = self.request.GET.get(
            "scope",
            "",
        ).strip()

        embedded = self.request.GET.get(
            "embedded",
            "",
        ).lower() in {
            "1",
            "true",
            "yes",
        }

        # =====================================================
        # Viewer Information
        # =====================================================

        context["viewer_title"] = (
            f"{chapter.package.aircraft.name} / "
            f"{chapter.package.manual_type} / "
            f"{chapter.task}"
        )

        context["viewer_subtitle"] = chapter.title or "PDF Viewer"

        context["pdf_url"] = reverse(
            "manual_chapter_pdf",
            kwargs={
                "pk": chapter.pk,
            },
        )

        # =====================================================
        # Back URL
        # =====================================================

        if back_url:
            context["back_url"] = back_url

        else:
            context["back_url"] = reverse(
                "manual_chapter_list",
                kwargs={
                    "package_pk": chapter.package.pk,
                },
            )

        # =====================================================
        # Search Origin
        # =====================================================

        back_url_lower = (back_url or "").lower()

        from_search_flag = self.request.GET.get(
            "from_search_page",
            "",
        ).lower()

        context["from_search_page"] = (
            from_search_flag
            in {
                "1",
                "true",
                "yes",
            }
            or (bool(query) and match_scope == "chapter")
            or ("manual-search" in back_url_lower)
            or ("manual_search" in back_url_lower)
            or ("dispatch_search" in back_url_lower)
            or ("/dispatch/" in back_url_lower)
        )

        # =====================================================
        # Basic Context
        # =====================================================

        context["page_number"] = page_number

        context["query"] = query

        context["viewer_type"] = "chapter"

        context["view_mode"] = view_mode

        context["embedded"] = embedded

        # =====================================================
        # Match Defaults
        # =====================================================

        context["prev_match_url"] = None

        context["next_match_url"] = None

        context["current_match_index"] = 0

        context["match_count"] = 0

        context["matching_pages_json"] = json.dumps([])

        context["matching_match_items_json"] = json.dumps([])

        # =====================================================
        # Viewer Query Builder
        # =====================================================

        def build_viewer_query_params(
            page,
        ):
            params = {
                "page": page,
                "mode": view_mode,
            }

            if query:
                params["q"] = query

            if context["from_search_page"]:
                params["from_search_page"] = "1"

            if back_url:
                params["back"] = back_url

            if match_scope:
                params["scope"] = match_scope

            # Preserve Workspace iframe mode
            # during cross-chapter navigation.
            if embedded:
                params["embedded"] = "1"

            return urlencode(params)

        # =====================================================
        # Package Chapters
        # =====================================================

        all_chapters = ManualChapter.objects.filter(package=chapter.package).order_by(
            "task",
            "subtask",
        )

        chapters_data = []

        for ch in all_chapters:
            chapters_data.append(
                {
                    "pk": ch.pk,
                    "task": ch.task or "",
                    "subtask": ch.subtask or "",
                    "title": ch.title or "",
                    "viewer_url": (
                        reverse(
                            "manual_chapter_pdf_viewer",
                            kwargs={
                                "pk": ch.pk,
                            },
                        )
                        + "?"
                        + build_viewer_query_params(1)
                    ),
                }
            )

        context["package_chapters_json"] = json.dumps(chapters_data)

        # =====================================================
        # Search
        # =====================================================

        if query:
            (
                search_value,
                match_mode,
            ) = parse_manual_search_query(query)

            if search_value:
                text_regex = build_manual_text_regex(
                    search_value,
                    match_mode,
                )

                # =============================================
                # Page Filter
                # =============================================

                if match_mode == "contains":
                    page_filter = Q(text__icontains=search_value)

                else:
                    page_filter = Q(text__iregex=text_regex)

                match_queryset = ManualPDFPage.objects.select_related("chapter").filter(
                    page_filter
                )

                # =============================================
                # Chapter-only Search
                # =============================================

                if match_scope == "chapter":
                    match_queryset = match_queryset.filter(chapter=chapter).order_by(
                        "page_number"
                    )

                # =============================================
                # Package-wide Search
                # =============================================

                else:
                    match_queryset = match_queryset.filter(
                        chapter__package=chapter.package
                    ).order_by(
                        "chapter__task",
                        "chapter__subtask",
                        "page_number",
                    )

                matching_page_records = list(
                    match_queryset.only(
                        "chapter_id",
                        "page_number",
                        "text",
                    )
                )

                # =============================================
                # Same page set as Manual/Dispatch Search
                # =============================================

                if match_scope != "chapter":
                    matching_page_records = prefer_content_pages(matching_page_records)

                # =============================================
                # Match Data
                # =============================================

                matches = [
                    {
                        "chapter_id": page.chapter_id,
                        "page_number": page.page_number,
                    }
                    for page in matching_page_records
                ]

                context["match_count"] = len(matches)

                # =============================================
                # Current Match
                # =============================================

                current_index = None

                # Exact current chapter/page
                for (
                    index,
                    item,
                ) in enumerate(matches):
                    if (
                        item["chapter_id"] == chapter.pk
                        and item["page_number"] == page_number
                    ):
                        current_index = index

                        break

                # Next matching page in current chapter
                if current_index is None:
                    for (
                        index,
                        item,
                    ) in enumerate(matches):
                        if (
                            item["chapter_id"] == chapter.pk
                            and item["page_number"] >= page_number
                        ):
                            current_index = index

                            break

                # Fallback
                if current_index is None and matches:
                    current_index = 0

                # =============================================
                # Match Navigation
                # =============================================

                if current_index is not None:

                    # Chapter-only viewer opened at page 1:
                    # jump to first match.
                    if match_scope == "chapter" and page_number == 1:
                        page_number = matches[current_index]["page_number"]

                        context["page_number"] = page_number

                    context["current_match_index"] = current_index + 1

                    # -----------------------------------------
                    # Previous Match
                    # -----------------------------------------

                    if current_index > 0:
                        prev_item = matches[current_index - 1]

                        context["prev_match_url"] = (
                            reverse(
                                "manual_chapter_pdf_viewer",
                                kwargs={
                                    "pk": prev_item["chapter_id"],
                                },
                            )
                            + "?"
                            + build_viewer_query_params(prev_item["page_number"])
                        )

                    # -----------------------------------------
                    # Next Match
                    # -----------------------------------------

                    if current_index < len(matches) - 1:
                        next_item = matches[current_index + 1]

                        context["next_match_url"] = (
                            reverse(
                                "manual_chapter_pdf_viewer",
                                kwargs={
                                    "pk": next_item["chapter_id"],
                                },
                            )
                            + "?"
                            + build_viewer_query_params(next_item["page_number"])
                        )

                # =============================================
                # Current Chapter Matching Pages
                # =============================================

                matching_pages_list = [
                    item["page_number"]
                    for item in matches
                    if (item["chapter_id"] == chapter.pk)
                ]

                context["matching_pages_json"] = json.dumps(matching_pages_list)

                # =============================================
                # Package-wide Match Items
                # =============================================

                context["matching_match_items_json"] = json.dumps(
                    [
                        {
                            "chapterId": item["chapter_id"],
                            "pageNumber": item["page_number"],
                            "viewerUrl": (
                                reverse(
                                    "manual_chapter_pdf_viewer",
                                    kwargs={
                                        "pk": item["chapter_id"],
                                    },
                                )
                                + "?"
                                + build_viewer_query_params(item["page_number"])
                            ),
                        }
                        for item in matches
                    ]
                )

        return context


class ManualFilePDFViewerView(LoginRequiredMixin, DetailView):
    model = ManualFile
    template_name = "manuals/manual_pdf_viewer.html"
    context_object_name = "manual"

    def find_mel_item_start_pages(self, manual, mel_item):
        mel_item = (mel_item or "").strip()

        if not mel_item:
            return []

        item_pattern = re.compile(
            rf"(?<![0-9A-Za-z/_-]){re.escape(mel_item)}(?![0-9A-Za-z/_-])",
            re.IGNORECASE,
        )
        candidates = []

        pages = (
            ManualFilePDFPage.objects.filter(
                manual_file=manual,
                text__icontains=mel_item,
            )
            .only("page_number", "text")
            .order_by("page_number")
        )

        for page in pages:
            match = item_pattern.search(page.text or "")

            if match:
                candidates.append(
                    {
                        "page_number": page.page_number,
                        "position": match.start(),
                        "score": self.score_mel_item_page(
                            page.text,
                            match.start(),
                        ),
                    }
                )

        candidates.sort(
            key=lambda item: (
                -item["score"],
                item["position"],
                item["page_number"],
            )
        )

        return [candidates[0]["page_number"]] if candidates else []

    def score_mel_item_page(self, text, match_position):
        text = text or ""
        lower_text = text.lower()
        before_text = lower_text[max(match_position - 500, 0) : match_position]
        after_text = lower_text[match_position : match_position + 900]
        nearby_text = lower_text[max(match_position - 120, 0) : match_position + 220]

        score = 0

        if re.search(r"\binterval\s+installed\s+required\b", after_text):
            score += 120

        if re.search(r"\brepair\s+interval\b", after_text):
            score += 80

        if re.search(r"\bitem\s*$", before_text[-80:]):
            score += 20

        if "mel item - index" in lower_text[:500]:
            score -= 120

        if "eicas message - index" in lower_text[:500]:
            score -= 120

        if "list of effective" in lower_text[:500]:
            score -= 120

        if re.search(r"\buse\s+mel\s+item\b", nearby_text):
            score -= 90

        if re.search(r"\brefer\s+to\s+(?:mel\s+)?item\b", nearby_text):
            score -= 60

        return score

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        manual = self.object
        page_number = safe_int(self.request.GET.get("page", "1"), default=1)
        query = self.request.GET.get("q", "").strip()
        mel_item_query = self.request.GET.get("mel_item", "").strip()
        mel_start_query = self.request.GET.get("mel_start", "").lower()
        view_mode = self.request.GET.get("mode", "single")
        back_url = self.request.GET.get("back", "")

        context["viewer_title"] = f"{manual.aircraft.name} / {manual.manual_type}"
        context["viewer_subtitle"] = manual.description or "PDF Viewer"
        pdf_url = reverse("manual_file_pdf", kwargs={"pk": manual.pk})

        if manual.uploaded_at:
            pdf_url = f"{pdf_url}?v={int(manual.uploaded_at.timestamp())}"

        context["pdf_url"] = pdf_url

        try:
            context["pdf_fallback_url"] = manual.file.url if manual.file else ""
        except Exception:
            context["pdf_fallback_url"] = ""

        if back_url:
            context["back_url"] = back_url
        else:
            context["back_url"] = reverse(
                "aircraft_manual_detail",
                kwargs={"pk": manual.aircraft.pk},
            )

        back_url_lower = (back_url or "").lower()
        from_search_flag = self.request.GET.get("from_search_page", "").lower()

        context["from_search_page"] = (
            from_search_flag in {"1", "true", "yes"}
            or "manual-search" in back_url_lower
            or "manual_search" in back_url_lower
            or "dispatch_search" in back_url_lower
            or "/dispatch/" in back_url_lower
        )

        context["page_number"] = page_number
        context["query"] = query
        context["viewer_type"] = "file"
        context["view_mode"] = view_mode

        context["prev_match_url"] = None
        context["next_match_url"] = None
        context["current_match_index"] = 0
        context["match_count"] = 0
        context["matching_pages_json"] = json.dumps([])
        context["package_chapters_json"] = json.dumps([])

        def build_viewer_query_params(page):
            params = {
                "page": page,
                "mode": view_mode,
            }

            if query:
                params["q"] = query

            if context["from_search_page"]:
                params["from_search_page"] = "1"

            if back_url:
                params["back"] = back_url

            if mel_item_query:
                params["mel_item"] = mel_item_query

            if mel_start_query:
                params["mel_start"] = mel_start_query

            return urlencode(params)

        if query:
            search_value, match_mode = parse_manual_search_query(query)

            if search_value:
                text_regex = build_manual_text_regex(search_value, match_mode)

                if mel_item_query and mel_start_query in {"1", "true", "yes"}:
                    matching_pages_list = self.find_mel_item_start_pages(
                        manual,
                        mel_item_query,
                    )
                else:
                    if match_mode == "contains":
                        page_filter = Q(text__icontains=search_value)
                    else:
                        page_filter = Q(text__iregex=text_regex)

                    matching_page_records = list(
                        ManualFilePDFPage.objects.filter(manual_file=manual)
                        .filter(page_filter)
                        .order_by("page_number")
                        .only("page_number", "text")
                    )
                    matching_pages_list = [
                        page.page_number
                        for page in prefer_content_pages(matching_page_records)
                    ]

                context["match_count"] = len(matching_pages_list)
                context["matching_pages_json"] = json.dumps(matching_pages_list)

                current_index = None

                for index, matched_page_number in enumerate(matching_pages_list):
                    if matched_page_number == page_number:
                        current_index = index
                        break

                if current_index is None:
                    for index, matched_page_number in enumerate(matching_pages_list):
                        if matched_page_number >= page_number:
                            current_index = index
                            break

                if current_index is None and matching_pages_list:
                    current_index = 0

                if current_index is not None:
                    context["current_match_index"] = current_index + 1

                    if current_index > 0:
                        prev_page_number = matching_pages_list[current_index - 1]
                        context["prev_match_url"] = (
                            reverse(
                                "manual_file_pdf_viewer",
                                kwargs={"pk": manual.pk},
                            )
                            + "?"
                            + build_viewer_query_params(prev_page_number)
                        )

                    if current_index < len(matching_pages_list) - 1:
                        next_page_number = matching_pages_list[current_index + 1]
                        context["next_match_url"] = (
                            reverse(
                                "manual_file_pdf_viewer",
                                kwargs={"pk": manual.pk},
                            )
                            + "?"
                            + build_viewer_query_params(next_page_number)
                        )

        return context


class ManualFilePDFView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        manual = get_object_or_404(
            ManualFile.objects.select_related("aircraft"), pk=kwargs["pk"]
        )

        if not manual.file:
            raise Http404("PDF file is not available")

        file_name = (manual.file.name or "").lower()
        if not file_name.endswith(".pdf"):
            raise Http404("PDF file is not available")

        try:
            pdf_file = manual.file.open("rb")
        except Exception as error:
            raise Http404("PDF file could not be opened") from error

        return FileResponse(
            pdf_file,
            content_type="application/pdf",
            as_attachment=False,
            filename=os.path.basename(manual.file.name),
        )


class ManualPackageUpdateView(
    R2DirectUploadContextMixin,
    LoginRequiredMixin,
    StaffRequiredMixin,
    UpdateView,
):
    model = ManualPackage
    form_class = ManualPackageForm
    template_name = "manuals/manual_package_form.html"
    context_object_name = "package"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["back_url"] = reverse_lazy(
            "aircraft_manual_detail", kwargs={"pk": self.object.aircraft.pk}
        )

        return context

    def form_valid(self, form):
        old_file = ManualPackage.objects.get(pk=self.object.pk).zip_file
        uploaded_zip = self.request.FILES.get("zip_file")
        file_replaced = bool(uploaded_zip or form.cleaned_data.get("r2_object_key"))

        if uploaded_zip:
            form.instance.original_zip_file_name = uploaded_zip.name

        response = super().form_valid(form)

        if (
            old_file
            and self.object.zip_file
            and old_file.name != self.object.zip_file.name
        ):
            delete_file_field(old_file)

        if file_replaced:
            self.object.processed = False
            self.object.uploaded_at = timezone.now()
            self.object.save(update_fields=["processed", "uploaded_at"])
            queue_reindex_message(
                self.request,
                ReindexJob.TARGET_MANUAL_PACKAGE,
                self.object.pk,
                f"{self.object.manual_type} ZIP",
            )
        else:
            messages.success(self.request, "매뉴얼 정보를 수정했습니다.")

        return response

    def get_success_url(self):
        return reverse_lazy(
            "aircraft_manual_detail", kwargs={"pk": self.object.aircraft.pk}
        )


class ManualPackageReindexView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        package = ManualPackage.objects.get(pk=kwargs["pk"])

        package.processed = False
        package.save(update_fields=["processed"])
        queue_reindex_message(
            request,
            ReindexJob.TARGET_MANUAL_PACKAGE,
            package.pk,
            f"{package.manual_type} ZIP",
        )

        return redirect("aircraft_manual_detail", pk=package.aircraft.pk)


class ManualPackageOpenView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        package = ManualPackage.objects.select_related("aircraft").get(pk=kwargs["pk"])
        return redirect("manual_chapter_list", package_pk=package.pk)


class ManualFileReindexView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        manual = ManualFile.objects.get(pk=kwargs["pk"])

        queue_reindex_message(
            request,
            ReindexJob.TARGET_MANUAL_FILE,
            manual.pk,
            f"{manual.manual_type} PDF",
        )

        return redirect("aircraft_manual_detail", pk=manual.aircraft.pk)


class ManualPackageReuploadView(
    R2DirectUploadContextMixin,
    LoginRequiredMixin,
    UserPassesTestMixin,
    UpdateView,
):
    model = ManualPackage
    form_class = ManualPackageForm
    template_name = "manuals/manual_package_form.html"
    pk_url_kwarg = "package_pk"

    def test_func(self):
        return self.request.user.is_superuser

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["zip_file"].required = False
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_reupload"] = True
        context["back_url"] = reverse_lazy(
            "aircraft_manual_detail", kwargs={"pk": self.object.aircraft.pk}
        )
        return context

    def form_valid(self, form):
        old_file = ManualPackage.objects.get(pk=self.object.pk).zip_file
        uploaded_zip = self.request.FILES.get("zip_file")
        r2_object_key = form.cleaned_data.get("r2_object_key")

        if not uploaded_zip and not r2_object_key:
            form.add_error("zip_file", "다시 업로드할 ZIP 파일을 선택해 주세요.")
            return self.form_invalid(form)

        if uploaded_zip:
            form.instance.original_zip_file_name = uploaded_zip.name

        package = form.save()

        if old_file and package.zip_file and old_file.name != package.zip_file.name:
            delete_file_field(old_file)

        package.processed = False
        package.uploaded_at = timezone.now()
        package.save(update_fields=["processed", "uploaded_at"])

        queue_reindex_message(
            self.request,
            ReindexJob.TARGET_MANUAL_PACKAGE,
            package.pk,
            f"{package.manual_type} ZIP",
        )

        return redirect("aircraft_manual_detail", pk=package.aircraft.pk)


class ExtractMelDispatchItemsView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, pk):
        manual = get_object_or_404(ManualFile, pk=pk)

        if manual.manual_type != "MEL":
            messages.error(request, "MEL 파일만 추출할 수 있습니다.")
            return redirect(
                "aircraft_manual_detail",
                pk=manual.aircraft.pk,
            )

        count = extract_mel_dispatch_items_from_pdf(manual)

        messages.success(request, f"MEL Dispatch Item {count}개를 추출했습니다.")

        return redirect(
            "aircraft_manual_detail",
            pk=manual.aircraft.pk,
        )


class CommonManualCategoryDetailView(LoginRequiredMixin, DetailView):
    model = CommonManualCategory
    template_name = "manuals/common_manual_category_detail.html"
    context_object_name = "category"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        back_url = self.request.GET.get("back", "").strip()

        context["files"] = attach_latest_reindex_jobs(
            CommonManualFile.objects.filter(category=self.object).order_by("title"),
            lambda file: file,
            ReindexJob.TARGET_COMMON_FILE,
        )
        context["back_url"] = back_url or reverse_lazy("home")

        return context


class CommonManualFileCreateView(LoginRequiredMixin, StaffRequiredMixin, CreateView):
    model = CommonManualFile
    form_class = CommonManualFileForm
    template_name = "manuals/common_manual_file_form.html"

    def get_initial(self):
        initial = super().get_initial()

        category_id = self.request.GET.get("category")

        if category_id:
            initial["category"] = category_id

        return initial

    def get_form(self, form_class=None):
        form = super().get_form(form_class)

        if self.request.GET.get("category"):
            form.fields["category"].disabled = True

        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        category_id = self.request.GET.get("category")

        if category_id:
            context["back_url"] = reverse_lazy(
                "common_manual_category_detail",
                kwargs={"pk": category_id},
            )
        else:
            context["back_url"] = reverse_lazy("aircraft_manage")

        return context

    def get_success_url(self):
        return reverse_lazy(
            "common_manual_category_detail",
            kwargs={"pk": self.object.category.pk},
        )

    def form_valid(self, form):
        response = super().form_valid(form)

        queue_reindex_message(
            self.request,
            ReindexJob.TARGET_COMMON_FILE,
            self.object.pk,
            "Common manual",
        )

        return response


class CommonManualFileUpdateView(LoginRequiredMixin, StaffRequiredMixin, UpdateView):
    model = CommonManualFile
    form_class = CommonManualFileForm
    template_name = "manuals/common_manual_file_form.html"
    context_object_name = "common_file"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["back_url"] = reverse_lazy(
            "common_manual_category_detail",
            kwargs={"pk": self.object.category.pk},
        )

        return context

    def get_success_url(self):
        return reverse_lazy(
            "common_manual_category_detail",
            kwargs={"pk": self.object.category.pk},
        )

    def form_valid(self, form):
        old_file = None
        old_pdf_file = None
        file_replaced = bool(self.request.FILES.get("file"))

        if self.object.pk:
            old_instance = CommonManualFile.objects.get(pk=self.object.pk)
            old_file = old_instance.file
            old_pdf_file = old_instance.pdf_file

        response = super().form_valid(form)

        if old_file and self.object.file and old_file.name != self.object.file.name:
            delete_file_field(old_file)
            delete_file_field(old_pdf_file)

        if file_replaced:
            refresh_uploaded_at(self.object)

        queue_reindex_message(
            self.request,
            ReindexJob.TARGET_COMMON_FILE,
            self.object.pk,
            "Common manual",
        )

        return response


class CommonManualFileDeleteView(LoginRequiredMixin, StaffRequiredMixin, DeleteView):
    model = CommonManualFile
    template_name = "common/delete_confirm.html"
    context_object_name = "common_file"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "delete_type": "COMMON MANUAL FILE",
                "delete_title": self.object.title,
                "delete_message": "공통 매뉴얼 파일을 삭제하시겠습니까?",
                "warning_message": "PDF 인덱스 데이터도 함께 삭제됩니다.",
                "back_url": reverse(
                    "common_manual_category_detail",
                    kwargs={"pk": self.object.category.pk},
                ),
            }
        )
        return context

    def form_valid(self, form):
        delete_file_field(self.object.file)
        delete_file_field(self.object.pdf_file)

        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy(
            "common_manual_category_detail",
            kwargs={"pk": self.object.category.pk},
        )


class CommonManualFilePDFView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        common_file = get_object_or_404(CommonManualFile, pk=kwargs["pk"])

        if common_file.pdf_file:
            return FileResponse(
                common_file.pdf_file.open("rb"),
                content_type="application/pdf",
            )

        if common_file.file and common_file.file.name.lower().endswith(".pdf"):
            return FileResponse(
                common_file.file.open("rb"),
                content_type="application/pdf",
            )

        raise Http404("PDF file is not available")


class CommonManualFilePDFViewerView(LoginRequiredMixin, DetailView):
    model = CommonManualFile
    template_name = "manuals/manual_pdf_viewer.html"
    context_object_name = "common_file"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        common_file = self.object
        page_number = safe_int(self.request.GET.get("page", "1"), default=1)
        query = self.request.GET.get("q", "").strip()
        view_mode = self.request.GET.get("mode", "single")
        back_url = self.request.GET.get("back", "")

        context["viewer_title"] = f"COMMON / {common_file.category.name}"
        context["viewer_subtitle"] = common_file.title
        context["pdf_url"] = reverse(
            "common_manual_file_pdf",
            kwargs={"pk": common_file.pk},
        )

        back_url_lower = (back_url or "").lower()
        from_search_flag = self.request.GET.get("from_search_page", "").lower()

        if back_url:
            context["back_url"] = back_url
        else:
            context["back_url"] = reverse(
                "common_manual_category_detail",
                kwargs={"pk": common_file.category.pk},
            )

        context["from_search_page"] = (
            from_search_flag in {"1", "true", "yes"}
            or "manual-search" in back_url_lower
            or "manual_search" in back_url_lower
            or "dispatch_search" in back_url_lower
            or "/dispatch/" in back_url_lower
        )
        context["page_number"] = page_number
        context["query"] = query
        context["viewer_type"] = "common"
        context["view_mode"] = view_mode

        context["prev_match_url"] = None
        context["next_match_url"] = None
        context["current_match_index"] = 0
        context["match_count"] = 0
        context["matching_pages_json"] = json.dumps([])
        context["package_chapters_json"] = json.dumps([])

        if query:
            search_value, match_mode = parse_manual_search_query(query)

            if search_value:
                text_regex = build_manual_text_regex(search_value, match_mode)

                if match_mode == "contains":
                    page_filter = Q(text__icontains=search_value)
                else:
                    page_filter = Q(text__iregex=text_regex)

                matching_page_records = list(
                    CommonManualPDFPage.objects.filter(common_file=common_file)
                    .filter(page_filter)
                    .order_by("page_number")
                    .only("page_number", "text")
                )
                matching_pages_list = [
                    page.page_number
                    for page in prefer_content_pages(matching_page_records)
                ]

                context["match_count"] = len(matching_pages_list)
                context["matching_pages_json"] = json.dumps(matching_pages_list)

                current_index = None

                for index, matched_page_number in enumerate(matching_pages_list):
                    if matched_page_number == page_number:
                        current_index = index
                        break

                if current_index is None:
                    for index, matched_page_number in enumerate(matching_pages_list):
                        if matched_page_number >= page_number:
                            current_index = index
                            break

                if current_index is None and matching_pages_list:
                    current_index = 0

                if current_index is not None:
                    context["current_match_index"] = current_index + 1

                    if current_index > 0:
                        prev_page_number = matching_pages_list[current_index - 1]
                        context["prev_match_url"] = (
                            reverse(
                                "common_manual_file_pdf_viewer",
                                kwargs={"pk": common_file.pk},
                            )
                            + f"?page={prev_page_number}&q={query}&mode={view_mode}"
                        )

                    if current_index < len(matching_pages_list) - 1:
                        next_page_number = matching_pages_list[current_index + 1]
                        context["next_match_url"] = (
                            reverse(
                                "common_manual_file_pdf_viewer",
                                kwargs={"pk": common_file.pk},
                            )
                            + f"?page={next_page_number}&q={query}&mode={view_mode}"
                        )

        return context


class CommonManualFileReindexView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        common_file = get_object_or_404(CommonManualFile, pk=kwargs["pk"])

        queue_reindex_message(
            request,
            ReindexJob.TARGET_COMMON_FILE,
            common_file.pk,
            "Common manual",
        )

        return redirect(
            "common_manual_category_detail",
            pk=common_file.category.pk,
        )


class CommonManualCategoryCreateView(
    LoginRequiredMixin, StaffRequiredMixin, CreateView
):
    model = CommonManualCategory
    form_class = CommonManualCategoryForm
    template_name = "manuals/common_manual_category_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["back_url"] = f"{reverse('aircraft_manage')}?tab=common"
        return context

    def get_success_url(self):
        return f"{reverse('aircraft_manage')}?tab=common"


class CommonManualCategoryUpdateView(
    LoginRequiredMixin, StaffRequiredMixin, UpdateView
):
    model = CommonManualCategory
    form_class = CommonManualCategoryForm
    template_name = "manuals/common_manual_category_form.html"
    context_object_name = "category"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["back_url"] = f"{reverse('aircraft_manage')}?tab=common"
        return context

    def get_success_url(self):
        return f"{reverse('aircraft_manage')}?tab=common"


class CommonManualCategoryDeleteView(
    LoginRequiredMixin,
    StaffRequiredMixin,
    DeleteView,
):
    model = CommonManualCategory
    template_name = "common/delete_confirm.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context.update(
            {
                "delete_type": "COMMON MANUAL",
                "delete_title": self.object.name,
                "delete_message": "공통 매뉴얼 카테고리를 삭제하시겠습니까?",
                "warning_message": "카테고리에 연결된 파일도 함께 삭제될 수 있습니다.",
                "back_url": f"{reverse('aircraft_manage')}?tab=common",
            }
        )

        return context

    def form_valid(self, form):
        for common_file in self.object.files.all():
            delete_file_field(common_file.file)
            delete_file_field(common_file.pdf_file)

        return super().form_valid(form)

    def get_success_url(self):
        return f"{reverse('aircraft_manage')}?tab=common"


class OtherManualCategoryCreateView(LoginRequiredMixin, StaffRequiredMixin, CreateView):
    model = OtherManualCategory
    form_class = OtherManualCategoryForm
    template_name = "manuals/other_manual_category_form.html"

    def dispatch(self, request, *args, **kwargs):
        aircraft_id = request.GET.get("aircraft")
        manual_type = request.GET.get("manual_type", "OTHER").strip().upper()

        if aircraft_id:
            existing_category = OtherManualCategory.objects.filter(
                aircraft_id=aircraft_id,
                manual_type=manual_type,
            ).first()

            if existing_category:
                return redirect(
                    "other_manual_category_detail",
                    pk=existing_category.pk,
                )

        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()

        aircraft_id = self.request.GET.get("aircraft")
        manual_type = self.request.GET.get("manual_type")

        if aircraft_id:
            initial["aircraft"] = aircraft_id

        if manual_type:
            initial["manual_type"] = manual_type.strip().upper()

        return initial

    def get_form(self, form_class=None):
        form = super().get_form(form_class)

        if self.request.GET.get("aircraft"):
            form.fields["aircraft"].disabled = True

        if self.request.GET.get("manual_type"):
            form.fields["manual_type"].disabled = True

        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        aircraft_id = self.request.GET.get("aircraft")

        if aircraft_id:
            context["back_url"] = reverse_lazy(
                "aircraft_manual_detail",
                kwargs={"pk": aircraft_id},
            )
        else:
            context["back_url"] = reverse_lazy("home")

        return context

    def get_success_url(self):
        return reverse_lazy(
            "other_manual_category_detail",
            kwargs={"pk": self.object.pk},
        )


class OtherManualCategoryDetailView(LoginRequiredMixin, DetailView):
    model = OtherManualCategory
    template_name = "manuals/other_manual_category_detail.html"
    context_object_name = "category"

    def get_folder_path(self):
        return ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        files = attach_latest_reindex_jobs(
            OtherManualFile.objects.filter(category=self.object).order_by("title"),
            lambda file: file,
            ReindexJob.TARGET_OTHER_FILE,
        )
        current_path = self.get_folder_path()
        prefix = f"{current_path}/" if current_path else ""
        direct_files = []
        child_folders = {}

        for other_file in files:
            folder_path = (other_file.folder_path or "").strip("/")

            if folder_path == current_path:
                direct_files.append(other_file)
                continue

            if not folder_path.startswith(prefix):
                continue

            remainder = folder_path[len(prefix) :]

            if not remainder:
                continue

            child_name = remainder.split("/", 1)[0]
            child_path = "/".join(
                part for part in [current_path, child_name] if part
            )
            entry = child_folders.setdefault(
                child_name,
                {
                    "name": child_name,
                    "path": child_path,
                    "file_count": 0,
                },
            )
            entry["file_count"] += 1

        folder_entries = sorted(
            child_folders.values(),
            key=lambda item: item["name"].lower(),
        )

        for entry in folder_entries:
            entry["open_url"] = (
                reverse(
                    "other_manual_folder_browse",
                    kwargs={"pk": self.object.pk},
                )
                + "?"
                + urlencode({"path": entry["path"]})
            )

        context["files"] = files
        context["display_files"] = sorted(
            direct_files,
            key=lambda item: item.title.lower(),
        )
        context["folder_entries"] = folder_entries
        context["has_items"] = bool(folder_entries or direct_files)
        context["current_folder_path"] = current_path
        context["current_folder_name"] = (
            current_path.rsplit("/", 1)[-1] if current_path else ""
        )

        if current_path:
            parent_path = current_path.rsplit("/", 1)[0] if "/" in current_path else ""
            context["back_url"] = (
                reverse(
                    "other_manual_folder_browse",
                    kwargs={"pk": self.object.pk},
                )
                + "?"
                + urlencode({"path": parent_path})
                if parent_path
                else reverse(
                    "other_manual_category_detail",
                    kwargs={"pk": self.object.pk},
                )
            )
        else:
            context["back_url"] = reverse_lazy(
                "aircraft_manual_detail",
                kwargs={"pk": self.object.aircraft.pk},
            )

        return context


class OtherManualFolderBrowseView(OtherManualCategoryDetailView):
    def get_folder_path(self):
        raw_path = self.request.GET.get("path", "").replace("\\", "/").strip("/")
        parts = raw_path.split("/") if raw_path else []

        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise Http404("Invalid folder path")

        normalized_path = "/".join(parts)
        exists = OtherManualFile.objects.filter(category=self.object).filter(
            Q(folder_path=normalized_path)
            | Q(folder_path__startswith=f"{normalized_path}/")
        ).exists()

        if not exists:
            raise Http404("Folder not found")

        return normalized_path


class OtherManualFolderUploadView(
    LoginRequiredMixin,
    StaffRequiredMixin,
    FormView,
):
    form_class = OtherManualFolderUploadForm
    template_name = "manuals/other_manual_folder_form.html"

    def get_initial(self):
        initial = super().get_initial()
        category_id = self.request.GET.get("category")

        if category_id:
            initial["category"] = category_id

        return initial

    def get_form(self, form_class=None):
        form = super().get_form(form_class)

        if self.request.GET.get("category"):
            form.fields["category"].disabled = True

        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        category_id = self.request.GET.get("category") or self.request.POST.get(
            "category"
        )

        context["back_url"] = (
            reverse("other_manual_category_detail", kwargs={"pk": category_id})
            if category_id
            else reverse("home")
        )
        return context

    def form_valid(self, form):
        category = form.cleaned_data["category"]
        folder_package = form.cleaned_data["folder_package"]
        revision_no = form.cleaned_data.get("revision_no", "").strip()
        revision_date_text = form.cleaned_data.get(
            "revision_date_text",
            "",
        ).strip()
        description = form.cleaned_data.get("description", "").strip()
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        extract_to = tempfile.mkdtemp(
            prefix="other-manual-",
            dir=settings.MEDIA_ROOT,
        )
        zip_path = None

        try:
            with tempfile.NamedTemporaryFile(
                suffix=".zip",
                delete=False,
                dir=settings.MEDIA_ROOT,
            ) as temp_zip:
                zip_path = temp_zip.name

                for chunk in folder_package.chunks():
                    temp_zip.write(chunk)

            safe_extract_zip(zip_path, extract_to)
            extracted_root = os.path.abspath(extract_to)
            source_files = []

            for root, dirs, file_names in os.walk(extracted_root):
                for file_name in file_names:
                    absolute_path = os.path.abspath(os.path.join(root, file_name))

                    if os.path.commonpath([extracted_root, absolute_path]) != extracted_root:
                        continue

                    extension = os.path.splitext(file_name)[1].lower()

                    if extension not in set(DOCUMENT_EXTENSIONS + IMAGE_EXTENSIONS):
                        continue

                    source_files.append(absolute_path)

            if not source_files:
                form.add_error(
                    "folder_package",
                    "폴더에서 PDF, Word, Excel 또는 이미지 파일을 찾지 못했습니다.",
                )
                return self.form_invalid(form)

            created_count = 0
            replaced_count = 0

            for absolute_path in sorted(source_files):
                relative_path = os.path.relpath(
                    absolute_path,
                    extracted_root,
                ).replace(os.sep, "/")
                folder_path = os.path.dirname(relative_path).replace(os.sep, "/")
                file_name = os.path.basename(absolute_path)
                title = os.path.splitext(file_name)[0]
                other_file = OtherManualFile.objects.filter(
                    category=category,
                    relative_path=relative_path,
                ).first()

                if other_file:
                    delete_file_field(other_file.file)
                    delete_file_field(other_file.converted_pdf)
                    replaced_count += 1
                else:
                    other_file = OtherManualFile(category=category)
                    created_count += 1

                other_file.title = title
                other_file.relative_path = relative_path
                other_file.folder_path = folder_path
                other_file.description = description
                other_file.revision_no = revision_no
                other_file.revision_date_text = revision_date_text

                with open(absolute_path, "rb") as source:
                    other_file.file.save(file_name, File(source), save=False)

                other_file.save()
                queue_reindex_job(
                    ReindexJob.TARGET_OTHER_FILE,
                    other_file.pk,
                    message="OTHER manual indexing queued.",
                )

            messages.success(
                self.request,
                f"OTHER 폴더 업로드 완료: 신규 {created_count}개, 교체 {replaced_count}개",
            )
            self.category = category
            return super().form_valid(form)
        except Exception as error:
            logger.exception("OTHER folder upload failed")
            form.add_error("folder_package", f"폴더 업로드 실패: {error}")
            return self.form_invalid(form)
        finally:
            shutil.rmtree(extract_to, ignore_errors=True)

            if zip_path and os.path.exists(zip_path):
                os.remove(zip_path)

    def get_success_url(self):
        return reverse(
            "other_manual_category_detail",
            kwargs={"pk": self.category.pk},
        )


class OtherManualCategoryUpdateView(LoginRequiredMixin, StaffRequiredMixin, UpdateView):
    model = OtherManualCategory
    form_class = OtherManualCategoryForm
    template_name = "manuals/other_manual_category_form.html"
    context_object_name = "category"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["back_url"] = reverse_lazy(
            "other_manual_category_detail",
            kwargs={"pk": self.object.pk},
        )

        return context

    def get_success_url(self):
        return reverse_lazy(
            "other_manual_category_detail",
            kwargs={"pk": self.object.pk},
        )


class OtherManualCategoryDeleteView(LoginRequiredMixin, StaffRequiredMixin, DeleteView):
    model = OtherManualCategory
    template_name = "common/delete_confirm.html"
    context_object_name = "category"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context.update(
            {
                "delete_type": "OTHER FOLDER",
                "delete_title": self.object.name,
                "delete_message": "OTHER 폴더를 삭제하시겠습니까?",
                "warning_message": "폴더 안의 업로드 파일과 PDF 인덱스 데이터도 함께 삭제됩니다.",
                "back_url": reverse(
                    "aircraft_manual_detail",
                    kwargs={"pk": self.object.aircraft.pk},
                ),
            }
        )

        return context

    def form_valid(self, form):
        for other_file in self.object.files.all():
            delete_file_field(other_file.file)
            delete_file_field(other_file.converted_pdf)

        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy(
            "aircraft_manual_detail",
            kwargs={"pk": self.object.aircraft.pk},
        )


class OtherManualFileCreateView(LoginRequiredMixin, StaffRequiredMixin, CreateView):
    model = OtherManualFile
    form_class = OtherManualFileForm
    template_name = "manuals/other_manual_file_form.html"

    def get_initial(self):
        initial = super().get_initial()

        category_id = self.request.GET.get("category")

        if category_id:
            initial["category"] = category_id

        return initial

    def get_form(self, form_class=None):
        form = super().get_form(form_class)

        if self.request.GET.get("category"):
            form.fields["category"].disabled = True

        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        category_id = self.request.GET.get("category")

        if category_id:
            context["back_url"] = reverse_lazy(
                "other_manual_category_detail",
                kwargs={"pk": category_id},
            )
        else:
            context["back_url"] = reverse_lazy("home")

        return context

    def form_valid(self, form):
        response = super().form_valid(form)

        queue_reindex_message(
            self.request,
            ReindexJob.TARGET_OTHER_FILE,
            self.object.pk,
            "OTHER manual",
        )

        return response

    def get_success_url(self):
        return reverse_lazy(
            "other_manual_category_detail",
            kwargs={"pk": self.object.category.pk},
        )


class OtherManualFileUpdateView(LoginRequiredMixin, StaffRequiredMixin, UpdateView):
    model = OtherManualFile
    form_class = OtherManualFileForm
    template_name = "manuals/other_manual_file_form.html"
    context_object_name = "other_file"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["back_url"] = reverse_lazy(
            "other_manual_category_detail",
            kwargs={"pk": self.object.category.pk},
        )

        return context

    def form_valid(self, form):
        old_file = None
        old_converted_pdf = None

        if self.object.pk:
            old_instance = OtherManualFile.objects.get(pk=self.object.pk)
            old_file = old_instance.file
            old_converted_pdf = old_instance.converted_pdf

        response = super().form_valid(form)

        if old_file and self.object.file and old_file.name != self.object.file.name:
            delete_file_field(old_file)
            delete_file_field(old_converted_pdf)

        queue_reindex_message(
            self.request,
            ReindexJob.TARGET_OTHER_FILE,
            self.object.pk,
            "OTHER manual",
        )

        return response

    def get_success_url(self):
        return reverse_lazy(
            "other_manual_category_detail",
            kwargs={"pk": self.object.category.pk},
        )


class OtherManualFileDeleteView(LoginRequiredMixin, StaffRequiredMixin, DeleteView):
    model = OtherManualFile
    template_name = "common/delete_confirm.html"
    context_object_name = "other_file"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context.update(
            {
                "delete_type": "OTHER FILE",
                "delete_title": self.object.title,
                "delete_message": "OTHER 파일을 삭제하시겠습니까?",
                "warning_message": "업로드 파일과 PDF 인덱스 데이터도 함께 삭제됩니다.",
                "back_url": reverse(
                    "other_manual_category_detail",
                    kwargs={"pk": self.object.category.pk},
                ),
            }
        )

        return context

    def form_valid(self, form):
        delete_file_field(self.object.file)
        delete_file_field(self.object.converted_pdf)

        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy(
            "other_manual_category_detail",
            kwargs={"pk": self.object.category.pk},
        )


class OtherManualFilePDFView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        other_file = get_object_or_404(
            OtherManualFile.objects.select_related(
                "category",
                "category__aircraft",
            ),
            pk=kwargs["pk"],
        )

        pdf_file = other_file.pdf_file

        if not pdf_file:
            raise Http404("PDF file is not available")

        return FileResponse(
            pdf_file.open("rb"),
            content_type="application/pdf",
        )


class OtherManualFilePDFViewerView(LoginRequiredMixin, DetailView):
    model = OtherManualFile
    template_name = "manuals/manual_pdf_viewer.html"
    context_object_name = "other_file"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        other_file = self.object
        page_number = safe_int(self.request.GET.get("page", "1"), default=1)
        query = self.request.GET.get("q", "").strip()
        view_mode = self.request.GET.get("mode", "single")
        back_url = self.request.GET.get("back", "")

        context["viewer_title"] = (
            f"OTHER / {other_file.category.aircraft.name} / {other_file.category.name}"
        )
        context["viewer_subtitle"] = other_file.title
        context["pdf_url"] = reverse(
            "other_manual_file_pdf",
            kwargs={"pk": other_file.pk},
        )

        back_url_lower = (back_url or "").lower()
        from_search_flag = self.request.GET.get("from_search_page", "").lower()

        if back_url:
            context["back_url"] = back_url
        else:
            context["back_url"] = reverse(
                "other_manual_category_detail",
                kwargs={"pk": other_file.category.pk},
            )

        context["from_search_page"] = (
            from_search_flag in {"1", "true", "yes"}
            or "manual-search" in back_url_lower
            or "manual_search" in back_url_lower
            or "dispatch_search" in back_url_lower
            or "/dispatch/" in back_url_lower
        )
        context["page_number"] = page_number
        context["query"] = query
        context["viewer_type"] = "other"
        context["view_mode"] = view_mode

        context["prev_match_url"] = None
        context["next_match_url"] = None
        context["current_match_index"] = 0
        context["match_count"] = 0
        context["matching_pages_json"] = json.dumps([])
        context["package_chapters_json"] = json.dumps([])

        if query:
            search_value, match_mode = parse_manual_search_query(query)

            if search_value:
                text_regex = build_manual_text_regex(search_value, match_mode)

                if match_mode == "contains":
                    page_filter = Q(text__icontains=search_value)
                else:
                    page_filter = Q(text__iregex=text_regex)

                matching_page_records = list(
                    OtherManualPDFPage.objects.filter(other_file=other_file)
                    .filter(page_filter)
                    .order_by("page_number")
                    .only("page_number", "text")
                )
                matching_pages_list = [
                    page.page_number
                    for page in prefer_content_pages(matching_page_records)
                ]

                context["match_count"] = len(matching_pages_list)
                context["matching_pages_json"] = json.dumps(matching_pages_list)

                current_index = None

                def build_viewer_query_params(page):
                    params = {
                        "page": page,
                        "mode": view_mode,
                    }

                    if query:
                        params["q"] = query

                    if context["from_search_page"]:
                        params["from_search_page"] = "1"

                    if back_url:
                        params["back"] = back_url

                    return urlencode(params)

                for index, matched_page_number in enumerate(matching_pages_list):
                    if matched_page_number == page_number:
                        current_index = index
                        break

                if current_index is None:
                    for index, matched_page_number in enumerate(matching_pages_list):
                        if matched_page_number >= page_number:
                            current_index = index
                            break

                if current_index is None and matching_pages_list:
                    current_index = 0

                if current_index is not None:
                    context["current_match_index"] = current_index + 1

                    if current_index > 0:
                        prev_page_number = matching_pages_list[current_index - 1]
                        context["prev_match_url"] = (
                            reverse(
                                "other_manual_file_pdf_viewer",
                                kwargs={"pk": other_file.pk},
                            )
                            + "?"
                            + build_viewer_query_params(prev_page_number)
                        )

                    if current_index < len(matching_pages_list) - 1:
                        next_page_number = matching_pages_list[current_index + 1]
                        context["next_match_url"] = (
                            reverse(
                                "other_manual_file_pdf_viewer",
                                kwargs={"pk": other_file.pk},
                            )
                            + "?"
                            + build_viewer_query_params(next_page_number)
                        )

        return context


class OtherManualFileReindexView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        other_file = get_object_or_404(OtherManualFile, pk=kwargs["pk"])

        queue_reindex_message(
            request,
            ReindexJob.TARGET_OTHER_FILE,
            other_file.pk,
            "OTHER manual",
        )

        return redirect(
            "other_manual_category_detail",
            pk=other_file.category.pk,
        )
