# manuals/r2_upload_views.py

import json
import math

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    UserPassesTestMixin,
)
from django.http import JsonResponse
from django.views import View

from manuals.r2_multipart import (
    abort_multipart_upload,
    build_object_key,
    complete_multipart_upload,
    create_multipart_upload,
    generate_upload_part_url,
    get_content_type,
    object_exists,
    validate_upload_file,
)


class StaffRequiredMixin(UserPassesTestMixin):
    raise_exception = True

    def test_func(self):
        return self.request.user.is_staff


class R2EnabledMixin:
    def dispatch(self, request, *args, **kwargs):
        if not getattr(settings, "USE_R2", False):
            return JsonResponse(
                {"success": False, "message": "R2 직접 업로드가 비활성화되어 있습니다."},
                status=404,
            )

        return super().dispatch(request, *args, **kwargs)


def upload_session_key(upload_id):
    return f"r2_multipart_{upload_id}"


def get_upload_session(request, upload_id, object_key):
    upload_info = request.session.get(upload_session_key(upload_id))

    if not upload_info or upload_info.get("object_key") != object_key:
        raise ValueError("현재 세션의 업로드 정보와 일치하지 않습니다.")

    return upload_info


class JSONBodyMixin:
    def get_json_body(self):
        try:
            return json.loads(self.request.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("올바른 JSON 요청이 아닙니다.")


class R2MultipartInitiateView(
    R2EnabledMixin,
    LoginRequiredMixin,
    StaffRequiredMixin,
    JSONBodyMixin,
    View,
):
    """
    1. 파일 정보 검사
    2. R2 Multipart Upload 생성
    3. upload_id와 object_key 반환
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        try:
            data = self.get_json_body()

            filename = data.get("filename", "")
            file_size = data.get("file_size")
            upload_type = data.get("upload_type", "")
            aircraft_id = data.get("aircraft_id")
            manual_type = data.get("manual_type", "")

            safe_name, normalized_size, extension = validate_upload_file(
                filename=filename,
                file_size=file_size,
            )

            if upload_type not in {"file", "package"}:
                return JsonResponse(
                    {
                        "success": False,
                        "message": "올바른 업로드 종류가 아닙니다.",
                    },
                    status=400,
                )

            if upload_type == "package" and extension != ".zip":
                return JsonResponse(
                    {
                        "success": False,
                        "message": "매뉴얼 패키지는 ZIP 파일만 가능합니다.",
                    },
                    status=400,
                )

            if upload_type == "file" and extension != ".pdf":
                return JsonResponse(
                    {
                        "success": False,
                        "message": "PDF 매뉴얼은 PDF 파일만 가능합니다.",
                    },
                    status=400,
                )

            object_key = build_object_key(
                filename=safe_name,
                upload_type=upload_type,
                aircraft_id=aircraft_id,
                manual_type=manual_type,
            )

            content_type = get_content_type(extension)

            upload_id = create_multipart_upload(
                object_key=object_key,
                content_type=content_type,
            )

            request.session[upload_session_key(upload_id)] = {
                "object_key": object_key,
                "filename": safe_name,
                "file_size": normalized_size,
                "content_type": content_type,
            }

            chunk_size = settings.AWS_MULTIPART_CHUNK_SIZE
            part_count = math.ceil(normalized_size / chunk_size)

            return JsonResponse(
                {
                    "success": True,
                    "upload_id": upload_id,
                    "object_key": object_key,
                    "filename": safe_name,
                    "file_size": normalized_size,
                    "content_type": content_type,
                    "chunk_size": chunk_size,
                    "part_count": part_count,
                }
            )

        except ValueError as error:
            return JsonResponse(
                {
                    "success": False,
                    "message": str(error),
                },
                status=400,
            )

        except (BotoCoreError, ClientError):
            return JsonResponse(
                {
                    "success": False,
                    "message": "R2 업로드 시작 중 오류가 발생했습니다.",
                },
                status=502,
            )


class R2MultipartPartURLView(
    R2EnabledMixin,
    LoginRequiredMixin,
    StaffRequiredMixin,
    JSONBodyMixin,
    View,
):
    """
    특정 파트를 업로드할 Presigned URL을 발급합니다.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        try:
            data = self.get_json_body()

            object_key = data.get("object_key", "").strip()
            upload_id = data.get("upload_id", "").strip()
            part_number = int(data.get("part_number", 0))

            if not object_key or not upload_id:
                raise ValueError("업로드 정보가 누락되었습니다.")

            get_upload_session(request, upload_id, object_key)

            if part_number < 1 or part_number > 10000:
                raise ValueError("올바른 파트 번호가 아닙니다.")

            upload_url = generate_upload_part_url(
                object_key=object_key,
                upload_id=upload_id,
                part_number=part_number,
            )

            return JsonResponse(
                {
                    "success": True,
                    "part_number": part_number,
                    "upload_url": upload_url,
                }
            )

        except (TypeError, ValueError) as error:
            return JsonResponse(
                {
                    "success": False,
                    "message": str(error),
                },
                status=400,
            )

        except (BotoCoreError, ClientError):
            return JsonResponse(
                {
                    "success": False,
                    "message": "파트 업로드 URL 생성 중 오류가 발생했습니다.",
                },
                status=502,
            )


class R2MultipartCompleteView(
    R2EnabledMixin,
    LoginRequiredMixin,
    StaffRequiredMixin,
    JSONBodyMixin,
    View,
):
    """
    업로드된 파트들을 하나의 R2 객체로 완성합니다.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        try:
            data = self.get_json_body()

            object_key = data.get("object_key", "").strip()
            upload_id = data.get("upload_id", "").strip()
            parts = data.get("parts", [])

            if not object_key or not upload_id:
                raise ValueError("업로드 정보가 누락되었습니다.")

            upload_info = get_upload_session(request, upload_id, object_key)

            if not isinstance(parts, list) or not parts:
                raise ValueError("완료할 파트 정보가 없습니다.")

            response = complete_multipart_upload(
                object_key=object_key,
                upload_id=upload_id,
                parts=parts,
            )

            exists, object_info = object_exists(object_key)

            if not exists:
                return JsonResponse(
                    {
                        "success": False,
                        "message": "업로드 완료 후 R2 객체를 확인하지 못했습니다.",
                    },
                    status=502,
                )

            upload_info["completed"] = True
            request.session[upload_session_key(upload_id)] = upload_info

            return JsonResponse(
                {
                    "success": True,
                    "object_key": object_key,
                    "etag": response.get("ETag", ""),
                    "file_size": object_info.get("ContentLength", 0),
                    "content_type": object_info.get(
                        "ContentType",
                        "application/octet-stream",
                    ),
                }
            )

        except (KeyError, TypeError, ValueError) as error:
            return JsonResponse(
                {
                    "success": False,
                    "message": str(error),
                },
                status=400,
            )

        except (BotoCoreError, ClientError):
            return JsonResponse(
                {
                    "success": False,
                    "message": "R2 Multipart Upload 완료 중 오류가 발생했습니다.",
                },
                status=502,
            )


class R2MultipartAbortView(
    R2EnabledMixin,
    LoginRequiredMixin,
    StaffRequiredMixin,
    JSONBodyMixin,
    View,
):
    """
    취소되거나 실패한 Multipart Upload를 정리합니다.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        try:
            data = self.get_json_body()

            object_key = data.get("object_key", "").strip()
            upload_id = data.get("upload_id", "").strip()

            if not object_key or not upload_id:
                raise ValueError("취소할 업로드 정보가 없습니다.")

            get_upload_session(request, upload_id, object_key)

            abort_multipart_upload(
                object_key=object_key,
                upload_id=upload_id,
            )

            request.session.pop(upload_session_key(upload_id), None)

            return JsonResponse(
                {
                    "success": True,
                    "message": "업로드가 취소되었습니다.",
                }
            )

        except ValueError as error:
            return JsonResponse(
                {
                    "success": False,
                    "message": str(error),
                },
                status=400,
            )

        except (BotoCoreError, ClientError):
            return JsonResponse(
                {
                    "success": False,
                    "message": "R2 업로드 취소 중 오류가 발생했습니다.",
                },
                status=502,
            )
