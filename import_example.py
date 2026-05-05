"""
Direct import of example_full_service.yaml into evart DB.
Run with: python manage.py shell < import_example.py
"""
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'evart.settings')

import yaml
from events.models import (
    Project, Domain, EventType, Event, Payload, Field, FieldType,
    Service, DatabasePayload, DatabaseField,
    HTTPClient, HTTPClientField, GRPCClient, DbOperation
)

YAML_PATH = '/Users/ustunova/Desktop/Gitlab/golang-template/test/example_full_service.yaml'

with open(YAML_PATH) as f:
    data = yaml.safe_load(f)

info = data['info']
schemas = data['components']['schemas']
messages = data['components']['messages']
channels = data['channels']
operations = data['operations']

# ── helpers ──────────────────────────────────────────────────────────────────

def get_or_create_field_type(project, name, type_str, format_str=None, max_length=None,
                              enum_choices=None, custom=False, x_type=None):
    ft, created = FieldType.objects.get_or_create(
        project=project, name=name,
        defaults={
            'type': type_str,
            'format': format_str,
            'max_length': max_length,
            'enum_choices': ','.join(enum_choices) if enum_choices else None,
            'custom_type': custom,
            'protobuf_type': type_str,
            'x_type': x_type,
        }
    )
    if not created and x_type and not ft.x_type:
        ft.x_type = x_type
        ft.save()
    return ft

def create_fields_for_payload(payload, props, project):
    """Create Field objects from a properties dict for a Payload."""
    for fname, fdata in props.items():
        if '$ref' in fdata:
            # schema ref field
            ft = get_or_create_field_type(project, 'object', 'object')
            Field.objects.get_or_create(
                payload=payload, name=fname,
                defaults={'type': ft, 'schema_ref': fdata['$ref']}
            )
            continue

        raw_type = fdata.get('type', 'string')
        fmt = fdata.get('format')
        max_len = fdata.get('maxLength')
        enums = fdata.get('enum')
        x_type = fdata.get('x-type')  # None if not set

        if enums:
            ft = get_or_create_field_type(project, fname, 'string',
                                          enum_choices=enums, custom=True)
        elif x_type and x_type != raw_type:
            # Use x-type value as the FieldType name so it round-trips correctly
            ft = get_or_create_field_type(project, x_type, raw_type,
                                          format_str=fmt, x_type=x_type)
        elif fmt:
            ft_name = f"{raw_type}_{fmt}"
            ft = get_or_create_field_type(project, ft_name, raw_type, format_str=fmt)
        else:
            ft = get_or_create_field_type(project, raw_type, raw_type, max_length=max_len)

        items = fdata.get('items', {})
        array_items_type = items.get('type') if items and 'type' in items else None
        array_items_ref = items.get('$ref') if items and '$ref' in items else None

        field, created = Field.objects.get_or_create(
            payload=payload, name=fname,
            defaults={
                'type': ft,
                'description': fdata.get('description', ''),
                'array_items_type': array_items_type,
                'array_items_ref': array_items_ref,
            }
        )
        if not created:
            field.type = ft
            field.description = fdata.get('description', '')
            field.array_items_type = array_items_type
            field.array_items_ref = array_items_ref
            field.save()

# ── project + service ─────────────────────────────────────────────────────────
project, _ = Project.objects.get_or_create(name='Demo', slug_name='demo')
domain, _ = Domain.objects.get_or_create(project=project, name='default')
event_type, _ = EventType.objects.get_or_create(name='event')

