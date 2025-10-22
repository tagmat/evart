from django.core.management.base import BaseCommand
from django.db import transaction
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
            
            # Delete models in reverse dependency order
            models_to_clear = [
                ('Events', Event),
                ('Services', Service),
                ('Database Tables', DatabaseTables),
                ('Database Fields', DatabaseField),
                ('Fields', Field),
                ('Database Payloads', DatabasePayload),
                ('Payloads', Payload),
                ('Domains', Domain),
                ('Field Types', FieldType),
                ('Event Types', EventType),
                ('Projects', Project),
            ]
            
            for model_name, model_class in models_to_clear:
                count = model_class.objects.count()
                if count > 0:
                    self.stdout.write(f'Deleting {count} {model_name}...')
                    model_class.objects.all().delete()
                else:
                    self.stdout.write(f'No {model_name} to delete.')

        self.stdout.write(
            self.style.SUCCESS('Database cleared successfully!')
        )
