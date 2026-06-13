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
)
from .forms import ManualFileForm, AircraftForm, ManualPackageForm

from django.contrib import messages
from .services import (
    process_manual_package,
    index_pdf_pages_for_manual_file,
    process_manual_package_safely,
    index_pdf_pages_for_manual_file_safely,
    parse_search_query,
    get_match_navigation,
    calculate_package_score,
    calculate_file_score,
    safe_int,
    parse_manual_search_query,
    build_manual_text_regex,
)
from django.http import FileResponse, Http404
from django.db.models import Q, Count, Min
from django.shortcuts import redirect, get_object_or_404
from dispatch.services import extract_mel_dispatch_items_from_pdf


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
            .prefetch_related("manuals", "manual_packages")
        )

        context["airbus_aircrafts"] = (
            Aircraft.objects.filter(maker="AIRBUS")
            .order_by("name")
            .prefetch_related("manuals", "manual_packages")
        )

        return context


class AircraftManualDetailView(LoginRequiredMixin, DetailView):
    model = Aircraft
    template_name = "manuals/aircraft_manual_detail.html"
    context_object_name = "aircraft"

    def get_queryset(self):
        return Aircraft.objects.order_by("name").prefetch_related(
            "manuals",
            "manual_packages",
            "manual_packages__chapters",
            "manual_packages__chapters__pages",
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
                "aircraft_manual_detail",
                kwargs={"pk": aircraft_id},
            )
        else:
            context["back_url"] = reverse_lazy("home")

        return context

    def form_valid(self, form):
        response = super().form_valid(form)

        try:
            page_count = index_pdf_pages_for_manual_file_safely(self.object)

            if page_count:
                messages.success(
                    self.request,
                    f"{self.object.manual_type} PDF Page {page_count}개 인덱싱 완료",
                )
            else:
                messages.info(
                    self.request,
                    "PDF 업로드는 완료되었지만 인덱싱할 페이지가 없습니다.",
                )

        except Exception as error:
            messages.warning(
                self.request,
                f"PDF 업로드는 완료되었지만 인덱싱 실패: {error}",
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

        try:
            page_count = index_pdf_pages_for_manual_file_safely(self.object)

            if page_count:
                messages.success(
                    self.request,
                    f"{self.object.manual_type} PDF Page {page_count}개 재인덱싱 완료",
                )
            else:
                messages.info(
                    self.request,
                    "PDF 업로드는 완료되었지만 인덱싱할 페이지가 없습니다.",
                )

        except Exception as error:
            messages.warning(
                self.request,
                f"PDF 업로드는 완료되었지만 인덱싱 실패: {error}",
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
        if self.object.file:
            self.object.file.delete(save=False)

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

        if self.object.zip_file:
            self.object.zip_file.delete(save=False)

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

            if aircraft_id:
                package_pages_qs = package_pages_qs.filter(
                    chapter__package__aircraft_id=aircraft_id
                )
                file_pages_qs = file_pages_qs.filter(
                    manual_file__aircraft_id=aircraft_id
                )

            if manual_type:
                if manual_type in ["AMM", "FIM", "IPC"]:
                    package_pages_qs = package_pages_qs.filter(
                        chapter__package__manual_type=manual_type
                    )
                    file_pages_qs = ManualFilePDFPage.objects.none()

                elif manual_type in ["MEL", "CDL"]:
                    file_pages_qs = file_pages_qs.filter(
                        manual_file__manual_type=manual_type
                    )
                    package_pages_qs = ManualPDFPage.objects.none()

            search_value, match_mode = parse_manual_search_query(query)

            if search_value:
                text_regex = build_manual_text_regex(search_value, match_mode)

                if match_mode == "contains":
                    package_pages_qs = package_pages_qs.filter(
                        text__icontains=search_value
                    )

                    file_pages_qs = file_pages_qs.filter(text__icontains=search_value)

                elif match_mode == "wildcard":
                    package_pages_qs = package_pages_qs.filter(text__iregex=text_regex)

                    file_pages_qs = file_pages_qs.filter(text__iregex=text_regex)

                else:
                    package_pages_qs = package_pages_qs.filter(text__iregex=text_regex)

                    file_pages_qs = file_pages_qs.filter(text__iregex=text_regex)

                package_pages = list(package_pages_qs)
                file_pages = list(file_pages_qs)

                highlight_query = " ".join(
                    part.strip() for part in search_value.split("*") if part.strip()
                )

                for page in package_pages:
                    page.snippet = page.get_snippet(highlight_query)
                    page.score = calculate_package_score(page, highlight_query)

                for page in file_pages:
                    page.snippet = page.get_snippet(highlight_query)
                    page.score = calculate_file_score(page, highlight_query)

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

        grouped_results = {
            "AMM": [],
            "FIM": [],
            "IPC": [],
            "MEL": [],
            "CDL": [],
        }

        for page in package_pages:
            page_manual_type = page.chapter.package.manual_type

            if page_manual_type in grouped_results:
                grouped_results[page_manual_type].append(page)

        for page in file_pages:
            page_manual_type = page.manual_file.manual_type

            if page_manual_type in grouped_results:
                grouped_results[page_manual_type].append(page)

        context["package_pages"] = package_pages
        context["file_pages"] = file_pages
        context["grouped_results"] = grouped_results

        context["aircrafts"] = Aircraft.objects.all().order_by("maker", "name")

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
        context["viewer_type"] = "chapter"
        context["view_mode"] = view_mode

        context["prev_match_url"] = None
        context["next_match_url"] = None
        context["current_match_index"] = 0
        context["match_count"] = 0
        context["matching_pages_json"] = json.dumps([])

        if query:
            search_value, match_mode = parse_manual_search_query(query)

            if search_value:
                text_regex = build_manual_text_regex(search_value, match_mode)

                if match_mode == "contains":
                    page_filter = Q(text__icontains=search_value)
                else:
                    page_filter = Q(text__iregex=text_regex)

                matches = list(
                    ManualPDFPage.objects.select_related("chapter")
                    .filter(chapter__package=chapter.package)
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
                            + f"?page={prev_item['page_number']}&q={query}&mode={view_mode}"
                        )

                    if current_index < len(matches) - 1:
                        next_item = matches[current_index + 1]
                        context["next_match_url"] = (
                            reverse(
                                "manual_chapter_pdf_viewer",
                                kwargs={"pk": next_item["chapter_id"]},
                            )
                            + f"?page={next_item['page_number']}&q={query}&mode={view_mode}"
                        )

                matching_pages_list = [
                    item["page_number"]
                    for item in matches
                    if item["chapter_id"] == chapter.pk
                ]

                context["matching_pages_json"] = json.dumps(matching_pages_list)

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

        context["viewer_title"] = f"{manual.aircraft.name} / {manual.manual_type}"
        context["viewer_subtitle"] = manual.description or "PDF Viewer"
        context["pdf_url"] = reverse("manual_file_pdf", kwargs={"pk": manual.pk})
        context["back_url"] = reverse(
            "aircraft_manual_detail",
            kwargs={"pk": manual.aircraft.pk},
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
                            reverse("manual_file_pdf_viewer", kwargs={"pk": manual.pk})
                            + f"?page={prev_page_number}&q={query}"
                        )

                    if current_index < len(matching_pages_list) - 1:
                        next_page_number = matching_pages_list[current_index + 1]
                        context["next_match_url"] = (
                            reverse("manual_file_pdf_viewer", kwargs={"pk": manual.pk})
                            + f"?page={next_page_number}&q={query}"
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

        try:
            result = process_manual_package_safely(package)

            messages.success(
                request,
                f"{package.manual_type} 재인덱싱 완료: Chapter {result['chapter_count']}개, PDF Page {result['page_count']}개",
            )

        except Exception as error:
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

            messages.success(
                request, f"{manual.manual_type} PDF Page {page_count}개 재인덱싱 완료"
            )

        except Exception as error:
            messages.error(request, f"재인덱싱 실패: {error}")

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
