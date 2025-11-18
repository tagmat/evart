from django.core.management.base import BaseCommand
from django.db import transaction, connection
from django.core.management import call_command
from events.models import (
    Project, Domain, Event, EventType, Payload, DatabasePayload, 
    Field, DatabaseField, FieldType, Service, DatabaseTables
)


class Command(BaseCommand):
    help = 'Clean the database with various options'

    def add_arguments(self, parser):
        parser.add_argument(
            '--data-only',
            action='store_true',
            help='Only clear data, keep database structure',
        )
        parser.add_argument(
            '--reset-sequences',
            action='store_true',
            help='Reset auto-increment sequences after clearing data',
        )
        parser.add_argument(
            '--optimize',
            action='store_true',
            help='Optimize database tables (SQLite: VACUUM, PostgreSQL: VACUUM ANALYZE)',
        )
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Confirm that you want to clean the database',
        )
        parser.add_argument(
            '--migrate',
            action='store_true',
            help='Run migrations after cleaning (useful for --data-only)',
        )

    def handle(self, *args, **options):
        if not options['confirm']:
            self.stdout.write(
                self.style.ERROR(
                    'This command will clean the database!\n'
                    'Use --confirm flag to proceed.\n'
                    'Available options:\n'
                    '  --data-only: Clear data but keep structure\n'
                    '  --reset-sequences: Reset auto-increment sequences\n'
                    '  --optimize: Optimize database tables\n'
                    '  --migrate: Run migrations after cleaning'
                )
            )
            return

        # Clear data if requested
        if options['data_only']:
            self.clear_data()
        
        # Reset sequences if requested
        if options['reset_sequences']:
            self.reset_sequences()
        
        # Optimize database if requested
        if options['optimize']:
            self.optimize_database()
        
        # Run migrations if requested
        if options['migrate']:
            self.run_migrations()

        self.stdout.write(
            self.style.SUCCESS('Database cleaning completed successfully!')
        )

    def clear_data(self):
        """Clear all data from the database while preserving structure"""
        self.stdout.write('Clearing database data...')
        
        with transaction.atomic():
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
            
            for model_name, model_class in models_to_clear:
                count = model_class.objects.count()
                if count > 0:
                    self.stdout.write(f'Deleting {count} {model_name}...')
                    model_class.objects.all().delete()
                else:
                    self.stdout.write(f'No {model_name} to delete.')

    def reset_sequences(self):
        """Reset auto-increment sequences for all tables"""
        self.stdout.write('Resetting auto-increment sequences...')
        
        with connection.cursor() as cursor:
            # Get all tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
            tables = cursor.fetchall()
            
            for (table_name,) in tables:
                try:
                    # Reset the sequence for each table
                    cursor.execute(f"DELETE FROM sqlite_sequence WHERE name='{table_name}';")
                    self.stdout.write(f'Reset sequence for {table_name}')
                except Exception as e:
                    self.stdout.write(
                        self.style.WARNING(f'Could not reset sequence for {table_name}: {e}')
                    )

    def optimize_database(self):
        """Optimize the database based on the database engine"""
        self.stdout.write('Optimizing database...')
        
        with connection.cursor() as cursor:
            try:
                # For SQLite, use VACUUM
                if 'sqlite' in connection.vendor:
                    cursor.execute("VACUUM;")
                    self.stdout.write('SQLite database vacuumed successfully')
                # For PostgreSQL, use VACUUM ANALYZE
                elif 'postgresql' in connection.vendor:
                    cursor.execute("VACUUM ANALYZE;")
                    self.stdout.write('PostgreSQL database vacuumed and analyzed successfully')
                # For MySQL, use OPTIMIZE TABLE
                elif 'mysql' in connection.vendor:
                    cursor.execute("SHOW TABLES;")
                    tables = cursor.fetchall()
                    for (table_name,) in tables:
                        cursor.execute(f"OPTIMIZE TABLE {table_name};")
                    self.stdout.write('MySQL tables optimized successfully')
                else:
                    self.stdout.write(
                        self.style.WARNING(f'Database optimization not supported for {connection.vendor}')
                    )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'Error optimizing database: {e}')
                )

    def run_migrations(self):
        """Run Django migrations"""
        self.stdout.write('Running migrations...')
        try:
            call_command('migrate', verbosity=1)
            self.stdout.write('Migrations completed successfully')
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error running migrations: {e}')
            )
