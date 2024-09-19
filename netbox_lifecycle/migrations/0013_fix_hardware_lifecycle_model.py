# Generated by Django 5.0.8 on 2024-09-19 03:35

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("netbox_lifecycle", "0012_primarymodels"),
    ]

    operations = [
        migrations.AlterField(
            model_name="hardwarelifecycle",
            name="assigned_object_type",
            field=models.ForeignKey(
                blank=True,
                limit_choices_to=models.Q(
                    ("app_label", "dcim"), ("model__in", ("moduletype", "devicetype"))
                ),
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="contenttypes.contenttype",
            ),
        ),
    ]