service, _ = Service.objects.get_or_create(
    project=project, slug_name='demoservice',
    defaults={
        'name': 'Demo Service',
        'asyncapi_version': data.get('asyncapi', '3.0.0'),
        'version': info.get('version', '1.0.0'),
        'description': info.get('description', ''),
        'original_title': info.get('title', ''),
        'x_general_name': info.get('x-general-name', ''),
        'x_service_name': info.get('x-service-name', ''),
        'x_service_ip': str(info.get('x-service-ip', '')),
        'x_transport': info.get('x-transport', ''),
    }
)
# Always update metadata
service.x_general_name = info.get('x-general-name', '')
service.x_service_name = info.get('x-service-name', '')
service.x_service_ip = str(info.get('x-service-ip', ''))
service.x_transport = info.get('x-transport', '')
service.original_title = info.get('title', '')
service.description = info.get('description', '')
service.save()

# ── HTTPClients ───────────────────────────────────────────────────────────────
# Clear and re-create
HTTPClient.objects.filter(service=service).delete()
for client_data in info.get('x-http-clients', []):
    client = HTTPClient.objects.create(service=service, name=client_data['name'])
    for field_data in client_data.get('fields', []):
        HTTPClientField.objects.create(client=client, name=field_data['name'])

# ── GRPCClients ───────────────────────────────────────────────────────────────
GRPCClient.objects.filter(service=service).delete()
for grpc_data in info.get('x-grpc-clients', []):
    GRPCClient.objects.create(service=service, name=grpc_data['name'])

# ── payloads for sync events ──────────────────────────────────────────────────
# Create payloads from Request/Response body schemas
def get_sync_payload_from_schema(schema_name, project):
    """Create a Payload from a RequestBody or ResponseBody schema."""
    schema = schemas.get(schema_name, {})
    payload, _ = Payload.objects.get_or_create(
        project=project, name=schema_name,
        defaults={'description': schema.get('description', '')}
    )
    create_fields_for_payload(payload, schema.get('properties', {}), project)
    return payload

# ── payloads for async events ─────────────────────────────────────────────────
def get_async_payload(payload_name, project):
    """Get or create a Payload for an async event from its Data_* schema."""
    camel_name = payload_name[0].lower() + payload_name[1:]
    data_schema_name = f"Data_{camel_name}"
    data_schema = schemas.get(data_schema_name, {})
    description = data_schema.get('description', '')

    payload, _ = Payload.objects.get_or_create(
        project=project, name=payload_name,
        defaults={'description': description}
    )
    create_fields_for_payload(payload, data_schema.get('properties', {}), project)
    return payload

# ── events ────────────────────────────────────────────────────────────────────
# Build JWT lookup from operations
op_jwt = {}
op_endpoint = {}
op_action = {}
for op_key, op_data in operations.items():
    ch_ref = op_data.get('channel', {}).get('$ref', '')
    ch_name = ch_ref.split('/')[-1] if ch_ref else ''
    if ch_name:
        if 'x-jwt' in op_data:
            op_jwt[ch_name] = op_data['x-jwt']
        if 'x-endpoint' in op_data:
            op_endpoint[ch_name] = op_data['x-endpoint']
        op_action[ch_name] = op_data.get('action', 'receive')

created_events = {}  # channel_key -> Event

