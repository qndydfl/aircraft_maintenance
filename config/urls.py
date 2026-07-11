from django.contrib import admin
from django.urls import path
from django.urls import include

from django.conf import settings
from django.conf.urls.static import static
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView


urlpatterns = [

    path(
        "service-worker.js",
        never_cache(
            TemplateView.as_view(
                template_name="service-worker.js",
                content_type="application/javascript",
            )
        ),
        name="service_worker",
    ),

    path("admin/", admin.site.urls ),

    path("", include("manuals.urls")),

    path("accounts/", include("accounts.urls")),

    path("dispatch/", include("dispatch.urls")),

] 

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
