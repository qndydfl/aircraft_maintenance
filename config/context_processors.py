from django.conf import settings


def site_dates(request):
    return {
        "site_created_date": getattr(settings, "SITE_CREATED_DATE", ""),
        "site_revision_date": getattr(settings, "SITE_REVISION_DATE", ""),
        "site_revision_no": getattr(settings, "SITE_REVISION_NO", ""),
    }
