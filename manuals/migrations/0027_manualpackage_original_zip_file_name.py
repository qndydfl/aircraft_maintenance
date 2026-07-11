from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("manuals", "0026_reindexjob"),
    ]

    operations = [
        migrations.AddField(
            model_name="manualpackage",
            name="original_zip_file_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
