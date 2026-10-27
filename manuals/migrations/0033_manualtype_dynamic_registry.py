from django.db import migrations, models


DEFAULT_MANUAL_TYPES = (
    ("AMM", "AMM", "ZIP", 10),
    ("FIM", "FIM", "ZIP", 20),
    ("IPC", "IPC", "ZIP", 30),
    ("WDM", "WDM", "ZIP", 40),
    ("SSM", "SSM", "ZIP", 50),
    ("MEL", "MEL", "PDF", 60),
    ("CDL", "CDL", "PDF", 70),
    ("OTHER", "OTHER", "FOLDER", 80),
)


def create_default_manual_types(apps, schema_editor):
    ManualType = apps.get_model("manuals", "ManualType")

    for code, name, upload_mode, sort_order in DEFAULT_MANUAL_TYPES:
        ManualType.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "upload_mode": upload_mode,
                "sort_order": sort_order,
                "is_active": True,
                "is_searchable": True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("manuals", "0032_add_wdm_ssm_manual_package_types"),
    ]

    operations = [
        migrations.CreateModel(
            name="ManualType",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("code", models.CharField(max_length=20, unique=True)),
                ("name", models.CharField(max_length=100)),
                (
                    "upload_mode",
                    models.CharField(
                        choices=[
                            ("ZIP", "ZIP package"),
                            ("PDF", "PDF file"),
                            ("FOLDER", "Folder"),
                        ],
                        max_length=20,
                    ),
                ),
                ("sort_order", models.PositiveIntegerField(default=100)),
                ("is_active", models.BooleanField(default=True)),
                ("is_searchable", models.BooleanField(default=True)),
            ],
            options={
                "ordering": ["sort_order", "code"],
            },
        ),
        migrations.AlterField(
            model_name="manualfile",
            name="manual_type",
            field=models.CharField(max_length=20),
        ),
        migrations.AlterField(
            model_name="manualpackage",
            name="manual_type",
            field=models.CharField(max_length=20),
        ),
        migrations.RunPython(
            create_default_manual_types,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
