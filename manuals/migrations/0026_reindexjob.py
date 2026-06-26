from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("manuals", "0025_othermanualfile_converted_pdf"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReindexJob",
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
                (
                    "target_type",
                    models.CharField(
                        choices=[
                            ("manual_package", "Manual Package"),
                            ("manual_file", "Manual File"),
                            ("common_file", "Common File"),
                            ("other_file", "Other File"),
                        ],
                        max_length=30,
                    ),
                ),
                ("target_id", models.PositiveIntegerField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("processing", "Processing"),
                            ("done", "Done"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="queued",
                        max_length=20,
                    ),
                ),
                ("message", models.CharField(blank=True, default="", max_length=255)),
                ("page_count", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["target_type", "target_id", "-created_at"],
                        name="manuals_rei_target_f70ac5_idx",
                    ),
                    models.Index(
                        fields=["status", "created_at"],
                        name="manuals_rei_status_9b0c60_idx",
                    ),
                ],
            },
        ),
    ]
