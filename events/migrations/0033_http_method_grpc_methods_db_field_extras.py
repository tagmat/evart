from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0032_add_databasepayload_description'),
    ]

    operations = [
        # ── Event: replace is_post with http_method ──────────────────────────
        migrations.AddField(
            model_name='event',
            name='http_method',
            field=models.CharField(
                max_length=10,
                blank=True,
                null=True,
                choices=[('get', 'GET'), ('post', 'POST'), ('put', 'PUT'), ('delete', 'DELETE'), ('patch', 'PATCH')],
                help_text="HTTP method for sync endpoints (emitted as x-http-method on the operation). Defaults to 'post' if blank.",
            ),
        ),
        migrations.RemoveField(
            model_name='event',
            name='is_post',
        ),

        # ── GRPCClient: add module and proto_service ──────────────────────────
        migrations.AddField(
            model_name='grpcclient',
            name='module',
            field=models.CharField(
                max_length=200,
                blank=True,
                null=True,
                help_text="Go import module path for this gRPC client (e.g. 'catalog'). Emitted as x-grpc-clients[].module.",
            ),
        ),
        migrations.AddField(
            model_name='grpcclient',
            name='proto_service',
            field=models.CharField(
                max_length=200,
                blank=True,
                null=True,
                help_text="Proto service name (e.g. 'CatalogPublicService'). Emitted as x-grpc-clients[].service.",
            ),
        ),

        # ── GRPCMethod: new model ─────────────────────────────────────────────
        migrations.CreateModel(
            name='GRPCMethod',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, help_text="gRPC method name (e.g. 'GetProduct').")),
                ('request', models.CharField(max_length=200, help_text="Request message type (e.g. 'GetProductRequest').")),
                ('response', models.CharField(max_length=200, help_text="Response message type (e.g. 'GetProductResponse').")),
                ('input_field', models.CharField(
                    max_length=200, blank=True, null=True,
                    help_text="Input field name for typed wrapper (e.g. 'product_id'). If blank, raw req/resp stub is forwarded.",
                )),
                ('output_field', models.CharField(
                    max_length=200, blank=True, null=True,
                    help_text="Output field name for typed wrapper (e.g. 'product'). If blank, raw req/resp stub is forwarded.",
                )),
                ('client', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='methods',
                    to='events.grpcclient',
                )),
            ],
        ),

        # ── DatabaseField: add x_preload and x_primary_key ────────────────────
        migrations.AddField(
            model_name='databasefield',
            name='x_preload',
            field=models.BooleanField(
                default=False,
                help_text="Adds 'preload' to the GORM tag so this association is eager-loaded whenever the parent is fetched.",
            ),
        ),
        migrations.AddField(
            model_name='databasefield',
            name='x_primary_key',
            field=models.BooleanField(
                default=False,
                help_text="Marks this field as the table's primary key (GORM 'primaryKey' tag).",
            ),
        ),
    ]
