from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("manuals", "0030_manualpackagefile"),
    ]

    operations = [
        migrations.AddField(
            model_name="othermanualfile",
            name="folder_path",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="othermanualfile",
            name="relative_path",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AlterModelOptions(
            name="othermanualfile",
            options={"ordering": ["folder_path", "title"]},
        ),
    ]
