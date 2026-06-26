import os, shutil, json

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
)
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

from .models import (
    Aircraft,
    ManualFile,
    ManualPackage,
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
)
from .forms import (
    ManualFileForm,
    AircraftForm,
    ManualPackageForm,
    CommonManualCategoryForm,
    CommonManualFileForm,
    OtherManualCategoryForm,
    OtherManualFileForm,
)

from django.contrib import messages
from .services import (
    process_manual_package,
    index_pdf_pages_for_manual_file,
    process_manual_package_safely,
    parse_search_query,
    get_match_navigation,
    calculate_package_score,
    calculate_file_score,
    safe_int,
    parse_manual_search_query,
    build_manual_text_regex,
    build_snippet_from_regex,
)
from django.http import FileResponse, Http404
from django.db.models import Q, Count, Min, Prefetch
from django.shortcuts import redirect, get_object_or_404
from dispatch.services import extract_mel_dispatch_items_from_pdf
from django.views import View
from urllib.parse import urlencode


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


def attach_latest_reindex_jobs(items, item_getter, target_type):
    item_list = list(items)
    target_ids = [
        item_getter(item).pk
        for item in item_list
        if item_getter(item) is not None
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


class HomeView(LoginRequiredMixin, TemplateView):
    template_name = "home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["boeing_aircrafts"] = (
            Aircraft.objects.filter(maker="BOEING")
            .order_by("name")
            .prefetch_related(
                "manuals",
                "manual_packages",
                "other_categories",
                "other_categories__files",
            )
        )

        context["airbus_aircrafts"] = (
            Aircraft.objects.filter(maker="AIRBUS")
            .order_by("name")
            .prefetch_related(
                "manuals",
                "manual_packages",
                "other_categories",
                "other_categories__files",
            )
        )

        context["common_categories"] = CommonManualCategory.objects.prefetch_related(
            "files"
        )

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
        manual_items = self.object.get_manual_status_list()
        manual_items = attach_latest_reindex_jobs(
            manual_items,
            lambda item: item.get("package"),
            ReindexJob.TARGET_MANUAL_PACKAGE,
        )
        manual_items = attach_latest_reindex_jobs(
            manual_items,
            lambda item: item.get("file"),
            ReindexJob.TARGET_MANUAL_FILE,
        )
        context["manual_items"] = manual_items
        return context


class ManualFileCreateView(LoginRequiredMixin, StaffRequiredMixin, CreateView):
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
        old_file = None

        if self.object.pk:
            old_instance = ManualFile.objects.get(pk=self.object.pk)
            old_file = old_instance.file

        response = super().form_valid(form)

        if old_file and self.object.file and old_file.name != self.object.file.name:
            delete_file_field(old_file)

        queue_reindex_job(
            ReindexJob.TARGET_MANUAL_FILE,
            self.object.pk,
            f"{self.object.manual_type} PDF indexing queued",
        )
        messages.success(
            self.request,
            "PDF 업로드가 완료되었습니다. 검색 인덱싱 작업을 예약했습니다.",
        )

        return response

    def get_success_url(self):
        return reverse_lazy(
            "aircraft_manual_detail",
            kwargs={"pk": self.object.aircraft.pk},
        )


