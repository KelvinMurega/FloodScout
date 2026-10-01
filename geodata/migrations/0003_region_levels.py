"""Let Region hold sub-counties as well as counties.

Existing rows are all counties, so each one's `name` is its `county_name`.
The unique constraint is added only after that backfill, when no two rows
share an empty name.
"""

import django.db.models.deletion
from django.db import migrations, models


def backfill_county_names(apps, schema_editor):
    Region = apps.get_model("geodata", "Region")
    Region.objects.filter(name="").update(name=models.F("county_name"))


class Migration(migrations.Migration):

    dependencies = [
        ("geodata", "0002_alter_geospatialfeature_distance_to_river_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="region",
            name="name",
            field=models.CharField(default="", max_length=100),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="region",
            name="level",
            field=models.CharField(
                choices=[("county", "County"), ("sub_county", "Sub-county")],
                default="county",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="region",
            name="parent",
            field=models.ForeignKey(
                blank=True,
                help_text="The county a sub-county lies in. Empty for counties.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="sub_counties",
                to="geodata.region",
            ),
        ),
        migrations.RunPython(backfill_county_names, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="region",
            constraint=models.UniqueConstraint(
                fields=("level", "name"), name="unique_region_level_name"
            ),
        ),
    ]
