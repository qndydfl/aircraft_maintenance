from django.apps import AppConfig


class ManualsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "manuals"

    def ready(self):
        from . import signals  # noqa: F401