class ManualFileUpdateView(LoginRequiredMixin, StaffRequiredMixin, UpdateView):
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
        response = super().form_valid(form)

        queue_reindex_job(
            ReindexJob.TARGET_MANUAL_FILE,
            self.object.pk,
            f"{self.object.manual_type} PDF indexing queued",
        )
        messages.success(
            self.request,
            "PDF 재업로드가 완료되었습니다. 검색 인덱싱 작업을 예약했습니다.",
        )

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
        return Aircraft.objects.all().order_by("maker", "name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["common_categories"] = CommonManualCategory.objects.prefetch_related(
            "files"
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


class AircraftManualPrintView(LoginRequiredMixin, DetailView):
    model = Aircraft
    template_name = "manuals/aircraft_manual_print.html"
    context_object_name = "aircraft"

    def get_queryset(self):
        return Aircraft.objects.prefetch_related("manuals")


class ManualFileDetailView(LoginRequiredMixin, DetailView):
    model = ManualFile
    template_name = "manuals/manual_detail.html"
    context_object_name = "manual"


class ManualPackageCreateView(LoginRequiredMixin, StaffRequiredMixin, CreateView):
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
        response = super().form_valid(form)

        try:
            result = process_manual_package_safely(self.object)

            messages.success(
                self.request,
                f"ZIP 처리 완료: Chapter {result['chapter_count']}개, PDF Page {result['page_count']}개 인덱싱 완료",
            )

        except Exception as error:
            self.object.delete()

            messages.error(self.request, f"ZIP 처리 실패: {error}")

        return response

    def get_success_url(self):
        return reverse_lazy(
            "aircraft_manual_detail", kwargs={"pk": self.object.aircraft.pk}
        )


class ManualChapterListView(ListView):
    model = ManualChapter
    template_name = "manuals/manual_chapter_list.html"
    context_object_name = "chapters"
    paginate_by = 50

    def get_queryset(self):
        self.package = ManualPackage.objects.get(pk=self.kwargs["package_pk"])

        return ManualChapter.objects.filter(package=self.package).order_by(
            "task",
            "subtask",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        q = self.request.GET.get("q", "").strip()

        context["package"] = self.package
        context["q"] = q

        context["chapter_bookmarks"] = ManualChapter.objects.filter(
            package=self.package
        ).order_by(
            "task",
            "subtask",
        )

        context["matching_chapters"] = []

        if q:
            search_value, match_mode = parse_manual_search_query(q)
            text_regex = build_manual_text_regex(
                search_value,
                match_mode,
            )

            if match_mode == "contains":
                page_filter = Q(text__icontains=search_value)
                chapter_filter = (
                    Q(task__icontains=search_value)
                    | Q(subtask__icontains=search_value)
                    | Q(title__icontains=search_value)
                )

            elif match_mode == "startswith":
                page_filter = Q(text__iregex=text_regex)
                chapter_filter = (
                    Q(task__istartswith=search_value)
                    | Q(subtask__istartswith=search_value)
                    | Q(title__istartswith=search_value)
                )

            else:
                page_filter = Q(text__iregex=text_regex)
                chapter_filter = (
                    Q(task__iexact=search_value)
                    | Q(subtask__iexact=search_value)
                    | Q(title__iexact=search_value)
                )

            context["chapter_bookmarks"] = (
                ManualChapter.objects.filter(package=self.package)
                .filter(chapter_filter | Q(pages__text__iregex=text_regex))
                .distinct()
                .order_by("task", "subtask")
            )

            context["matching_chapters"] = (
                ManualPDFPage.objects.filter(chapter__package=self.package)
                .filter(page_filter)
                .values(
                    "chapter_id",
                    "chapter__task",
                    "chapter__subtask",
                    "chapter__title",
                )
                .annotate(
                    match_count=Count("id"),
                    first_page=Min("page_number"),
                )
                .order_by("chapter__task", "chapter__subtask")
            )

        return context


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

        return FileResponse(open(pdf_path, "rb"), content_type="application/pdf")


class ManualSearchView(LoginRequiredMixin, TemplateView):
    template_name = "manuals/manual_search.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        query = self.request.GET.get("q", "").strip()
        aircraft_id = self.request.GET.get("aircraft", "").strip()
        manual_type = self.request.GET.get("manual_type", "").strip()

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

            if manual_type:
                if manual_type in ["AMM", "FIM", "IPC"]:
                    package_pages_qs = package_pages_qs.filter(
                        chapter__package__manual_type=manual_type
                    )
                    file_pages_qs = ManualFilePDFPage.objects.none()
                    common_pages_qs = CommonManualPDFPage.objects.none()
                    other_pages_qs = OtherManualPDFPage.objects.none()

                elif manual_type in ["MEL", "CDL"]:
                    file_pages_qs = file_pages_qs.filter(
                        manual_file__manual_type=manual_type
                    )
                    package_pages_qs = ManualPDFPage.objects.none()
                    common_pages_qs = CommonManualPDFPage.objects.none()
                    other_pages_qs = OtherManualPDFPage.objects.none()

                elif manual_type == "OTHER":
                    package_pages_qs = ManualPDFPage.objects.none()
                    file_pages_qs = ManualFilePDFPage.objects.none()
                    common_pages_qs = CommonManualPDFPage.objects.none()

                elif manual_type == "COMMON":
                    package_pages_qs = ManualPDFPage.objects.none()
                    file_pages_qs = ManualFilePDFPage.objects.none()
                    other_pages_qs = OtherManualPDFPage.objects.none()

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
                    package_pages_qs = package_pages_qs.filter(text__iregex=text_regex)
                    file_pages_qs = file_pages_qs.filter(text__iregex=text_regex)
                    common_pages_qs = common_pages_qs.filter(text__iregex=text_regex)
                    other_pages_qs = other_pages_qs.filter(text__iregex=text_regex)

                package_pages = list(package_pages_qs)
                file_pages = list(file_pages_qs)
                common_pages = list(common_pages_qs)
                other_pages = list(other_pages_qs)

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
            "AMM": [],
            "FIM": [],
            "IPC": [],
            "MEL": [],
            "CDL": [],
            "OTHER": [],
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
            grouped_results["OTHER"].append(page)

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
            if key not in result_group_map:
                result_group_map[key] = {
                    **group_data,
                    "pages": [],
                    "first_page": page,
                    "match_count": 0,
                }

            result_group_map[key]["pages"].append(page)
            result_group_map[key]["match_count"] = len(result_group_map[key]["pages"])

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
                    "button_class": "btn-danger"
                    if manual_file.manual_type in {"MEL", "CDL"}
                    else "btn-primary",
                    "badge_class": "bg-primary",
                },
            )

        for page in other_pages:
            other_file = page.other_file
            append_result_group(
                ("other", other_file.pk),
                page,
                {
                    "manual_type": "OTHER",
                    "eyebrow": "OTHER MANUAL",
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

        context["manual_types"] = [
            "AMM",
            "FIM",
            "IPC",
            "MEL",
            "CDL",
            "OTHER",
            "COMMON",
        ]

        context["query"] = query
        context["selected_aircraft"] = aircraft_id
        context["selected_manual_type"] = manual_type

        return context


class ManualChapterPDFViewerView(LoginRequiredMixin, DetailView):
    model = ManualChapter
    template_name = "manuals/manual_pdf_viewer.html"
    context_object_name = "chapter"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        chapter = self.object
        page_number = safe_int(self.request.GET.get("page", "1"), default=1)
        query = self.request.GET.get("q", "").strip()
        view_mode = self.request.GET.get("mode", "single")
        back_url = self.request.GET.get("back", "")
        match_scope = self.request.GET.get("scope", "").strip()

        context["viewer_title"] = (
            f"{chapter.package.aircraft.name} / "
            f"{chapter.package.manual_type} / "
            f"{chapter.task}"
        )
        context["viewer_subtitle"] = chapter.title or "PDF Viewer"
        context["pdf_url"] = reverse("manual_chapter_pdf", kwargs={"pk": chapter.pk})

        if back_url:
            context["back_url"] = back_url
        else:
            context["back_url"] = reverse(
                "manual_chapter_list",
                kwargs={"package_pk": chapter.package.pk},
            )

        back_url_lower = (back_url or "").lower()
        from_search_flag = self.request.GET.get("from_search_page", "").lower()

        context["from_search_page"] = (
            from_search_flag in {"1", "true", "yes"}
            or "manual-search" in back_url_lower
            or "manual_search" in back_url_lower
            or "dispatch_auto_search" in back_url_lower
            or "/dispatch/" in back_url_lower
        )

        context["page_number"] = page_number
        context["query"] = query
        context["viewer_type"] = "chapter"
        context["view_mode"] = view_mode

        context["prev_match_url"] = None
        context["next_match_url"] = None
        context["current_match_index"] = 0
        context["match_count"] = 0
        context["matching_pages_json"] = json.dumps([])
        context["matching_match_items_json"] = json.dumps([])

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

            if match_scope:
                params["scope"] = match_scope

            return urlencode(params)

        all_chapters = ManualChapter.objects.filter(package=chapter.package).order_by(
            "task", "subtask"
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
                        reverse("manual_chapter_pdf_viewer", kwargs={"pk": ch.pk})
                        + "?"
                        + build_viewer_query_params(1)
                    ),
                }
            )

        context["package_chapters_json"] = json.dumps(chapters_data)

        if query:
            search_value, match_mode = parse_manual_search_query(query)

            if search_value:
                text_regex = build_manual_text_regex(search_value, match_mode)

                if match_mode == "contains":
                    page_filter = Q(text__icontains=search_value)
                else:
                    page_filter = Q(text__iregex=text_regex)

                match_queryset = ManualPDFPage.objects.select_related("chapter").filter(
                    page_filter
                )

                if match_scope == "chapter":
                    match_queryset = match_queryset.filter(chapter=chapter).order_by(
                        "page_number"
                    )
                else:
                    match_queryset = match_queryset.filter(
                        chapter__package=chapter.package
                    ).order_by(
                        "chapter__task",
                        "chapter__subtask",
                        "page_number",
                    )

                matches = list(
                    match_queryset.values("chapter_id", "page_number", "chapter__task")
                )

                context["match_count"] = len(matches)

                current_index = None

                for index, item in enumerate(matches):
                    if (
                        item["chapter_id"] == chapter.pk
                        and item["page_number"] == page_number
                    ):
                        current_index = index
                        break

                if current_index is None:
                    for index, item in enumerate(matches):
                        if (
                            item["chapter_id"] == chapter.pk
                            and item["page_number"] >= page_number
                        ):
                            current_index = index
                            break

                if current_index is None and matches:
                    current_index = 0

                if current_index is not None:
                    if match_scope == "chapter" and page_number == 1:
                        page_number = matches[current_index]["page_number"]
                        context["page_number"] = page_number

                    context["current_match_index"] = current_index + 1

                    if current_index > 0:
                        prev_item = matches[current_index - 1]
                        context["prev_match_url"] = (
                            reverse(
                                "manual_chapter_pdf_viewer",
                                kwargs={"pk": prev_item["chapter_id"]},
                            )
                            + "?"
                            + build_viewer_query_params(prev_item["page_number"])
                        )

                    if current_index < len(matches) - 1:
                        next_item = matches[current_index + 1]
                        context["next_match_url"] = (
                            reverse(
                                "manual_chapter_pdf_viewer",
                                kwargs={"pk": next_item["chapter_id"]},
                            )
                            + "?"
                            + build_viewer_query_params(next_item["page_number"])
                        )

                matching_pages_list = [
                    item["page_number"]
                    for item in matches
                    if item["chapter_id"] == chapter.pk
                ]

                context["matching_pages_json"] = json.dumps(matching_pages_list)
                context["matching_match_items_json"] = json.dumps(
                    [
                        {
                            "chapterId": item["chapter_id"],
                            "pageNumber": item["page_number"],
                            "viewerUrl": (
                                reverse(
                                    "manual_chapter_pdf_viewer",
                                    kwargs={"pk": item["chapter_id"]},
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        manual = self.object
        page_number = safe_int(self.request.GET.get("page", "1"), default=1)
        query = self.request.GET.get("q", "").strip()
        view_mode = self.request.GET.get("mode", "single")
        back_url = self.request.GET.get("back", "")

        context["viewer_title"] = f"{manual.aircraft.name} / {manual.manual_type}"
        context["viewer_subtitle"] = manual.description or "PDF Viewer"
        context["pdf_url"] = reverse("manual_file_pdf", kwargs={"pk": manual.pk})

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
            or "dispatch_auto_search" in back_url_lower
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

            return urlencode(params)

        if query:
            search_value, match_mode = parse_manual_search_query(query)

            if search_value:
                text_regex = build_manual_text_regex(search_value, match_mode)

                if match_mode == "contains":
                    page_filter = Q(text__icontains=search_value)
                else:
                    page_filter = Q(text__iregex=text_regex)

                matching_pages_list = list(
                    ManualFilePDFPage.objects.filter(manual_file=manual)
                    .filter(page_filter)
                    .order_by("page_number")
                    .values_list("page_number", flat=True)
                    .distinct()
                )

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

        file_name = (manual.file.name or "").lower()
        if not file_name.endswith(".pdf"):
            raise Http404("PDF file is not available")

        if not manual.file:
            raise Http404("PDF file is not available")

        return FileResponse(manual.file.open("rb"), content_type="application/pdf")


class ManualPackageUpdateView(LoginRequiredMixin, StaffRequiredMixin, UpdateView):
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

        response = super().form_valid(form)

        self.object.processed = False
        self.object.save(update_fields=["processed"])

        try:
            result = process_manual_package_safely(self.object)

            messages.success(
                self.request,
                f"ZIP 재처리 완료: Chapter {result['chapter_count']}개, PDF Page {result['page_count']}개 인덱싱 완료",
            )

        except Exception as error:
            messages.error(self.request, f"ZIP 재처리 실패: {error}")

        return response

    def get_success_url(self):
        return reverse_lazy(
            "aircraft_manual_detail", kwargs={"pk": self.object.aircraft.pk}
        )


class ManualPackageReindexView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        package = ManualPackage.objects.get(pk=kwargs["pk"])

        job, created = queue_reindex_job(
            ReindexJob.TARGET_MANUAL_PACKAGE,
            package.pk,
            f"{package.manual_type} ZIP re-index queued",
        )

        if created:
            package.processed = False
            package.save(update_fields=["processed"])
            messages.success(request, "재인덱싱 작업을 예약했습니다.")
        else:
            messages.info(request, "이미 재인덱싱 작업이 진행 중입니다.")

        return redirect("aircraft_manual_detail", pk=package.aircraft.pk)


class ManualPackageOpenView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        package = ManualPackage.objects.select_related("aircraft").get(pk=kwargs["pk"])
        return redirect("manual_chapter_list", package_pk=package.pk)


class ManualFileReindexView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        manual = ManualFile.objects.get(pk=kwargs["pk"])

        _, created = queue_reindex_job(
            ReindexJob.TARGET_MANUAL_FILE,
            manual.pk,
            f"{manual.manual_type} PDF re-index queued",
        )

        if created:
            messages.success(request, "재인덱싱 작업을 예약했습니다.")
        else:
            messages.info(request, "이미 재인덱싱 작업이 진행 중입니다.")

        return redirect("aircraft_manual_detail", pk=manual.aircraft.pk)


class ManualPackageReuploadView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = ManualPackage
    form_class = ManualPackageForm
    template_name = "manuals/manual_package_form.html"
    pk_url_kwarg = "package_pk"

    def test_func(self):
        return self.request.user.is_staff

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["back_url"] = reverse_lazy(
            "aircraft_manual_detail", kwargs={"pk": self.object.aircraft.pk}
        )
        return context

    def form_valid(self, form):
        package = form.save()
        package.processed = False
        package.save(update_fields=["processed"])

        try:
            result = process_manual_package_safely(package)

            messages.success(
                self.request,
                f"재업로드 완료: Chapter {result['chapter_count']}개, PDF Page {result['page_count']}개",
            )

        except Exception as error:
            messages.error(self.request, f"재업로드 실패: {error}")
            return redirect("aircraft_manual_detail", pk=package.aircraft.pk)

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
            CommonManualFile.objects.filter(
            category=self.object
            ).order_by("title"),
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
        old_file = None
        old_pdf_file = None

        if self.object.pk:
            old_instance = CommonManualFile.objects.get(pk=self.object.pk)
            old_file = old_instance.file
            old_pdf_file = old_instance.pdf_file

        response = super().form_valid(form)

        if old_file and self.object.file and old_file.name != self.object.file.name:
            delete_file_field(old_file)
            delete_file_field(old_pdf_file)

        queue_reindex_job(
            ReindexJob.TARGET_COMMON_FILE,
            self.object.pk,
            "Common manual indexing queued",
        )
        messages.success(
            self.request,
            "업로드가 완료되었습니다. 검색 인덱싱 작업을 예약했습니다.",
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
        response = super().form_valid(form)

        queue_reindex_job(
            ReindexJob.TARGET_COMMON_FILE,
            self.object.pk,
            "Common manual indexing queued",
        )
        messages.success(
            self.request,
            "수정이 완료되었습니다. 검색 인덱싱 작업을 예약했습니다.",
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
            or "dispatch_auto_search" in back_url_lower
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

                matching_pages_list = list(
                    CommonManualPDFPage.objects.filter(common_file=common_file)
                    .filter(page_filter)
                    .order_by("page_number")
                    .values_list("page_number", flat=True)
                    .distinct()
                )

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

        _, created = queue_reindex_job(
            ReindexJob.TARGET_COMMON_FILE,
            common_file.pk,
            "Common manual re-index queued",
        )

        if created:
            messages.success(request, "재인덱싱 작업을 예약했습니다.")
        else:
            messages.info(request, "이미 재인덱싱 작업이 진행 중입니다.")

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
    success_url = reverse_lazy("aircraft_manage")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["back_url"] = reverse_lazy("aircraft_manage")
        return context


class CommonManualCategoryUpdateView(
    LoginRequiredMixin, StaffRequiredMixin, UpdateView
):
    model = CommonManualCategory
    form_class = CommonManualCategoryForm
    template_name = "manuals/common_manual_category_form.html"
    context_object_name = "category"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["back_url"] = reverse_lazy("aircraft_manage")
        return context

    def get_success_url(self):
        return reverse_lazy("aircraft_manage")


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
                "back_url": reverse(
                    "common_manual_category_detail",
                    kwargs={"pk": self.object.pk},
                ),
            }
        )

        return context

    def form_valid(self, form):
        for common_file in self.object.files.all():
            delete_file_field(common_file.file)
            delete_file_field(common_file.pdf_file)

        return super().form_valid(form)

    def get_success_url(self):
        return reverse("home")


class OtherManualCategoryCreateView(LoginRequiredMixin, StaffRequiredMixin, CreateView):
    model = OtherManualCategory
    form_class = OtherManualCategoryForm
    template_name = "manuals/other_manual_category_form.html"

    def dispatch(self, request, *args, **kwargs):
        aircraft_id = request.GET.get("aircraft")

        if aircraft_id:
            existing_category = OtherManualCategory.objects.filter(
                aircraft_id=aircraft_id
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

        if aircraft_id:
            initial["aircraft"] = aircraft_id

        return initial

    def get_form(self, form_class=None):
        form = super().get_form(form_class)

        if self.request.GET.get("aircraft"):
            form.fields["aircraft"].disabled = True

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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["files"] = attach_latest_reindex_jobs(
            OtherManualFile.objects.filter(
            category=self.object
            ).order_by("title"),
            lambda file: file,
            ReindexJob.TARGET_OTHER_FILE,
        )

        context["back_url"] = reverse_lazy(
            "aircraft_manual_detail",
            kwargs={"pk": self.object.aircraft.pk},
        )

        return context


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

        queue_reindex_job(
            ReindexJob.TARGET_OTHER_FILE,
            self.object.pk,
            "Other file indexing queued",
        )
        messages.success(
            self.request,
            "업로드가 완료되었습니다. 검색 인덱싱 작업을 예약했습니다.",
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

        queue_reindex_job(
            ReindexJob.TARGET_OTHER_FILE,
            self.object.pk,
            "Other file indexing queued",
        )
        messages.success(
            self.request,
            "수정이 완료되었습니다. 검색 인덱싱 작업을 예약했습니다.",
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
            or "dispatch_auto_search" in back_url_lower
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

                matching_pages_list = list(
                    OtherManualPDFPage.objects.filter(other_file=other_file)
                    .filter(page_filter)
                    .order_by("page_number")
                    .values_list("page_number", flat=True)
                    .distinct()
                )

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

        _, created = queue_reindex_job(
            ReindexJob.TARGET_OTHER_FILE,
            other_file.pk,
            "Other file re-index queued",
        )

        if created:
            messages.success(request, "재인덱싱 작업을 예약했습니다.")
        else:
            messages.info(request, "이미 재인덱싱 작업이 진행 중입니다.")

        return redirect(
            "other_manual_category_detail",
            pk=other_file.category.pk,
        )