for ch_key, ch_data in channels.items():
    is_sync = ch_data.get('x-is-sync', False)
    is_post = ch_data.get('x-is-post', False)
    address = ch_data.get('address', '')
    description = ch_data.get('description', '')
    summary = ch_data.get('summary', '')

    # Strip GeneralName prefix: "Demoapi.listProducts" -> "listProducts"
    if '.' in ch_key:
        camel_event = ch_key.split('.', 1)[1]
    else:
        camel_event = ch_key

    # camelCase -> snake_case
    import re
    snake_name = re.sub(r'([A-Z])', r'_\1', camel_event).lower().lstrip('_')

    is_jwt = op_jwt.get(ch_key, False)
    endpoint = op_endpoint.get(ch_key, f"/{camel_event}")

    request_payload = None
    response_payload = None

    ch_msgs = ch_data.get('messages', {})

    if is_sync:
        # Look for Request and Response messages
        for msg_name in ch_msgs:
            if msg_name in messages:
                payload_ref = messages[msg_name].get('payload', {}).get('$ref', '')
                schema_name = payload_ref.split('/')[-1] if payload_ref else ''
                if msg_name.endswith('Response'):
                    response_payload = get_sync_payload_from_schema(schema_name, project)
                else:
                    request_payload = get_sync_payload_from_schema(schema_name, project)
    else:
        # Async: look for the payload message -> Envelope schema -> Data_* schema
        for msg_name in ch_msgs:
            if msg_name in messages:
                payload_ref = messages[msg_name].get('payload', {}).get('$ref', '')
                schema_name = payload_ref.split('/')[-1] if payload_ref else ''
                # e.g., "UserLifecycleEnvelope" -> payload_name = "UserLifecycle"
                if schema_name.endswith('Envelope'):
                    payload_name = schema_name[:-len('Envelope')]
                elif schema_name.endswith('Payload'):
                    payload_name = schema_name[:-len('Payload')]
                else:
                    payload_name = msg_name.replace('Payload', '')
                request_payload = get_async_payload(payload_name, project)
                break  # async channels only have one payload message

    event, _ = Event.objects.get_or_create(
        domain=domain, name=snake_name,
        defaults={
            'type': event_type,
            'payload': request_payload,
            'response_payload': response_payload,
            'is_sync': is_sync,
            'is_post': is_post,
            'is_jwt': is_jwt,
            'address': address,
            'endpoint': endpoint,
            'description': description,
            'summary': summary,
        }
    )
    # Always update
    event.payload = request_payload
    event.response_payload = response_payload
    event.is_sync = is_sync
    event.is_post = is_post
    event.is_jwt = is_jwt
    event.address = address
    event.endpoint = endpoint
    event.description = description
    event.summary = summary
    event.save()

    created_events[ch_key] = event

    # Import x-db-operation
    db_op_data = ch_data.get('x-db-operation')
    if db_op_data and not is_sync:
        DbOperation.objects.filter(event=event).delete()
        DbOperation.objects.create(
            event=event,
            type=db_op_data.get('type', ''),
            schema=db_op_data.get('schema', ''),
            lookup_field=db_op_data.get('lookup-field', ''),
            lookup_field_2=db_op_data.get('lookup-field-2', None),
            status=db_op_data.get('status', None),
            function_name=db_op_data.get('function-name', None),
        )

# ── set consumes/publishes ────────────────────────────────────────────────────
service.consumes.clear()
service.publishes.clear()
for ch_key, event in created_events.items():
    action = op_action.get(ch_key, 'receive')
    if action == 'receive':
        service.consumes.add(event)
    else:
        service.publishes.add(event)

# ── database payloads ─────────────────────────────────────────────────────────
ALL_DB_STR_PROPS = [
    ('x-type', 'x_type'), ('x-type-override', 'x_type_override'),
    ('x-column', 'x_column'), ('x-comment', 'x_comment'),
    ('x-serializer', 'x_serializer'), ('x-check', 'x_check'),
    ('x-default', 'x_type'),  # handled separately as default_value
    ('x-foreign-key', 'x_foreign_key'), ('x-references', 'x_references'),
    ('x-cascade-update', 'x_cascade_update'), ('x-cascade-delete', 'x_cascade_delete'),
    ('x-many-to-many', 'x_many_to_many'), ('x-join-table', 'x_join_table'),
    ('x-join-foreign-key', 'x_join_foreign_key'), ('x-join-references', 'x_join_references'),
    ('x-embedded-prefix', 'x_embedded_prefix'),
    ('x-polymorphic', 'x_polymorphic'), ('x-polymorphic-value', 'x_polymorphic_value'),
    ('x-association-foreign-key', 'x_association_foreign_key'),
    ('x-constraint', 'x_constraint'),
    ('x-relation-schema-id', 'x_relation_schema_id'),
]
ALL_DB_BOOL_PROPS = [
    ('x-unique', 'x_unique'), ('x-index', 'x_index'),
    ('x-unique-index', 'x_unique_index'), ('x-not-null', 'x_not_null'),
    ('x-nullable', 'x_nullable'), ('x-ignore', 'x_ignore'),
    ('x-auto-create-time', 'x_auto_create_time'),
    ('x-auto-update-time', 'x_auto_update_time'),
    ('x-auto-increment', 'x_auto_increment'),
    ('x-embedded', 'x_embedded'),
    ('x-association-autocreate', 'x_association_autocreate'),
    ('x-association-autoupdate', 'x_association_autoupdate'),
    ('x-association-save-reference', 'x_association_save_reference'),
]
ALL_DB_INT_PROPS = [
    ('x-size', 'x_size'), ('x-precision', 'x_precision'), ('x-scale', 'x_scale'),
]

