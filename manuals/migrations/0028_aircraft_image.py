from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("manuals", "0027_manualpackage_original_zip_file_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="aircraft",
            name="image",
            field=models.ImageField(
                blank=True,
                help_text="Aircraft image shown on the Manual Library card.",
                null=True,
                upload_to="aircraft_images/",
            ),
        ),
    ]
