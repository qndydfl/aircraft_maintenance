from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("manuals", "0028_aircraft_image"),
    ]

    operations = [
        migrations.AddField(
            model_name="commonmanualcategory",
            name="image",
            field=models.ImageField(
                blank=True,
                help_text="Image shown on the Common Manuals library card.",
                null=True,
                upload_to="common_manual_images/",
            ),
        ),
    ]
