from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("manuals", "0029_commonmanualcategory_image"),
    ]

    operations = [
        migrations.CreateModel(
            name="ManualPackageFile",
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
                ("relative_path", models.CharField(max_length=500)),
                (
                    "folder_path",
                    models.CharField(blank=True, default="", max_length=500),
                ),
                ("file_name", models.CharField(max_length=255)),
                (
                    "extension",
                    models.CharField(blank=True, default="", max_length=20),
                ),
                ("file_size", models.PositiveBigIntegerField(default=0)),
                (
                    "preview_pdf_relative_path",
                    models.CharField(blank=True, default="", max_length=500),
                ),
                (
                    "package",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="package_files",
                        to="manuals.manualpackage",
                    ),
                ),
            ],
            options={
                "ordering": ["folder_path", "file_name"],
            },
        ),
        migrations.AddConstraint(
            model_name="manualpackagefile",
            constraint=models.UniqueConstraint(
                fields=("package", "relative_path"),
                name="unique_manual_package_file_path",
            ),
        ),
    ]
