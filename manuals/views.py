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
    AirbusManualLink,
    ManualPackage,
    ManualChapter,
    ManualPDFPage,
    ManualFilePDFPage,
    ManualIndexLog,
)
from .forms import ManualFileForm, AircraftForm, AirbusManualLinkForm, ManualPackageForm

from django.contrib import messages
from .services import (
    process_manual_package,
    index_pdf_pages_for_manual_file,
    process_manual_package_safely,
    index_pdf_pages_for_manual_file_safely,
    create_index_log,
    parse_search_query,
    get_match_navigation,
    calculate_package_score,
    calculate_file_score,
    safe_int,
    merge_package_chapter_pdfs,
    parse_manual_search_query,
    build_manual_text_regex,
)
from django.http import FileResponse, Http404
from django.db.models import Q, Count, Min
from django.shortcuts import redirect, get_object_or_404


class StaffRequiredMixin(UserPassesTestMixin):

    def test_func(self):
        return self.request.user.is_staff


class HomeView(LoginRequiredMixin, TemplateView):
    template_name = "home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["boeing_aircrafts"] = Aircraft.objects.filter(maker="BOEING")

        context["airbus_aircraft"] = Aircraft.objects.filter(maker="AIRBUS").first()

        context["recent_failed_logs"] = (
            ManualIndexLog.objects.filter(status="FAILED")
            .select_related("aircraft")
            .order_by("-created_at")[:5]
        )

        context["index_success_count"] = ManualIndexLog.objects.filter(
            status="SUCCESS"
        ).count()

        context["index_failed_count"] = ManualIndexLog.objects.filter(
            status="FAILED"
        ).count()

        context["recent_index_logs"] = ManualIndexLog.objects.select_related(
            "aircraft"
        ).order_by("-created_at")[:5]

        return context


class AircraftManualDetailView(LoginRequiredMixin, DetailView):
    model = Aircraft
    template_name = "manuals/aircraft_manual_detail.html"
    context_object_name = "aircraft"

    def get_queryset(self):
        return Aircraft.objects.filter(maker="BOEING").prefetch_related(
            "manuals",
            "manual_packages",
            "manual_packages__chapters",
            "manuals__pdf_pages",
        )


class ManualFileCreateView(LoginRequiredMixin, StaffRequiredMixin, CreateView):
    model = ManualFile
    form_class = ManualFileForm
    template_name = "manuals/manual_file_form.html"

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

        page_count = index_pdf_pages_for_manual_file_safely(self.object)

        if page_count:
            messages.success(
                self.request,
                f"{self.object.manual_type} PDF Page {page_count}개 인덱싱 완료",
            )

        return response

    def get_success_url(self):
        return reverse_lazy(
            "aircraft_manual_detail", kwargs={"pk": self.object.aircraft.pk}
        )


class ManualFileUpdateView(LoginRequiredMixin, StaffRequiredMixin, UpdateView):
    model = ManualFile
    form_class = ManualFileForm
    template_name = "manuals/manual_file_form.html"
    context_object_name = "manual"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["back_url"] = reverse_lazy(
            "aircraft_manual_detail", kwargs={"pk": self.object.aircraft.pk}
        )

        return context

    def form_valid(self, form):
        response = super().form_valid(form)

        page_count = index_pdf_pages_for_manual_file_safely(self.object)

        if page_count:
            messages.success(
                self.request,
                f"{self.object.manual_type} PDF Page {page_count}개 재인덱싱 완료",
            )
        else:
            messages.info(
                self.request,
                "PDF 인덱싱 대상이 아니거나 페이지 텍스트를 추출하지 못했습니다.",
            )

        return response

    def get_success_url(self):
        return reverse_lazy(
            "aircraft_manual_detail", kwargs={"pk": self.object.aircraft.pk}
        )


class ManualFileDeleteView(LoginRequiredMixin, StaffRequiredMixin, DeleteView):
    model = ManualFile
    template_name = "manuals/manual_file_confirm_delete.html"
    context_object_name = "manual"

    def get_success_url(self):
        return reverse_lazy(
            "aircraft_manual_detail", kwargs={"pk": self.object.aircraft.pk}
        )


class AircraftManageListView(LoginRequiredMixin, StaffRequiredMixin, ListView):
    model = Aircraft
    template_name = "manuals/aircraft_manage_list.html"
    context_object_name = "aircrafts"


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
    template_name = "manuals/aircraft_confirm_delete.html"
    context_object_name = "aircraft"
    success_url = reverse_lazy("aircraft_manage")


