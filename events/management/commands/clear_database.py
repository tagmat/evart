from django.core.management.base import BaseCommand
from django.db import transaction, connection
from django.db.utils import OperationalError
from events.models import (
    Project, Domain, Event, EventType, Payload, DatabasePayload, 
    Field, DatabaseField, FieldType, Service, DatabaseTables
)


class Command(BaseCommand):
    help = 'Clear all data from the database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Confirm that you want to delete all data',
        )

    def _column_exists(self, table_name, column_name):
        """Check if a column exists in a table"""
        with connection.cursor() as cursor:
            if 'sqlite' in connection.vendor:
                cursor.execute(
                    "PRAGMA table_info({})".format(table_name)
                )
                columns = [row[1] for row in cursor.fetchall()]
                return column_name in columns
            elif 'postgresql' in connection.vendor:
                cursor.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = %s AND column_name = %s",
                    [table_name, column_name]
                )
                return cursor.fetchone() is not None
            else:
                # For other databases, assume column exists
                return True

    def handle(self, *args, **options):
        if not options['confirm']:
            self.stdout.write(
                self.style.ERROR(
                    'This command will delete ALL data from the database!\n'
                    'Use --confirm flag to proceed.'
                )
            )
            return

        with transaction.atomic():
            # Delete in reverse dependency order to avoid foreign key constraints
            
            # Clear many-to-many relationships first
            self.stdout.write('Clearing many-to-many relationships...')
            for service in Service.objects.all():
                service.consumes.clear()
                service.publishes.clear()
                service.database_payloads.clear()
            
            for table in DatabaseTables.objects.all():
                table.fields.clear()
            
            # Delete models in reverse dependency order to avoid foreign key constraints
            # DatabasePayload must be deleted before Service (due to ForeignKey)
            # DatabaseField must be deleted before DatabasePayload (due to ForeignKey)
            models_to_clear = [
                ('Events', Event),
                ('Database Fields', DatabaseField),  # Must come before DatabasePayload
                ('Database Payloads', DatabasePayload),  # Must come before Service
                ('Services', Service),
                ('Database Tables', DatabaseTables),
                ('Fields', Field),
                ('Payloads', Payload),
                ('Domains', Domain),
                ('Field Types', FieldType),
                ('Event Types', EventType),
                ('Projects', Project),
            ]
            
            # Check if service_id column exists in DatabasePayload table (for migration compatibility)
            db_payload_table = DatabasePayload._meta.db_table
            has_service_column = self._column_exists(db_payload_table, 'service_id')
            
            for model_name, model_class in models_to_clear:
                try:
                    # Skip Service and DatabasePayload deletion if service_id column doesn't exist yet
                    # (migration hasn't been run)
                    if (model_class == Service or model_class == DatabasePayload) and not has_service_column:
                        self.stdout.write(
                            self.style.WARNING(
                                f'Skipping {model_name} - database schema may not be up to date. '
                                f'Run migrations first: python manage.py migrate'
                            )
                        )
                        continue
                    
                    count = model_class.objects.count()
                    if count > 0:
                        self.stdout.write(f'Deleting {count} {model_name}...')
                        model_class.objects.all().delete()
                    else:
                        self.stdout.write(f'No {model_name} to delete.')
                except OperationalError as e:
                    # Handle case where deletion fails due to schema issues
                    if 'no such column' in str(e).lower() or 'no such table' in str(e).lower():
                        self.stdout.write(
                            self.style.WARNING(
                                f'Error deleting {model_name} - database schema may not be up to date. '
                                f'Run migrations first: python manage.py migrate'
                            )
                        )
                    else:
                        raise

        self.stdout.write(
            self.style.SUCCESS('Database cleared successfully!')
        )
