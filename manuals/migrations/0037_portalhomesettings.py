from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("manuals", "0036_othermanualfolder"),
    ]

    operations = [
        migrations.CreateModel(
            name="PortalHomeSettings",
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
                    "hero_image",
                    models.ImageField(
                        blank=True,
                        help_text="홈페이지 상단 오른쪽에 표시할 이미지를 업로드하세요.",
                        null=True,
                        upload_to="portal_home/",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Portal Home Settings",
                "verbose_name_plural": "Portal Home Settings",
            },
        ),
    ]
