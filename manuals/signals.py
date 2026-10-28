import logging

from django.db import DatabaseError
from django.db.backends.signals import connection_created
from django.dispatch import receiver
from django.db.models.signals import post_save

from .models import Aircraft, OtherManualCategory


logger = logging.getLogger(__name__)


@receiver(post_save, sender=Aircraft)
def create_aircraft_other_manual_category(sender, instance, created, **kwargs):
    if created:
        OtherManualCategory.objects.get_or_create(
            aircraft=instance,
            manual_type="OTHER",
            defaults={
                "name": "OTHER",
                "code": "OTHER",
            },
        )


@receiver(connection_created)
def configure_sqlite_connection(sender, connection, **kwargs):
    if connection.vendor != "sqlite":
        return

    database_name = str(connection.settings_dict.get("NAME", ""))

    try:
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA busy_timeout = 30000")

            if "mode=memory" not in database_name and database_name != ":memory:":
                cursor.execute("PRAGMA journal_mode = WAL")

            cursor.execute("PRAGMA synchronous = NORMAL")
    except DatabaseError as error:
        logger.warning("Could not configure SQLite WAL mode: %s", error)
