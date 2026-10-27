from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("manuals", "0031_othermanualfile_folder_paths"),
    ]

    operations = [
        migrations.AlterField(
            model_name="manualpackage",
            name="manual_type",
            field=models.CharField(
                choices=[
                    ("AMM", "AMM"),
                    ("FIM", "FIM"),
                    ("IPC", "IPC"),
                    ("WDM", "WDM"),
                    ("SSM", "SSM"),
                ],
                max_length=20,
            ),
        ),
    ]
