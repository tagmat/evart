# Generated by Django 4.2 on 2023-04-18 20:42

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Domain',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
            ],
        ),
        migrations.CreateModel(
            name='Event',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('domain', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='events.domain')),
            ],
        ),
        migrations.CreateModel(
            name='EventType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
            ],
        ),
        migrations.CreateModel(
            name='Project',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
            ],
        ),
        migrations.CreateModel(
            name='Service',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('consumes', models.ManyToManyField(related_name='event_consumers', to='events.event')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='events.project')),
                ('publishes', models.ManyToManyField(related_name='event_publishers', to='events.event')),
            ],
        ),
        migrations.CreateModel(
            name='Payload',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='events.project')),
            ],
        ),
        migrations.CreateModel(
            name='FieldType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('custom_type', models.BooleanField(default=False)),
                ('enum_choices', models.CharField(blank=True, max_length=1000, null=True)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='events.project')),
            ],
        ),
        migrations.CreateModel(
            name='Field',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('required', models.BooleanField(default=False)),
                ('minimum', models.IntegerField(blank=True, null=True)),
                ('maximum', models.IntegerField(blank=True, null=True)),
                ('description', models.CharField(blank=True, max_length=200, null=True)),
                ('payload', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='events.payload')),
                ('type', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, to='events.fieldtype')),
            ],
        ),
        migrations.AddField(
            model_name='event',
            name='payload',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='events.payload'),
        ),
        migrations.AddField(
            model_name='event',
            name='response_payload',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='response_payload', to='events.payload'),
        ),
        migrations.AddField(
            model_name='event',
            name='type',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, to='events.eventtype'),
        ),
        migrations.AddField(
            model_name='domain',
            name='project',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='events.project'),
        ),
    ]
