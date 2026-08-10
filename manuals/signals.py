import logging

from django.db import DatabaseError
from django.db.backends.signals import connection_created
from django.dispatch import receiver


logger = logging.getLogger(__name__)


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
