from django.db import migrations, models
from django.db.models import Count, Min


def remove_duplicate_mel_dispatch_items(apps, schema_editor):
    mel_dispatch_item = apps.get_model("dispatch", "MelDispatchItem")
    identity_fields = (
        "aircraft_id",
        "manual_file_id",
        "message",
        "condition",
        "mel_item",
        "page_number",
    )
    duplicates = (
        mel_dispatch_item.objects.values(*identity_fields)
        .annotate(keep_id=Min("id"), row_count=Count("id"))
        .filter(row_count__gt=1)
    )

    for duplicate in list(duplicates):
        filters = {field: duplicate[field] for field in identity_fields}
        (
            mel_dispatch_item.objects.filter(**filters)
            .exclude(pk=duplicate["keep_id"])
            .delete()
        )


class Migration(migrations.Migration):
    dependencies = [
        ("dispatch", "0013_alter_dispatchreference_amm_task_ref_and_more"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="meldispatchitem",
            name="unique_mel_dispatch_full_row",
        ),
        migrations.RemoveField(
            model_name="meldispatchitem",
            name="level",
        ),
        migrations.RemoveField(
            model_name="meldispatchitem",
            name="adc",
        ),
        migrations.RunPython(
            remove_duplicate_mel_dispatch_items,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="meldispatchitem",
            constraint=models.UniqueConstraint(
                fields=(
                    "aircraft",
                    "manual_file",
                    "message",
                    "condition",
                    "mel_item",
                    "page_number",
                ),
                name="unique_mel_dispatch_full_row",
            ),
        ),
    ]
