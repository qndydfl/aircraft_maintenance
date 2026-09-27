"""Optional package PDF built from indexed chapters in index.html order."""

import json
import os
import uuid
from pathlib import Path

import fitz
from django.http import FileResponse, HttpResponse, HttpResponseNotModified
from django.utils.http import http_date

MANIFEST = "merged-package.json"


def load_merged_package(package):
    root = Path(package.extracted_path or "")
    if not package.extracted_path:
        return None
    try:
        data = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
        name = data["file"]
        if Path(name).name != name or not (root / name).is_file():
            return None
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def build_merged_package(package, progress=None):
    root = Path(package.extracted_path).resolve()
    chapters = list(package.chapters.order_by("pk"))
    if not chapters:
        raise ValueError("No chapters to merge")
    token = uuid.uuid4().hex
    pdf_path = root / f"merged-{token}.pdf"
    manifest_path = root / f"merged-{token}.json"
    entries, toc = [], []
    try:
        with fitz.open() as merged:
            for index, chapter in enumerate(chapters, start=1):
                source_path = (root / chapter.pdf_relative_path).resolve()
                if not source_path.is_relative_to(root):
                    raise ValueError("Chapter path outside package")
                with fitz.open(source_path) as source:
                    if source.needs_pass or not source.page_count:
                        raise ValueError(f"Cannot merge {chapter.pdf_relative_path}")
                    start = merged.page_count + 1
                    entries.append(
                        {
                            "chapterId": chapter.pk,
                            "start": start,
                            "count": source.page_count,
                        }
                    )
                    toc.append([1, chapter.title or chapter.task, start])
                    for level, title, page in source.get_toc():
                        if page > 0:
                            toc.append([level + 1, title, start + page - 1])
                    merged.insert_pdf(source)
                if progress and (index % 25 == 0 or index == len(chapters)):
                    progress(
                        f"Merged {index}/{len(chapters)} chapters, {merged.page_count} pages"
                    )
            merged.set_toc(toc)
            if progress:
                progress("Saving merged PDF...")
            merged.save(pdf_path, deflate=True)
            data = {
                "file": pdf_path.name,
                "chapters": entries,
                "page_count": merged.page_count,
            }
        manifest_path.write_text(json.dumps(data), encoding="utf-8")
        current = type(package).objects.get(pk=package.pk)
        if current.extracted_path != package.extracted_path or list(
            current.chapters.order_by("pk").values_list("pk", flat=True)
        ) != [chapter.pk for chapter in chapters]:
            raise ValueError(
                "Package changed during merge; retry after indexing completes"
            )
        os.replace(manifest_path, root / MANIFEST)
        return data
    except Exception:
        pdf_path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        raise


def merged_pdf_response(request, path):
    """Serve byte ranges so PDF.js can request pages without a full download."""
    stat = path.stat()
    size = stat.st_size
    etag = f'"{size:x}-{stat.st_mtime_ns:x}"'
    value = request.headers.get("Range", "")
    if not value and request.headers.get("If-None-Match") == etag:
        return HttpResponseNotModified()

    if not value:
        response = FileResponse(path.open("rb"), content_type="application/pdf")
    else:
        import re
        from django.http import StreamingHttpResponse

        match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
        try:
            if not match or not any(match.groups()):
                raise ValueError
            first, last = match.groups()
            start = int(first) if first else max(0, size - int(last))
            end = min(int(last), size - 1) if first and last else size - 1
            if start > end or start >= size:
                raise ValueError
        except ValueError:
            return HttpResponse(
                status=416, headers={"Content-Range": f"bytes */{size}"}
            )

        def chunks():
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        response = StreamingHttpResponse(
            chunks(), status=206, content_type="application/pdf"
        )
        response["Content-Length"] = str(end - start + 1)
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
    response["Accept-Ranges"] = "bytes"
    response["ETag"] = etag
    response["Last-Modified"] = http_date(stat.st_mtime)
    if request.GET.get("version") == path.name:
        response["Cache-Control"] = "private, max-age=31536000, immutable"
    else:
        response["Cache-Control"] = "private, max-age=0, must-revalidate"
    return response
