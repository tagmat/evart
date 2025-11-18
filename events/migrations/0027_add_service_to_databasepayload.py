# Generated manually to handle existing data

from django.db import migrations, models
import django.db.models.deletion


def populate_service_for_existing_payloads(apps, schema_editor):
    """Populate service field for existing DatabasePayload objects"""
    DatabasePayload = apps.get_model('events', 'DatabasePayload')
    Service = apps.get_model('events', 'Service')
    
    db = schema_editor.connection.alias
    
    orphaned_payloads = []
    for db_payload in DatabasePayload.objects.using(db).all():
        # Use the first service in the same project
        # This is a reasonable default since database payloads are typically
        # associated with services in the same project
        project_service = Service.objects.using(db).filter(project=db_payload.project).first()
        if project_service:
            db_payload.service = project_service
            db_payload.save(using=db)
        else:
            # Collect orphaned payloads for error reporting
            orphaned_payloads.append(f"{db_payload.name} (project: {db_payload.project.name})")
    
    if orphaned_payloads:
        raise ValueError(
            f"Cannot assign services to the following DatabasePayload objects because no services exist in their projects:\n"
            f"{', '.join(orphaned_payloads)}\n"
            f"Please create services for these projects or delete the orphaned DatabasePayload objects before running this migration."
        )


def reverse_populate_service(apps, schema_editor):
    """Reverse migration - nothing to do as we're removing the field"""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0026_add_schema_definition_to_fieldtype'),
    ]

    operations = [
        # Step 1: Add service field as nullable
        migrations.AddField(
            model_name='databasepayload',
            name='service',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='database_payloads',
                to='events.service',
                help_text='Service that owns this database object'
            ),
        ),
        # Step 2: Populate existing records
        migrations.RunPython(populate_service_for_existing_payloads, reverse_populate_service),
        # Step 3: Make field non-nullable
        migrations.AlterField(
            model_name='databasepayload',
            name='service',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='database_payloads',
                to='events.service',
                help_text='Service that owns this database object'
            ),
        ),
        # Step 4: Add unique_together constraint
        migrations.AlterUniqueTogether(
            name='databasepayload',
            unique_together={('service', 'name')},
        ),
    ]

