# tom_skynet 0.4.0: Skynet records key the observation and the grant by uid.
#
# Rewrites the integer Skynet ids stored by earlier versions through Skynet's
# API, which accepts both until its contract deploy; see uid_migration.py and
# the runbook in SkynetRTN/skynet docs/internal/reference/TOM_UID_MIGRATION_RUNBOOK.md.
# The reverse is a no-op: Skynet keeps resolving the uids.

from django.db import migrations


def forwards(apps, schema_editor):
    from tom_skynet.uid_migration import migrate_records

    migrate_records(apps)


class Migration(migrations.Migration):
    dependencies = [
        ("tom_observations", "0016_alter_facility_options"),
        ("tom_skynet", "0003_record_access_lost"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