class AirbusManualLinkListView(LoginRequiredMixin, ListView):
    model = AirbusManualLink
    template_name = "manuals/airbus_link_list.html"
    context_object_name = "links"


class AirbusManualLinkCreateView(LoginRequiredMixin, StaffRequiredMixin, CreateView):
    model = AirbusManualLink
    form_class = AirbusManualLinkForm
    template_name = "manuals/airbus_link_form.html"
    success_url = reverse_lazy("airbus_link_list")


class AirbusManualLinkUpdateView(LoginRequiredMixin, StaffRequiredMixin, UpdateView):
    model = AirbusManualLink
    form_class = AirbusManualLinkForm
    template_name = "manuals/airbus_link_form.html"
    context_object_name = "link"
    success_url = reverse_lazy("airbus_link_list")


class AirbusManualLinkDeleteView(LoginRequiredMixin, StaffRequiredMixin, DeleteView):
    model = AirbusManualLink
    template_name = "manuals/airbus_link_confirm_delete.html"
    context_object_name = "link"
    success_url = reverse_lazy("airbus_link_list")


class AircraftManualPrintView(LoginRequiredMixin, DetailView):
    model = Aircraft
    template_name = "manuals/aircraft_manual_print.html"
    context_object_name = "aircraft"

    def get_queryset(self):
        return Aircraft.objects.filter(maker="BOEING").prefetch_related("manuals")


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
        self.package = ManualPackage.objects.get(
            pk=self.kwargs["package_pk"]
        )

        return ManualChapter.objects.filter(
            package=self.package
        ).order_by(
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
                ManualChapter.objects
                .filter(package=self.package)
                .filter(chapter_filter | Q(pages__text__iregex=text_regex))
                .distinct()
                .order_by("task", "subtask")
            )

            context["matching_chapters"] = (
                ManualPDFPage.objects
                .filter(chapter__package=self.package)
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

        package_pages = ManualPDFPage.objects.none()
        file_pages = ManualFilePDFPage.objects.none()

        if query:
            package_pages = ManualPDFPage.objects.select_related(
                "chapter",
                "chapter__package",
                "chapter__package__aircraft",
            )

            file_pages = ManualFilePDFPage.objects.select_related(
                "manual_file",
                "manual_file__aircraft",
            )

            if aircraft_id:
                package_pages = package_pages.filter(
                    chapter__package__aircraft_id=aircraft_id
                )

                file_pages = file_pages.filter(manual_file__aircraft_id=aircraft_id)

            if manual_type:
                if manual_type in ["AMM", "FIM", "IPC"]:
                    package_pages = package_pages.filter(
                        chapter__package__manual_type=manual_type
                    )
                    file_pages = ManualFilePDFPage.objects.none()

                elif manual_type in ["MEL", "CDL"]:
                    file_pages = file_pages.filter(manual_file__manual_type=manual_type)
                    package_pages = ManualPDFPage.objects.none()

            words = query.split()

            for word in words:
                package_pages = package_pages.filter(
                    Q(text__icontains=word)
                    | Q(chapter__task__icontains=word)
                    | Q(chapter__subtask__icontains=word)
                    | Q(chapter__title__icontains=word)
                )

                file_pages = file_pages.filter(
                    Q(text__icontains=word)
                    | Q(manual_file__manual_type__icontains=word)
                    | Q(manual_file__description__icontains=word)
                )

            package_pages = list(package_pages[:200])
            file_pages = list(file_pages[:200])

            for page in package_pages:
                page.snippet = page.get_snippet(query)
                page.score = calculate_package_score(page, query)

            for page in file_pages:
                page.snippet = page.get_snippet(query)
                page.score = calculate_file_score(page, query)

            package_pages = sorted(
                package_pages, key=lambda page: page.score, reverse=True
            )[:50]

            file_pages = sorted(file_pages, key=lambda page: page.score, reverse=True)[
                :50
            ]

            for page in package_pages:
                page.snippet = page.get_snippet(query)

            for page in file_pages:
                page.snippet = page.get_snippet(query)

        context["package_pages"] = package_pages
        context["file_pages"] = file_pages

        context["aircrafts"] = Aircraft.objects.filter(maker="BOEING")

        context["manual_types"] = [
            "AMM",
            "FIM",
            "IPC",
            "MEL",
            "CDL",
        ]

        context["query"] = query
        context["selected_aircraft"] = aircraft_id
        context["selected_manual_type"] = manual_type

        return context


from django.urls import reverse
from django.db.models import Q

class ManualChapterPDFViewerView(LoginRequiredMixin, DetailView):
    model = ManualChapter
    template_name = "manuals/manual_pdf_viewer.html"
    context_object_name = "chapter"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        chapter = self.object
        page_number = safe_int(self.request.GET.get("page", "1"), default=1)
        query = self.request.GET.get("q", "").strip()

        context["viewer_title"] = (
            f"{chapter.package.aircraft.name} / "
            f"{chapter.package.manual_type} / "
            f"{chapter.task}"
        )
        context["viewer_subtitle"] = chapter.title or "PDF Viewer"

        context["pdf_url"] = reverse(
            "manual_chapter_pdf",
            kwargs={"pk": chapter.pk},
        )

        context["back_url"] = reverse(
            "manual_chapter_list",
            kwargs={"package_pk": chapter.package.pk},
        )

        context["page_number"] = page_number
        context["query"] = query
        context["viewer_type"] = "file"

        context["prev_match_url"] = None
        context["next_match_url"] = None
        context["current_match_index"] = 0
        context["match_count"] = 0

        if query:
            search_value, match_mode = parse_manual_search_query(query)
            text_regex = build_manual_text_regex(search_value, match_mode)

            if match_mode == "contains":
                page_filter = Q(text__icontains=search_value)
            else:
                page_filter = Q(text__iregex=text_regex)

            matches = list(
                ManualPDFPage.objects
                .select_related("chapter")
                .filter(
                    chapter__package=chapter.package
                )
                .filter(page_filter)
                .order_by(
                    "chapter__task",
                    "chapter__subtask",
                    "page_number",
                )
                .values(
                    "chapter_id",
                    "page_number",
                    "chapter__task",
                )
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

            if current_index is not None:
                context["current_match_index"] = current_index + 1

                if current_index > 0:
                    prev_item = matches[current_index - 1]
                    context["prev_match_url"] = (
                        reverse(
                            "manual_chapter_pdf_viewer",
                            kwargs={"pk": prev_item["chapter_id"]},
                        )
                        + f"?page={prev_item['page_number']}&q={query}"
                    )

                if current_index < len(matches) - 1:
                    next_item = matches[current_index + 1]
                    context["next_match_url"] = (
                        reverse(
                            "manual_chapter_pdf_viewer",
                            kwargs={"pk": next_item["chapter_id"]},
                        )
                        + f"?page={next_item['page_number']}&q={query}"
                    )

        return context


def _get_chapter_pdf_paths(package):
    if not package.extracted_path:
        return []

    extracted_root = os.path.abspath(package.extracted_path)
    chapters = package.chapters.order_by("task", "subtask")
    chapter_paths = []

    for chapter in chapters:
        pdf_path = os.path.abspath(
            os.path.join(extracted_root, chapter.pdf_relative_path)
        )

        if os.path.commonpath([extracted_root, pdf_path]) != extracted_root:
            continue

        if not os.path.exists(pdf_path):
            continue

        chapter_paths.append((chapter, pdf_path))

    return chapter_paths


def _ensure_merged_package_pdf(package):
    chapter_paths = _get_chapter_pdf_paths(package)

    if not chapter_paths:
        return None

    latest_source_mtime = max(os.path.getmtime(path) for _, path in chapter_paths)

    if package.merged_pdf:
        merged_path = package.merged_pdf.path

        if os.path.exists(merged_path) and os.path.getsize(merged_path) > 0:
            if os.path.getmtime(merged_path) >= latest_source_mtime:
                return merged_path

    merged_pdf = merge_package_chapter_pdfs(package)

    if not merged_pdf:
        return None

    merged_path = merged_pdf.path

    if not os.path.exists(merged_path) or os.path.getsize(merged_path) == 0:
        return None

    return merged_path


from .services import parse_search_query


class ManualFilePDFViewerView(LoginRequiredMixin, DetailView):
    model = ManualFile
    template_name = "manuals/manual_pdf_viewer.html"
    context_object_name = "manual"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        manual = self.object
        page_number = safe_int(self.request.GET.get("page", "1"), default=1)
        query = self.request.GET.get("q", "").strip()

        context["viewer_title"] = f"{manual.aircraft.name} / {manual.manual_type}"
        context["viewer_subtitle"] = manual.description or "PDF Viewer"
        context["pdf_url"] = manual.file.url

        context["back_url"] = reverse(
            "aircraft_manual_detail",
            kwargs={"pk": manual.aircraft.pk},
        )

        context["page_number"] = page_number
        context["query"] = query
        context["viewer_type"] = "file"

        if query:
            search_value, match_mode = parse_manual_search_query(query)
            text_regex = build_manual_text_regex(search_value, match_mode)

            if match_mode == "contains":
                page_filter = Q(text__icontains=search_value)
            else:
                page_filter = Q(text__iregex=text_regex)

            matching_pages = (
                ManualFilePDFPage.objects
                .filter(manual_file=manual)
                .filter(page_filter)
                .order_by("page_number")
                .values_list("page_number", flat=True)
                .distinct()
            )
        else:
            matching_pages = []

        context.update(
            get_match_navigation(matching_pages, page_number)
        )

        matching_pages_list = list(matching_pages)

        context.update(
            get_match_navigation(
                matching_pages_list,
                page_number,
            )
        )

        context["matching_pages_json"] = json.dumps(matching_pages_list)
        context["view_mode"] = self.request.GET.get("mode", "single")

        return context


class ManualPackagePDFView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        package = ManualPackage.objects.select_related("aircraft").get(pk=kwargs["pk"])

        merged_path = _ensure_merged_package_pdf(package)

        if not merged_path:
            raise Http404("Merged PDF is not available")

        return FileResponse(open(merged_path, "rb"), content_type="application/pdf")


class ManualPackagePDFViewerView(LoginRequiredMixin, DetailView):
    model = ManualPackage
    template_name = "manuals/manual_pdf_viewer.html"
    context_object_name = "package"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        package = self.object
        page_number = safe_int(self.request.GET.get("page", "1"), default=1)
        query = self.request.GET.get("q", "").strip()

        merged_path = _ensure_merged_package_pdf(package)

        context["viewer_title"] = f"{package.aircraft.name} / {package.manual_type}"
        context["viewer_subtitle"] = "Merged PDF Viewer"
        if merged_path:
            context["pdf_url"] = reverse(
                "manual_package_pdf",
                kwargs={"pk": package.pk},
            )
        else:
            context["pdf_url"] = ""
            context["merge_error"] = (
                "병합된 PDF를 만들 수 없습니다. ZIP 처리 상태를 확인하세요."
            )
        context["back_url"] = reverse(
            "aircraft_manual_detail",
            kwargs={"pk": package.aircraft.pk},
        )
        context["page_number"] = page_number
        context["query"] = query
        context["package_chapters"] = []
        context["viewer_type"] = "file"

        return context


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

            _ensure_merged_package_pdf(self.object)

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

        try:
            result = process_manual_package_safely(package)

            create_index_log(
                target_type="PACKAGE",
                aircraft=package.aircraft,
                manual_type=package.manual_type,
                status="SUCCESS",
                message="ZIP 재인덱싱 완료",
                chapter_count=result["chapter_count"],
                page_count=result["page_count"],
            )

            messages.success(
                request,
                f"{package.manual_type} 재인덱싱 완료: Chapter {result['chapter_count']}개, PDF Page {result['page_count']}개",
            )

            _ensure_merged_package_pdf(package)

        except Exception as error:
            create_index_log(
                target_type="PACKAGE",
                aircraft=package.aircraft,
                manual_type=package.manual_type,
                status="FAILED",
                message=str(error),
            )

            messages.error(request, f"재인덱싱 실패: {error}")

        return redirect("aircraft_manual_detail", pk=package.aircraft.pk)


class ManualPackageOpenView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        package = ManualPackage.objects.select_related("aircraft").get(pk=kwargs["pk"])
        return redirect("manual_chapter_list", package_pk=package.pk)


class ManualFileReindexView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        manual = ManualFile.objects.get(pk=kwargs["pk"])

        try:
            page_count = index_pdf_pages_for_manual_file_safely(manual)

            create_index_log(
                target_type="PDF",
                aircraft=manual.aircraft,
                manual_type=manual.manual_type,
                status="SUCCESS",
                message="PDF 재인덱싱 완료",
                page_count=page_count,
            )

            messages.success(
                request, f"{manual.manual_type} PDF Page {page_count}개 재인덱싱 완료"
            )

        except Exception as error:
            create_index_log(
                target_type="PDF",
                aircraft=manual.aircraft,
                manual_type=manual.manual_type,
                status="FAILED",
                message=str(error),
            )
            messages.error(request, f"재인덱싱 실패: {error}")

        return redirect("aircraft_manual_detail", pk=manual.aircraft.pk)


class ManualIndexLogListView(LoginRequiredMixin, StaffRequiredMixin, ListView):
    model = ManualIndexLog
    template_name = "manuals/index_log_list.html"
    context_object_name = "logs"
    paginate_by = 30

    def get_queryset(self):
        queryset = ManualIndexLog.objects.select_related("aircraft")

        target_type = self.request.GET.get("target_type", "")
        status = self.request.GET.get("status", "")
        aircraft_id = self.request.GET.get("aircraft", "")
        manual_type = self.request.GET.get("manual_type", "")

        if target_type:
            queryset = queryset.filter(target_type=target_type)

        if status:
            queryset = queryset.filter(status=status)

        if aircraft_id:
            queryset = queryset.filter(aircraft_id=aircraft_id)

        if manual_type:
            queryset = queryset.filter(manual_type=manual_type)

        return queryset.order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["aircrafts"] = Aircraft.objects.filter(maker="BOEING")

        context["manual_types"] = [
            "AMM",
            "FIM",
            "IPC",
            "MEL",
            "CDL",
        ]

        context["selected_target_type"] = self.request.GET.get("target_type", "")
        context["selected_status"] = self.request.GET.get("status", "")
        context["selected_aircraft"] = self.request.GET.get("aircraft", "")
        context["selected_manual_type"] = self.request.GET.get("manual_type", "")

        return context


class ManualIndexLogDetailView(LoginRequiredMixin, StaffRequiredMixin, DetailView):
    model = ManualIndexLog
    template_name = "manuals/index_log_detail.html"
    context_object_name = "log"


class ManualIndexDashboardView(LoginRequiredMixin, StaffRequiredMixin, TemplateView):
    template_name = "manuals/index_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        aircrafts = Aircraft.objects.filter(maker="BOEING").prefetch_related(
            "manual_packages",
            "manuals",
        )

        dashboard_rows = []

        for aircraft in aircrafts:
            row = {
                "aircraft": aircraft,
                "packages": [],
                "pdfs": [],
                "recent_logs": ManualIndexLog.objects.filter(
                    aircraft=aircraft
                ).order_by("-created_at")[:5],
            }

            for package in aircraft.manual_packages.all():
                row["packages"].append(
                    {
                        "id": package.id,
                        "manual_type": package.manual_type,
                        "processed": package.processed,
                        "chapter_count": package.chapter_count(),
                        "page_count": package.page_count(),
                    }
                )

            for manual in aircraft.manuals.all():
                if manual.manual_type in ["MEL", "CDL"]:
                    row["pdfs"].append(
                        {
                            "id": manual.id,
                            "manual_type": manual.manual_type,
                            "indexed": manual.is_indexed_pdf(),
                            "page_count": manual.indexed_page_count(),
                        }
                    )

            dashboard_rows.append(row)

        context["dashboard_rows"] = dashboard_rows

        context["total_package_count"] = ManualPackage.objects.count()

        context["total_chapter_count"] = ManualChapter.objects.count()

        context["total_package_page_count"] = ManualPDFPage.objects.count()

        context["total_pdf_page_count"] = ManualFilePDFPage.objects.count()

        context["total_index_page_count"] = (
            context["total_package_page_count"] + context["total_pdf_page_count"]
        )

        context["total_success_count"] = ManualIndexLog.objects.filter(
            status="SUCCESS"
        ).count()

        context["total_failed_count"] = ManualIndexLog.objects.filter(
            status="FAILED"
        ).count()

        return context


class ManualPackageReuploadView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = ManualPackage
    form_class = ManualPackageForm
    template_name = "manuals/manual_package_form.html"
    pk_url_kwarg = "package_pk"

    def test_func(self):
        return self.request.user.is_staff

    def form_valid(self, form):
        package = form.save()

        try:
            result = process_manual_package_safely(package)
            _ensure_merged_package_pdf(package)

            messages.success(
                self.request,
                f"재업로드 완료: Chapter {result['chapter_count']}개, PDF Page {result['page_count']}개",
            )

        except Exception as error:
            messages.error(self.request, f"재업로드 실패: {error}")
            return redirect("aircraft_manual_detail", pk=package.aircraft.pk)

        return redirect("manual_chapter_list", package_pk=package.pk)