for schema_name, schema_data in schemas.items():
    if not schema_name.startswith('DB_'):
        continue

    db_name = schema_name[len('DB_'):]
    db_payload, _ = DatabasePayload.objects.get_or_create(
        project=project, service=service, name=db_name,
        defaults={
            'description': schema_data.get('description') or None,
            'create_rest': schema_data.get('x-create-rest', False),
            'x_parser_schema_id': schema_data.get('x-parser-schema-id', ''),
            'x_derives_from': schema_data.get('x-derives-from', ''),
        }
    )
    db_payload.description = schema_data.get('description') or None
    db_payload.create_rest = schema_data.get('x-create-rest', False)
    db_payload.x_parser_schema_id = schema_data.get('x-parser-schema-id', '')
    db_payload.x_derives_from = schema_data.get('x-derives-from', '')
    db_payload.save()

    for field_name, field_data in schema_data.get('properties', {}).items():
        raw_type = field_data.get('type', 'string')
        fmt = field_data.get('format')

        # Get or create FieldType
        ft_name = raw_type
        if fmt:
            ft_name = f"{raw_type}_{fmt}"
        if field_data.get('x-type'):
            ft_name = field_data['x-type']

        ft, _ = FieldType.objects.get_or_create(
            project=project, name=ft_name,
            defaults={
                'type': raw_type,
                'format': fmt,
                'protobuf_type': raw_type,
            }
        )

        db_field_defaults = {
            'type': ft,
            'description': field_data.get('description', '') or '',
            'default_value': field_data.get('x-default'),  # x-default → default_value
        }

        # String props
        for yaml_key, model_field in ALL_DB_STR_PROPS:
            if yaml_key == 'x-default':
                continue  # handled above
            val = field_data.get(yaml_key)
            if val:
                db_field_defaults[model_field] = val

        # Bool props
        for yaml_key, model_field in ALL_DB_BOOL_PROPS:
            val = field_data.get(yaml_key, False)
            if val:
                db_field_defaults[model_field] = True

        # Int props
        for yaml_key, model_field in ALL_DB_INT_PROPS:
            val = field_data.get(yaml_key)
            if val is not None:
                db_field_defaults[model_field] = val

        db_field, created = DatabaseField.objects.get_or_create(
            payload=db_payload, name=field_name,
            defaults=db_field_defaults
        )
        if not created:
            for k, v in db_field_defaults.items():
                setattr(db_field, k, v)
            db_field.save()

    if not service.database_payloads.filter(id=db_payload.id).exists():
        service.database_payloads.add(db_payload)

print(f"✓ Imported service: {service.name} (id={service.id})")
print(f"  x_transport={service.x_transport}")
print(f"  http_clients={list(service.http_clients.values_list('name', flat=True))}")
print(f"  grpc_clients={list(service.grpc_clients.values_list('name', flat=True))}")
print(f"  consumes={service.consumes.count()} events")
print(f"  publishes={service.publishes.count()} events")
print(f"  database_payloads={service.database_payloads.count()}")
print(f"\nService ID: {service.id}")
