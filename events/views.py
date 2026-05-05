import yaml
import re
import json
import logging
import time
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.db import transaction, connection
from django.db.utils import OperationalError, IntegrityError, DatabaseError
from django.shortcuts import HttpResponse, render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib import messages

from events.models import *

logger = logging.getLogger(__name__)


def create_or_get_field_type(project, field_data, created_field_types):
    """Helper function to create or get field type with comprehensive handling"""
    try:
        raw_type = field_data.get('type', 'string')
        x_type = field_data.get('x-type')

        # Handle enum types
        if 'enum' in field_data:
            enum_choices = ','.join(str(v) for v in field_data.get('enum', []))
            # Create a unique name for enum types
            enum_type_name = f"{raw_type}_enum_{hash(enum_choices) % 10000}"
            field_type = _safe_get_or_create_field_type(
                project=project,
                name=enum_type_name,
                defaults={
                    'type': 'string',
                    'custom_type': True,
                    'enum_choices': enum_choices,
                    'format': field_data.get('format'),
                    'max_length': field_data.get('maxLength')
                },
                created_field_types=created_field_types
            )
        else:
            format_value = field_data.get('format')
            # Use x-type as FieldType name when present — enables round-trip for int64, uuid, etc.
            if x_type and x_type != raw_type:
                ft_name = x_type
            elif format_value:
                ft_name = f"{raw_type}_{format_value}"
            else:
                ft_name = raw_type

            field_type = _safe_get_or_create_field_type(
                project=project,
                name=ft_name,
                defaults={
                    'type': raw_type,
                    'x_type': x_type,
                    'custom_type': False,
                    'format': format_value,
                    'max_length': field_data.get('maxLength')
                },
                created_field_types=created_field_types
            )
            # Update x_type on existing record if it wasn't stored before
            if field_type and x_type and not field_type.x_type:
                field_type.x_type = x_type
                field_type.save(update_fields=['x_type'])

        return field_type
    except Exception as e:
        # Log error but return a default field type to prevent blocking the import
        logger.error(f"Failed to create/get FieldType: {str(e)}")
        return _get_fallback_field_type(project)


def _safe_get_or_create_field_type(project, name, defaults, created_field_types):
    """Safely get or create a FieldType with proper transaction and connection handling"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Try to get existing first
            try:
                field_type = FieldType.objects.get(project=project, name=name)
                return field_type
            except FieldType.DoesNotExist:
                # Doesn't exist, try to create it
                # Use a savepoint to ensure IntegrityError doesn't abort the outer transaction
                try:
                    with transaction.atomic():
                        field_type = FieldType.objects.create(
                            project=project,
                            name=name,
                            **defaults
                        )
                    created_field_types.append(field_type)
                    return field_type
                except (IntegrityError, OperationalError) as db_error:
                    # If creation fails due to constraint, try to get it again
                    # (might have been created by another process or concurrent request)
                    if attempt < max_retries - 1:
                        time.sleep(0.01 * (attempt + 1))  # Exponential backoff
                        continue
                    else:
                        # Last attempt - try to get it one more time
                        try:
                            return FieldType.objects.get(project=project, name=name)
                        except FieldType.DoesNotExist:
                            logger.error(f"FieldType {name} does not exist and cannot be created after {max_retries} attempts: {str(db_error)}")
                            return _get_fallback_field_type(project)
                except (DatabaseError, Exception) as db_error:
                    # For other database errors, retry
                    if attempt < max_retries - 1:
                        logger.warning(f"Database error creating FieldType {name} (attempt {attempt + 1}): {str(db_error)}")
                        time.sleep(0.01 * (attempt + 1))
                        continue
                    else:
                        logger.error(f"Failed to create FieldType {name} after {max_retries} attempts: {str(db_error)}")
                        return _get_fallback_field_type(project)
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Unexpected error creating FieldType {name} (attempt {attempt + 1}): {str(e)}")
                time.sleep(0.01 * (attempt + 1))
                continue
            else:
                logger.error(f"Failed to create FieldType {name} after {max_retries} attempts: {str(e)}")
                return _get_fallback_field_type(project)
    
    # Should never reach here, but just in case
    return _get_fallback_field_type(project)


def _get_fallback_field_type(project):
    """Get or create a default string field type as fallback"""
    try:
        # Try to get existing first
        try:
            return FieldType.objects.get(project=project, name='string')
        except FieldType.DoesNotExist:
            # Doesn't exist, try to create it
            # Use a savepoint to ensure IntegrityError doesn't abort the outer transaction
            try:
                with transaction.atomic():
                    return FieldType.objects.create(
                        project=project,
                        name='string',
                        type='string',
                        custom_type=False
                    )
            except (IntegrityError, OperationalError):
                # If creation fails, try to get it again
                try:
                    return FieldType.objects.get(project=project, name='string')
                except FieldType.DoesNotExist:
                    pass
            except Exception as create_error:
                logger.warning(f"Fallback FieldType creation failed: {str(create_error)}")
        
        # Try to get any existing string type
        try:
            return FieldType.objects.filter(project=project, name='string').first() or \
                   FieldType.objects.filter(project=project, type='string').first() or \
                   FieldType.objects.filter(project=project).first()
        except Exception:
            pass
        
        # Last resort - return None and let the caller handle it
        logger.critical("Could not get any FieldType, even as fallback")
        return None
    except Exception as fallback_error:
        # Even fallback failed, try to get any existing string type
        logger.error(f"Fallback FieldType creation also failed: {str(fallback_error)}")
        try:
            return FieldType.objects.filter(project=project, name='string').first() or \
                   FieldType.objects.filter(project=project, type='string').first() or \
                   FieldType.objects.filter(project=project).first()
        except Exception:
            # Last resort - return None and let the caller handle it
            logger.critical("Could not get any FieldType, even as fallback")
            return None


def _safe_get_or_create(model_class, created_list=None, **kwargs):
    """Safely get or create a model instance with proper transaction and connection handling"""
    defaults = kwargs.pop('defaults', {})
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            # Build lookup kwargs (everything except defaults)
            lookup_kwargs = kwargs.copy()
            
            # Try to get existing first
            try:
                instance = model_class.objects.get(**lookup_kwargs)
                return instance, False
            except model_class.DoesNotExist:
                # Doesn't exist, try to create it
                # Use a savepoint to ensure IntegrityError doesn't abort the outer transaction
                try:
                    with transaction.atomic():
                        create_kwargs = lookup_kwargs.copy()
                        create_kwargs.update(defaults)
                        instance = model_class.objects.create(**create_kwargs)
                    if created_list is not None:
                        created_list.append(instance)
                    return instance, True
                except (IntegrityError, OperationalError) as db_error:
                    # If creation fails due to constraint, try to get it again
                    # (might have been created by another process or concurrent request)
                    if attempt < max_retries - 1:
                        # Small delay to allow concurrent transaction to complete
                        time.sleep(0.01 * (attempt + 1))  # Exponential backoff: 10ms, 20ms, 30ms
                        continue
                    else:
                        # Last attempt - try to get it one more time
                        try:
                            return model_class.objects.get(**lookup_kwargs), False
                        except model_class.DoesNotExist:
                            logger.error(f"{model_class.__name__} does not exist and cannot be created after {max_retries} attempts: {str(db_error)}")
                            raise
                except (DatabaseError, Exception) as db_error:
                    # For other database errors, retry
                    if attempt < max_retries - 1:
                        logger.warning(f"Database error creating {model_class.__name__} (attempt {attempt + 1}): {str(db_error)}")
                        time.sleep(0.01 * (attempt + 1))
                        continue
                    else:
                        logger.error(f"Failed to create {model_class.__name__} after {max_retries} attempts: {str(db_error)}")
                        raise
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Unexpected error creating {model_class.__name__} (attempt {attempt + 1}): {str(e)}")
                time.sleep(0.01 * (attempt + 1))
                continue
            else:
                logger.error(f"Failed to create {model_class.__name__} after {max_retries} attempts: {str(e)}")
                raise
    
    # Should never reach here, but just in case
    raise Exception(f"Failed to create {model_class.__name__} after {max_retries} attempts")


def find_matching_payload(project, payload_name, schema_data, schemas):
    """Find existing payload that matches the schema data exactly"""
    # Get the data schema for this payload
    data_schema_name = f"Data_{payload_name}"
    if data_schema_name not in schema_data:
        return None
    
    data_schema = schema_data[data_schema_name]
    properties = data_schema.get('properties', {})
    required_fields = data_schema.get('required', [])
    
    # Look for existing payloads with the same name
    existing_payloads = Payload.objects.filter(project=project, name=payload_name)
    
    for payload in existing_payloads:
        # Check if field count matches
        payload_fields = payload.field_set.all()
        if len(payload_fields) != len(properties):
            continue
        
        # Check each field
        field_matches = True
        for field in payload_fields:
            if field.name not in properties:
                field_matches = False
                break
            
            field_data = properties[field.name]
            
            # Check field properties
            if field.required != (field.name in required_fields):
                field_matches = False
                break
            
            if field.description != field_data.get('description', ''):
                field_matches = False
                break
            
            if field.minimum != field_data.get('minimum'):
                field_matches = False
                break
            
            if field.maximum != field_data.get('maximum'):
                field_matches = False
                break
            
            # Check field type
            if field.type.type != field_data.get('type', 'string'):
                field_matches = False
                break
            
            if field.type.format != field_data.get('format'):
                field_matches = False
                break
            
            if field.type.max_length != field_data.get('maxLength'):
                field_matches = False
                break
        
        if field_matches:
            return payload
    
    return None


@staff_member_required
def generate_full_yaml(request, service_id):
    service = Service.objects.get(id=service_id)

    # ── helpers ──────────────────────────────────────────────────────────────
    general_name = service.x_general_name if service.x_general_name else service.name.lower()
    # Channel key prefix: first letter uppercased, rest as-is (e.g. "station" → "Station")
    pascal_general = general_name[0].upper() + general_name[1:]

    def to_pascal(snake: str) -> str:
        """snake_case → PascalCase with special-word handling and ID→Id fix."""
        result = ''.join(
            w.upper() if w.upper() in ('OTP', 'API', 'URL') else w.capitalize()
            for w in snake.split('_')
        )
        return result.replace('ID', 'Id')

    def to_camel(snake: str) -> str:
        """snake_case → camelCase."""
        pascal = to_pascal(snake)
        return pascal[0].lower() + pascal[1:]

    def channel_key(event) -> str:
        """'{PascalGeneralName}.{camelEventName}' — e.g. 'Station.bootNotificationReceived'."""
        return f"{pascal_general}.{to_camel(event.name)}"

    def build_field_properties(field_set):
        """Build a (properties_dict, required_list) from a queryset of Field objects."""
        properties = {}
        required_fields = []
        for field in field_set:
            x_type_override = getattr(field.type, 'x_type', None)
            resolved_schema_ref = field.schema_ref
            if not resolved_schema_ref and field.type.type == 'object':
                schema_def = field.type.get_schema_definition()
                if schema_def:
                    resolved_schema_ref = "#/components/schemas/{0}".format(field.type.name)

            if field.type.custom_type:
                if field.type.enum_choices is not None:
                    enum_choices = field.type.enum_choices.replace(" ", "").split(",")
                    field_property = {'type': 'string', 'enum': enum_choices}
                    if x_type_override:
                        field_property['x-type'] = x_type_override
                else:
                    field_property = {'type': field.type.type}
                    if x_type_override:
                        field_property['x-type'] = x_type_override
                    if field.type.max_length and field.type.max_length > 0:
                        field_property['maxLength'] = field.type.max_length
            else:
                field_property = {'type': field.type.type}
                if field.type.format:
                    field_property['format'] = field.type.format
                if x_type_override:
                    field_property['x-type'] = x_type_override
                if field.type.max_length and field.type.max_length > 0:
                    field_property['maxLength'] = field.type.max_length
                if field.type.type == 'array':
                    if field.array_items_type:
                        field_property['items'] = {'type': field.array_items_type}
                    elif field.array_items_ref:
                        field_property['items'] = {'$ref': field.array_items_ref}

            if resolved_schema_ref:
                field_property = {'$ref': resolved_schema_ref}

            if field.description and '$ref' not in field_property:
                field_property['description'] = field.description

            properties[field.name] = field_property
            if field.required:
                required_fields.append(field.name)
        return properties, required_fields

    # ── base structure ────────────────────────────────────────────────────────
    info = {
        'title': service.original_title if service.original_title else "{0} V2 {1} Service".format(service.project.name, service.name),
        'version': service.version,
        'description': service.description if service.description is not None else "N/A",
        'x-general-name': general_name,
        'x-service-name': service.x_service_name if service.x_service_name else service.kebab_name(),
        'x-service-ip': service.x_service_ip if service.x_service_ip else "change-this",
    }
    if service.x_transport:
        info['x-transport'] = service.x_transport
    http_clients = list(service.http_clients.prefetch_related('fields').all())
    if http_clients:
        info['x-http-clients'] = [
            {'name': c.name, 'fields': [{'name': f.name} for f in c.fields.all()]}
            for c in http_clients
        ]
    grpc_clients = list(service.grpc_clients.prefetch_related('methods').all())
    if grpc_clients:
        gc_list = []
        for c in grpc_clients:
            gc_item = {'name': c.name}
            if c.module:
                gc_item['module'] = c.module
            if c.proto_service:
                gc_item['service'] = c.proto_service
            methods = list(c.methods.all())
            if methods:
                gc_item['methods'] = []
                for m in methods:
                    method_item = {'name': m.name, 'request': m.request, 'response': m.response}
                    if m.input_field:
                        method_item['input_field'] = m.input_field
                    if m.output_field:
                        method_item['output_field'] = m.output_field
                    gc_item['methods'].append(method_item)
            gc_list.append(gc_item)
        info['x-grpc-clients'] = gc_list

    configuration = {
        'asyncapi': service.asyncapi_version,
        'info': info,
        'servers': {
            'kafka': {
                'host': "127.0.0.1:9092",
                'protocol': "kafka-secure",
                'description': "Test broker",
            }
        },
        'channels': {},
        'operations': {},
        'components': {
            'messages': {},
            'schemas': {},
        },
    }

    # Pre-fetch consumes/publishes sets for efficient membership checks
    consumes_ids = set(service.consumes.values_list('id', flat=True))
    publishes_ids = set(service.publishes.values_list('id', flat=True))

    # ── channels + operations + messages ─────────────────────────────────────
    for event in service.consumes.all().union(service.publishes.all()):
        pascal_event = to_pascal(event.name)
        ch_key = channel_key(event)

        description = event.description if event.description else event.name.replace('_', ' ').title()
        summary = event.summary if event.summary else description

        # ── channel config ────────────────────────────────────────────────
        channel_config = {
            'description': description,
            'x-is-sync': event.is_sync,
        }

        if not event.is_sync:
            # x-db-operation if linked
            try:
                op = event.db_operation
                db_op = {
                    'type': op.type,
                    'schema': op.schema,
                    'lookup-field': op.lookup_field,
                }
                if op.lookup_field_2:
                    db_op['lookup-field-2'] = op.lookup_field_2
                if op.status:
                    db_op['status'] = op.status
                if op.function_name:
                    db_op['function-name'] = op.function_name
                channel_config['x-db-operation'] = db_op
            except DbOperation.DoesNotExist:
                pass

        channel_config['address'] = event.address if event.address else f"{general_name}.{to_camel(event.name)}"
        channel_config['summary'] = summary

        # ── messages in channel ───────────────────────────────────────────
        if event.is_sync:
            request_msg_name = f"{pascal_event}Request"
            response_msg_name = f"{pascal_event}Response"
            channel_config['messages'] = {
                request_msg_name: {'$ref': f"#/components/messages/{request_msg_name}"},
                response_msg_name: {'$ref': f"#/components/messages/{response_msg_name}"},
            }
            op_msg_ref = f"#/channels/{ch_key}/messages/{request_msg_name}"
        else:
            payload_msg_name = f"{event.payload.name}Payload" if event.payload else f"{pascal_event}Payload"
            channel_config['messages'] = {
                payload_msg_name: {'$ref': f"#/components/messages/{payload_msg_name}"},
            }
            op_msg_ref = f"#/channels/{ch_key}/messages/{payload_msg_name}"

        configuration['channels'][ch_key] = channel_config

        # ── operation ─────────────────────────────────────────────────────
        is_response_channel = event.name.endswith('_response')
        should_create_operation = True

        if is_response_channel:
            base_event_name = event.name[:-len('_response')]
            try:
                base_event = Event.objects.get(domain=event.domain, name=base_event_name)
                special_cases = ('certificate_signed', 'data_transfer')
                is_special = base_event_name in special_cases
                should_create_operation = (
                    (base_event.id in consumes_ids)
                    or (is_special and event.id in publishes_ids and base_event.id in publishes_ids)
                )
            except Event.DoesNotExist:
                should_create_operation = False

        if should_create_operation:
            action = 'receive' if event.id in consumes_ids else 'send'
            operation_key_str = f"{action}{ch_key}"

            operation_config = {
                'action': action,
                'channel': {'$ref': f"#/channels/{ch_key}"},
                'messages': [{'$ref': op_msg_ref}],
            }
            if event.is_sync:
                operation_config['x-operation-name'] = pascal_event
                endpoint = event.endpoint if event.endpoint else f"/{to_camel(event.name)}"
                operation_config['x-endpoint'] = endpoint
                operation_config['x-http-method'] = event.http_method if event.http_method else 'post'
                operation_config['x-jwt'] = event.is_jwt

            configuration['operations'][operation_key_str] = operation_config

        # ── message components ────────────────────────────────────────────
        if event.is_sync:
            # Request message
            request_schema_name = f"{pascal_event}RequestBody"
            configuration['components']['messages'][f"{pascal_event}Request"] = {
                'name': f"{pascal_event}Request",
                'contentType': 'application/json',
                'payload': {'$ref': f"#/components/schemas/{request_schema_name}"},
            }
            # Response message
            response_schema_name = f"{pascal_event}ResponseBody"
            configuration['components']['messages'][f"{pascal_event}Response"] = {
                'name': f"{pascal_event}Response",
                'contentType': 'application/json',
                'payload': {'$ref': f"#/components/schemas/{response_schema_name}"},
            }
        else:
            payload_name = event.payload.name if event.payload else pascal_event
            envelope_schema_name = f"{payload_name}Envelope"
            msg_name = f"{payload_name}Payload"
            if msg_name not in configuration['components']['messages']:
                configuration['components']['messages'][msg_name] = {
                    'name': msg_name,
                    'contentType': 'application/json',
                    'payload': {'$ref': f"#/components/schemas/{envelope_schema_name}"},
                }

    # ── collect all payloads used by this service's events ────────────────────
    service_payloads = set()
    sync_events_by_payload = {}   # payload → list of events (for request/response schema naming)
    for event in service.consumes.all().union(service.publishes.all()):
        if event.payload:
            service_payloads.add(event.payload)
            if event.is_sync:
                sync_events_by_payload.setdefault(event.payload.id, []).append(event)
        if event.response_payload:
            service_payloads.add(event.response_payload)
            if event.is_sync:
                sync_events_by_payload.setdefault(event.response_payload.id, []).append(event)

    # ── payload schemas ───────────────────────────────────────────────────────
    # Track which payload names have already been written as async envelopes
    written_envelope_names = set()

    for event in service.consumes.all().union(service.publishes.all()):
        pascal_event = to_pascal(event.name)

        if event.is_sync:
            # ── Sync: plain RequestBody + ResponseBody schemas ────────────
            request_schema_name = f"{pascal_event}RequestBody"
            response_schema_name = f"{pascal_event}ResponseBody"

            if event.payload:
                req_props, req_required = build_field_properties(event.payload.field_set.all())
            else:
                req_props, req_required = {}, []

            req_schema = {'type': 'object', 'properties': req_props}
            if req_required:
                req_schema['required'] = req_required
            if event.payload and event.payload.description:
                req_schema['description'] = event.payload.description
            # x-parser-schema-id: lowercase first char of schema name
            req_schema['x-parser-schema-id'] = request_schema_name[0].lower() + request_schema_name[1:]
            configuration['components']['schemas'][request_schema_name] = req_schema

            if event.response_payload:
                res_props, res_required = build_field_properties(event.response_payload.field_set.all())
            else:
                res_props, res_required = {}, []

            res_schema = {'type': 'object', 'properties': res_props}
            if res_required:
                res_schema['required'] = res_required
            if event.response_payload and event.response_payload.description:
                res_schema['description'] = event.response_payload.description
            res_schema['x-parser-schema-id'] = response_schema_name[0].lower() + response_schema_name[1:]
            configuration['components']['schemas'][response_schema_name] = res_schema

        else:
            # ── Async: Envelope + Data_* schemas ─────────────────────────
            if not event.payload:
                continue
            payload = event.payload
            payload_name = payload.name
            envelope_schema_name = f"{payload_name}Envelope"

            if envelope_schema_name in written_envelope_names:
                continue
            written_envelope_names.add(envelope_schema_name)

            camel_payload = payload_name[0].lower() + payload_name[1:]
            data_schema_name = f"Data_{camel_payload}"

            data_props, data_required = build_field_properties(payload.field_set.all())

            if data_props:
                data_property = {'$ref': f"#/components/schemas/{data_schema_name}"}
            else:
                data_property = {'type': 'object', 'properties': {}}

            configuration['components']['schemas'][envelope_schema_name] = {
                'type': 'object',
                'properties': {
                    'eventName': {
                        'type': 'string',
                        'default': payload_name,
                    },
                    'fromService': {
                        'type': 'string',
                        'default': '',
                    },
                    'sentAt': {
                        'type': 'string',
                        'format': 'date-time',
                        'default': '2026-01-01T00:00:00Z',
                    },
                    'timeToLive': {
                        'type': 'integer',
                        'default': 3600000,
                    },
                    'data': data_property,
                },
                'x-parser-schema-id': f"{camel_payload}Envelope",
            }

            if data_props:
                data_schema = {'type': 'object', 'properties': data_props}
                if payload.description:
                    data_schema['description'] = payload.description
                if data_required:
                    data_schema['required'] = data_required
                data_schema['x-parser-schema-id'] = f"{camel_payload}Data"
                configuration['components']['schemas'][data_schema_name] = data_schema

    # ── DB_* schemas ──────────────────────────────────────────────────────────
    DB_INT_PROPS = ['x_size', 'x_precision', 'x_scale']

    for dbpayload in DatabasePayload.objects.filter(service=service):
        properties = {}

        for field in dbpayload.databasefield_set.all():
            if field.type.custom_type:
                if field.type.enum_choices is not None:
                    enum_choices = field.type.enum_choices.replace(" ", "").split(",")
                    properties[field.name] = {
                        'type': 'string',
                        'enum': enum_choices,
                        'description': field.description or field.name,
                    }
                else:
                    if field.type.type == "string":
                        properties[field.name] = {
                            '$ref': "#/components/schemas/{0}".format(field.type.name)
                        }
                        configuration['components']['schemas'][field.type.name] = {
                            'type': field.type.type,
                            'x-parser-schema-id': field.type.name,
                        }
                        if field.type.max_length and field.type.max_length > 0:
                            configuration['components']['schemas'][field.type.name]['maxLength'] = field.type.max_length
            else:
                # Build prop in example-matching order:
                # type → format → x-type → x-type-override → x-relation-schema-id →
                # x-size/precision/scale → bool flags → x-default → x-check →
                # x-column/comment/serializer → description →
                # x-foreign-key/references/cascade → x-many-to-many/join props →
                # x-association bools → x-embedded → x-polymorphic/constraint → items
                prop = {'type': field.type.type}

                if field.type.format:
                    prop['format'] = field.type.format
                if field.x_type:
                    prop['x-type'] = field.x_type
                if field.x_type_override:
                    prop['x-type-override'] = field.x_type_override
                if field.x_primary_key:
                    prop['x-primary-key'] = True
                # x-relation-schema-id comes right after x-type (not after null/index props)
                if field.x_relation_schema_id:
                    prop['x-relation-schema-id'] = field.x_relation_schema_id

                # int props
                for attr in DB_INT_PROPS:
                    val = getattr(field, attr, None)
                    if val is not None:
                        prop[attr.replace('_', '-')] = val

                # index/null/ignore/auto booleans (excludes association/embedded — those come later)
                for attr in ['x_unique', 'x_unique_index', 'x_index', 'x_not_null', 'x_nullable',
                             'x_ignore', 'x_auto_create_time', 'x_auto_update_time', 'x_auto_increment']:
                    if getattr(field, attr, False):
                        prop[attr.replace('_', '-')] = True

                # x-default (before x-check)
                if field.default_value is not None:
                    dv = field.default_value
                    if isinstance(dv, str):
                        if dv.lower() == 'true':
                            dv = True
                        elif dv.lower() == 'false':
                            dv = False
                    prop['x-default'] = dv

                if field.x_check:
                    prop['x-check'] = field.x_check
                for attr in ['x_column', 'x_comment', 'x_serializer']:
                    val = getattr(field, attr, None)
                    if val:
                        prop[attr.replace('_', '-')] = val

                if field.description:
                    prop['description'] = field.description

                # relation props
                for attr in ['x_foreign_key', 'x_references', 'x_cascade_update', 'x_cascade_delete']:
                    val = getattr(field, attr, None)
                    if val:
                        prop[attr.replace('_', '-')] = val

                # preload (after cascade props, before M2M and items)
                if field.x_preload:
                    prop['x-preload'] = True

                # M2M props
                for attr in ['x_many_to_many', 'x_join_table', 'x_join_foreign_key', 'x_join_references']:
                    val = getattr(field, attr, None)
                    if val:
                        prop[attr.replace('_', '-')] = val

                # association booleans (after join props, like in example)
                for attr in ['x_association_autocreate', 'x_association_autoupdate', 'x_association_save_reference']:
                    if getattr(field, attr, False):
                        prop[attr.replace('_', '-')] = True

                # embedded
                if field.x_embedded:
                    prop['x-embedded'] = True
                if field.x_embedded_prefix:
                    prop['x-embedded-prefix'] = field.x_embedded_prefix

                # polymorphic / constraint
                for attr in ['x_polymorphic', 'x_polymorphic_value', 'x_association_foreign_key', 'x_constraint']:
                    val = getattr(field, attr, None)
                    if val:
                        prop[attr.replace('_', '-')] = val

                # items for arrays (keyed by x-relation-schema-id already set above)
                if field.type.type == 'array' and field.x_relation_schema_id:
                    prop['items'] = {'$ref': f"#/components/schemas/{field.x_relation_schema_id}"}

                properties[field.name] = prop

        schema_config = {'type': 'object'}
        if dbpayload.description:
            schema_config['description'] = dbpayload.description
        schema_config['x-parser-schema-id'] = dbpayload.x_parser_schema_id if dbpayload.x_parser_schema_id else dbpayload.name
        schema_config['x-create-rest'] = dbpayload.create_rest
        if dbpayload.x_derives_from:
            schema_config['x-derives-from'] = dbpayload.x_derives_from
        schema_config['properties'] = properties

        configuration['components']['schemas'][f"DB_{dbpayload.name}"] = schema_config

    # Aliases for DB payload parser schema IDs (e.g., User → DB_User)
    for dbpayload in DatabasePayload.objects.filter(service=service):
        if dbpayload.x_parser_schema_id:
            db_schema_key = f"DB_{dbpayload.name}"
            if (db_schema_key in configuration['components']['schemas']
                    and dbpayload.x_parser_schema_id not in configuration['components']['schemas']):
                configuration['components']['schemas'][dbpayload.x_parser_schema_id] = {
                    '$ref': f"#/components/schemas/{db_schema_key}"
                }

    # ── export complex FieldType object schemas ───────────────────────────────
    service_field_types = set()
    for payload in service_payloads:
        for field in payload.field_set.all():
            service_field_types.add(field.type)
    for dbpayload in DatabasePayload.objects.filter(service=service):
        for field in dbpayload.databasefield_set.all():
            service_field_types.add(field.type)

    for field_type in service_field_types:
        if field_type.type == 'object' and field_type.custom_type:
            schema_def = field_type.get_schema_definition()
            if schema_def:
                configuration['components']['schemas'][field_type.name] = schema_def.copy()

    # ── resolve $ref schemas ──────────────────────────────────────────────────
    referenced_schema_names = set()

    def add_ref_schema_name(ref_value):
        if isinstance(ref_value, str) and '/schemas/' in ref_value:
            schema_name = ref_value.split('/')[-1]
            if schema_name:
                referenced_schema_names.add(schema_name)

    def collect_ref_schema_names(obj):
        if isinstance(obj, dict):
            ref_value = obj.get('$ref')
            if ref_value:
                add_ref_schema_name(ref_value)
            for value in obj.values():
                collect_ref_schema_names(value)
        elif isinstance(obj, list):
            for item in obj:
                collect_ref_schema_names(item)

    for payload in service_payloads:
        for field in payload.field_set.all():
            add_ref_schema_name(field.schema_ref)
            add_ref_schema_name(field.array_items_ref)

    for schema_data in configuration['components']['schemas'].values():
        collect_ref_schema_names(schema_data)

    field_type_cache = {}

    def get_field_type_by_name(schema_name):
        if schema_name not in field_type_cache:
            field_type_cache[schema_name] = FieldType.objects.filter(
                project=service.project, name=schema_name
            ).first()
        return field_type_cache[schema_name]

    def build_schema_from_field_type(field_type):
        if field_type.type == 'object':
            schema_def = field_type.get_schema_definition()
            if schema_def:
                return schema_def.copy()
            schema_def = {'type': 'object'}
        else:
            schema_def = {'type': field_type.type}
        if field_type.format:
            schema_def['format'] = field_type.format
        if field_type.max_length and field_type.max_length > 0:
            schema_def['maxLength'] = field_type.max_length
        if field_type.enum_choices:
            schema_def['enum'] = field_type.enum_choices.replace(" ", "").split(",")
        return schema_def

    resolved_schema_names = set()
    while referenced_schema_names:
        schema_name = referenced_schema_names.pop()
        if schema_name in configuration['components']['schemas'] or schema_name in resolved_schema_names:
            continue
        field_type = get_field_type_by_name(schema_name)
        if not field_type:
            resolved_schema_names.add(schema_name)
            continue
        schema_def = build_schema_from_field_type(field_type)
        if not schema_def:
            resolved_schema_names.add(schema_name)
            continue
        configuration['components']['schemas'][schema_name] = schema_def
        collect_ref_schema_names(schema_def)
        resolved_schema_names.add(schema_name)

    # ── YAML serialisation ────────────────────────────────────────────────────
    class DoubleQuotedString(str):
        pass

    def represent_double_quoted_string(dumper, data):
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')

    yaml.add_representer(DoubleQuotedString, represent_double_quoted_string)

    # Use literal block scalar (|) for multi-line strings
    def represent_str_literal(dumper, data):
        if '\n' in data:
            return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
        return dumper.represent_scalar('tag:yaml.org,2002:str', data)

    yaml.add_representer(str, represent_str_literal)

    def convert_to_double_quoted(obj, current_path=""):
        if isinstance(obj, dict):
            new_obj = {}
            for key, value in obj.items():
                new_path = f"{current_path}.{key}" if current_path else key
                if key == 'x-endpoint':
                    new_obj[key] = DoubleQuotedString(value)
                elif key == '$ref' and isinstance(value, str):
                    new_obj[key] = DoubleQuotedString(value)
                else:
                    new_obj[key] = convert_to_double_quoted(value, new_path)
            return new_obj
        elif isinstance(obj, list):
            return [convert_to_double_quoted(item, current_path) for item in obj]
        return obj

    configuration_with_quotes = convert_to_double_quoted(configuration)
    yaml_out = yaml.dump(configuration_with_quotes, sort_keys=False, default_flow_style=False, allow_unicode=True)

    response = HttpResponse(yaml_out, content_type='application/x-yaml')
    response['Content-Disposition'] = 'attachment; filename={0}.yaml'.format(service.slug_name)

    return response


def import_yaml_page(request):
    """Display the import YAML page"""
    return render(request, 'events/import_yaml.html')


def debug_import(request):
    """Debug view to see what's happening"""
    if request.method == 'POST':
        print("=== DEBUG POST REQUEST ===")
        print(f"POST data: {request.POST}")
        print(f"yaml_content length: {len(request.POST.get('yaml_content', ''))}")
        print(f"confirm parameter: {request.POST.get('confirm', 'NOT_FOUND')}")
        print("=========================")
        
        return HttpResponse(f"""
        <h1>Debug Info</h1>
        <p>yaml_content length: {len(request.POST.get('yaml_content', ''))}</p>
        <p>confirm parameter: {request.POST.get('confirm', 'NOT_FOUND')}</p>
        <p>All POST data: {dict(request.POST)}</p>
        <a href="/events/import-yaml-page/">Back to Import</a>
        """)
    
    from django.template import Template, Context
    template = Template("""
    <h1>Debug Import</h1>
    <form method="post">
        {% csrf_token %}
        <textarea name="yaml_content" rows="10" cols="50">asyncapi: 3.0.0
info:
  title: Test Project Test Service
  version: 1.0.0</textarea><br><br>
        <input type="hidden" name="confirm" value="false">
        <button type="submit">Test Submit</button>
    </form>
    """)
    return HttpResponse(template.render(Context({'request': request})))


def parse_yaml_for_preview(yaml_content):
    """Parse YAML and return preview data without creating objects"""
    try:
        yaml_data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        raise Exception(f'Invalid YAML: {str(e)}')
    
    # Extract basic info
    info = yaml_data.get('info', {})
    project_name = info.get('title', 'Imported Project').split(' ')[0]  # Extract project name
    
    # Extract service name: prefer x-service-name, otherwise try better parsing from title
    # x-service-name is in kebab-case, convert to proper name
    x_service_name = info.get('x-service-name', '')
    if x_service_name:
        # Convert kebab-case to title case (e.g., "ocpp-gateway-service" -> "Ocpp Gateway Service")
        service_name = ' '.join(word.capitalize() for word in x_service_name.split('-'))
    else:
        # Fallback: extract from title - take last 2-3 words as service name
        title_words = info.get('title', 'Imported Service').split(' ')
        if len(title_words) >= 3:
            # Take last 2 words (e.g., "OCPP Gateway Service OCPP Bridge" -> "OCPP Bridge")
            service_name = ' '.join(title_words[-2:])
        elif len(title_words) == 2:
            service_name = title_words[1]
        else:
            service_name = 'Imported Service'
    
    # Create preview data structures
    preview_data = {
        'projects': [],
        'services': [],
        'events': [],
        'payloads': [],
        'field_types': [],
        'fields': [],
        'db_payloads': [],
        'db_fields': []
    }
    
    # Get existing data for comparison
    existing_data = {
        'projects': [],
        'services': [],
        'events': [],
        'payloads': [],
        'field_types': [],
        'fields': [],
        'db_payloads': [],
        'db_fields': []
    }
    
    # Get existing projects
    for project in Project.objects.all():
        existing_data['projects'].append({
            'name': project.name,
            'slug_name': project.slug_name
        })
    
    # Get existing services
    for service in Service.objects.all():
        existing_data['services'].append({
            'name': service.name,
            'project_name': service.project.name,
            'slug_name': service.slug_name,
            'asyncapi_version': service.asyncapi_version,
            'version': service.version,
            'description': service.description,
            'x_general_name': service.x_general_name,
            'x_service_name': service.x_service_name
        })
    
    # Get existing events
    for event in Event.objects.all():
        existing_data['events'].append({
            'name': event.name,
            'domain_name': event.domain.name,
            'type_name': event.type.name,
            'is_sync': event.is_sync,
            'is_post': event.is_post,
            'is_jwt': event.is_jwt,
            'address': event.address,
            'endpoint': event.endpoint,
            'description': event.description,
            'summary': event.summary,
            'payload_name': event.payload.name if event.payload else None,
            'response_payload_name': event.response_payload.name if event.response_payload else None
        })
    
    # Get existing payloads
    for payload in Payload.objects.all():
        fields = []
        for field in payload.field_set.all():
            fields.append({
                'name': field.name,
                'type_name': field.type.name,
                'required': field.required,
                'description': field.description,
                'minimum': field.minimum,
                'maximum': field.maximum
            })
        
        existing_data['payloads'].append({
            'name': payload.name,
            'project_name': payload.project.name,
            'description': payload.description,
            'fields': fields
        })
    
    # Get existing field types
    for field_type in FieldType.objects.all():
        existing_data['field_types'].append({
            'name': field_type.name,
            'type': field_type.type,
            'custom_type': field_type.custom_type,
            'format': field_type.format,
            'max_length': field_type.max_length,
            'enum_choices': field_type.enum_choices
        })
    
    # Get existing database payloads
    for db_payload in DatabasePayload.objects.all():
        fields = []
        for field in db_payload.databasefield_set.all():
            fields.append({
                'name': field.name,
                'type_name': field.type.name,
                'required': field.required,
                'description': field.description,
                'minimum': field.minimum,
                'maximum': field.maximum,
                'x_type': field.x_type,
                'x_unique': field.x_unique,
                'x_index': field.x_index,
                'default_value': field.default_value,
                'x_relation_schema_id': field.x_relation_schema_id
            })
        
        existing_data['db_payloads'].append({
            'name': db_payload.name,
            'project_name': db_payload.project.name,
            'service_name': db_payload.service.name if hasattr(db_payload, 'service') else None,
            'create_rest': db_payload.create_rest,
            'x_parser_schema_id': db_payload.x_parser_schema_id,
            'x_derives_from': db_payload.x_derives_from,
            'fields': fields
        })
    
    # Get existing database fields
    for db_field in DatabaseField.objects.all():
        existing_data['db_fields'].append({
            'name': db_field.name,
            'type_name': db_field.type.name,
            'required': db_field.required,
            'description': db_field.description,
            'minimum': db_field.minimum,
            'maximum': db_field.maximum,
            'x_type': db_field.x_type,
            'x_unique': db_field.x_unique,
            'x_index': db_field.x_index,
            'default_value': db_field.default_value,
            'x_relation_schema_id': db_field.x_relation_schema_id
        })
    
    # Get existing standalone fields (not part of payloads)
    for field in Field.objects.all():
        existing_data['fields'].append({
            'name': field.name,
            'type_name': field.type.name,
            'required': field.required,
            'description': field.description,
            'minimum': field.minimum,
            'maximum': field.maximum,
            'payload_name': field.payload.name if field.payload else None,
            'project_name': field.payload.project.name if field.payload else None
        })
    
    # Project preview - check for exact match
    project_slug = re.sub(r'[^a-zA-Z0-9]', '', project_name.lower())[:20]
    project_preview = {
        'name': project_name,
        'slug_name': project_slug,
        'is_existing': False,
        'existing_match': None,
        'is_conflict': False,
        'conflict_match': None,
        'conflict_reason': None
    }
    
    # Check for exact match or conflict in existing projects
    for existing_project in existing_data['projects']:
        if existing_project['name'] == project_name:
            if existing_project['slug_name'] == project_slug:
                project_preview['is_existing'] = True
                project_preview['existing_match'] = existing_project
            else:
                project_preview['is_conflict'] = True
                project_preview['conflict_match'] = existing_project
                project_preview['conflict_reason'] = f"Slug mismatch: existing='{existing_project['slug_name']}', new='{project_slug}'"
            break
    
    preview_data['projects'].append(project_preview)
    
    # Service preview - check for exact match
    service_slug = re.sub(r'[^a-zA-Z0-9]', '', service_name.lower())[:200]
    x_general_name = info.get('x-general-name', '')
    x_service_name = info.get('x-service-name', '')
    service_preview = {
        'name': service_name,
        'project_name': project_name,
        'slug_name': service_slug,
        'asyncapi_version': yaml_data.get('asyncapi', '3.0.0'),
        'version': info.get('version', '1.0.0'),
        'description': info.get('description', 'Imported service'),
        'x_general_name': x_general_name,
        'x_service_name': x_service_name,
        'is_existing': False,
        'existing_match': None,
        'is_conflict': False,
        'conflict_match': None,
        'conflict_reason': None
    }
    
    # Check for exact match or conflict in existing services
    for existing_service in existing_data['services']:
        if (existing_service['name'] == service_name and 
            existing_service['project_name'] == project_name):
            
            conflicts = []
            if existing_service['slug_name'] != service_slug:
                conflicts.append(f"slug: existing='{existing_service['slug_name']}', new='{service_slug}'")
            if existing_service['asyncapi_version'] != service_preview['asyncapi_version']:
                conflicts.append(f"asyncapi_version: existing='{existing_service['asyncapi_version']}', new='{service_preview['asyncapi_version']}'")
            if existing_service['version'] != service_preview['version']:
                conflicts.append(f"version: existing='{existing_service['version']}', new='{service_preview['version']}'")
            if existing_service['description'] != service_preview['description']:
                conflicts.append(f"description: existing='{existing_service['description']}', new='{service_preview['description']}'")
            if existing_service.get('x_general_name') != x_general_name:
                conflicts.append(f"x_general_name: existing='{existing_service.get('x_general_name')}', new='{x_general_name}'")
            if existing_service.get('x_service_name') != x_service_name:
                conflicts.append(f"x_service_name: existing='{existing_service.get('x_service_name')}', new='{x_service_name}'")
            
            if not conflicts:
                service_preview['is_existing'] = True
                service_preview['existing_match'] = existing_service
            else:
                service_preview['is_conflict'] = True
                service_preview['conflict_match'] = existing_service
                service_preview['conflict_reason'] = "; ".join(conflicts)
            break
    
    preview_data['services'].append(service_preview)
    
    # Process channels and create event previews
    channels = yaml_data.get('channels', {})
    operations = yaml_data.get('operations', {})
    yaml_messages = yaml_data.get('components', {}).get('messages', {})
    schemas = yaml_data.get('components', {}).get('schemas', {})
    operation_jwt_by_channel = {}
    
    for op_data in operations.values():
        channel_ref = op_data.get('channel', {}).get('$ref', '')
        if not channel_ref or 'x-jwt' not in op_data:
            continue
        channel_name = channel_ref.split('/')[-1]
        if channel_name:
            operation_jwt_by_channel[channel_name] = op_data.get('x-jwt')
    
    for channel_name, channel_data in channels.items():
        # Convert PascalCase to snake_case for event name
        event_snake_name = re.sub('([A-Z]+)', r'_\1', channel_name).lower().strip('_')
        
        # Parse messages from channel
        channel_messages = channel_data.get('messages', {})
        
        # Extract payloads from messages
        request_payload_name = None
        response_payload_name = None
        
        # Handle both dictionary format (new) and array format (old)
        if isinstance(channel_messages, dict):
            # New dictionary format
            for message_name, message_data in channel_messages.items():
                if message_name in yaml_messages:
                    message_ref = yaml_messages[message_name]
                    payload_ref = message_ref.get('payload', {}).get('$ref', '')
                    if payload_ref:
                        # Extract payload name from reference
                        payload_name = payload_ref.split('/')[-1].replace('Payload', '')
                        
                        # Assign to request or response based on message name
                        if message_name.endswith('Response'):
                            response_payload_name = payload_name
                        else:
                            request_payload_name = payload_name
        
        # Create event preview - check for exact match
        jwt_value = channel_data.get('x-jwt')
        if jwt_value is None:
            jwt_value = operation_jwt_by_channel.get(channel_name)
        if jwt_value is None:
            jwt_value = False
        event_preview = {
            'name': event_snake_name,
            'domain_name': 'default',
            'type_name': 'event',
            'is_sync': channel_data.get('x-is-sync', True),
            'is_post': channel_data.get('x-is-post', False),
            'is_jwt': jwt_value,
            'address': channel_data.get('address'),
            'endpoint': f"/{channel_name}",
            'description': channel_data.get('description', ''),
            'summary': channel_data.get('summary', ''),
            'payload_name': request_payload_name,
            'response_payload_name': response_payload_name,
            'is_existing': False,
            'existing_match': None,
            'is_conflict': False,
            'conflict_match': None,
            'conflict_reason': None
        }
        
        # Check for exact match or conflict in existing events
        for existing_event in existing_data['events']:
            if (existing_event['name'] == event_snake_name and
                existing_event['domain_name'] == 'default' and
                existing_event['type_name'] == 'event'):
                
                conflicts = []
                if existing_event['is_sync'] != event_preview['is_sync']:
                    conflicts.append(f"is_sync: existing={existing_event['is_sync']}, new={event_preview['is_sync']}")
                if existing_event['is_post'] != event_preview['is_post']:
                    conflicts.append(f"is_post: existing={existing_event['is_post']}, new={event_preview['is_post']}")
                if existing_event.get('is_jwt') != event_preview['is_jwt']:
                    conflicts.append(f"is_jwt: existing={existing_event.get('is_jwt')}, new={event_preview['is_jwt']}")
                if existing_event['address'] != event_preview['address']:
                    conflicts.append(f"address: existing='{existing_event['address']}', new='{event_preview['address']}'")
                if existing_event['description'] != event_preview['description']:
                    conflicts.append(f"description: existing='{existing_event['description']}', new='{event_preview['description']}'")
                if existing_event['summary'] != event_preview['summary']:
                    conflicts.append(f"summary: existing='{existing_event['summary']}', new='{event_preview['summary']}'")
                if existing_event['payload_name'] != request_payload_name:
                    conflicts.append(f"payload_name: existing='{existing_event['payload_name']}', new='{request_payload_name}'")
                if existing_event['response_payload_name'] != response_payload_name:
                    conflicts.append(f"response_payload_name: existing='{existing_event['response_payload_name']}', new='{response_payload_name}'")
                
                if not conflicts:
                    event_preview['is_existing'] = True
                    event_preview['existing_match'] = existing_event
                else:
                    event_preview['is_conflict'] = True
                    event_preview['conflict_match'] = existing_event
                    event_preview['conflict_reason'] = "; ".join(conflicts)
                break
        
        preview_data['events'].append(event_preview)
    
    # Process schemas to create payload and field type previews
    for schema_name, schema_data in schemas.items():
        if schema_name.startswith('Data_'):
            # This is a data schema, find corresponding payload
            payload_name = schema_name.replace('Data_', '').replace('Payload', '')
            
            # Create payload preview - check for exact match
            payload_preview = {
                'name': payload_name,
                'project_name': project_name,
                'description': schema_data.get('description', ''),
                'fields': [],
                'is_existing': False,
                'existing_match': None,
                'is_conflict': False,
                'conflict_match': None,
                'conflict_reason': None
            }
            
            # Process properties
            properties = schema_data.get('properties', {})
            required_fields = schema_data.get('required', [])
            
            for field_name, field_data in properties.items():
                # Create field preview with proper field type name
                field_type_name = field_data.get('type', 'string')
                format_value = field_data.get('format')
                if format_value:
                    field_type_name_with_format = f"{field_type_name}_{format_value}"
                else:
                    field_type_name_with_format = field_type_name
                
                field_preview = {
                    'name': field_name,
                    'type_name': field_type_name_with_format,
                    'required': field_name in required_fields,
                    'description': field_data.get('description', ''),
                    'minimum': field_data.get('minimum'),
                    'maximum': field_data.get('maximum'),
                    'is_existing': False,
                    'existing_match': None,
                    'is_conflict': False,
                    'conflict_match': None,
                    'conflict_reason': None
                }
                
                # Check for exact match or conflict in existing fields
                for existing_field in existing_data['fields']:
                    if (existing_field['name'] == field_name and
                        existing_field.get('payload_name') == payload_name and
                        existing_field.get('project_name') == project_name):
                        conflicts = []
                        if existing_field['type_name'] != field_preview['type_name']:
                            conflicts.append(f"type: existing='{existing_field['type_name']}', new='{field_preview['type_name']}'")
                        if existing_field['required'] != field_preview['required']:
                            conflicts.append(f"required: existing={existing_field['required']}, new={field_preview['required']}")
                        if existing_field['description'] != field_preview['description']:
                            conflicts.append(f"description: existing='{existing_field['description']}', new='{field_preview['description']}'")
                        if existing_field['minimum'] != field_preview['minimum']:
                            conflicts.append(f"minimum: existing={existing_field['minimum']}, new={field_preview['minimum']}")
                        if existing_field['maximum'] != field_preview['maximum']:
                            conflicts.append(f"maximum: existing={existing_field['maximum']}, new={field_preview['maximum']}")
                        
                        if not conflicts:
                            field_preview['is_existing'] = True
                            field_preview['existing_match'] = existing_field
                        else:
                            field_preview['is_conflict'] = True
                            field_preview['conflict_match'] = existing_field
                            field_preview['conflict_reason'] = "; ".join(conflicts)
                        break
                
                payload_preview['fields'].append(field_preview)
                preview_data['fields'].append(field_preview)
            
            # Check for exact match or conflict in existing payloads
            for existing_payload in existing_data['payloads']:
                if (existing_payload['name'] == payload_name and
                    existing_payload['project_name'] == project_name):
                    
                    conflicts = []
                    if existing_payload['description'] != payload_preview['description']:
                        conflicts.append(f"description: existing='{existing_payload['description']}', new='{payload_preview['description']}'")
                    
                    # Check field differences
                    if len(existing_payload['fields']) != len(payload_preview['fields']):
                        conflicts.append(f"field_count: existing={len(existing_payload['fields'])}, new={len(payload_preview['fields'])}")
                    else:
                        # Check individual field differences by name
                        existing_fields_by_name = {field['name']: field for field in existing_payload['fields']}
                        for field in payload_preview['fields']:
                            if field['name'] in existing_fields_by_name:
                                existing_field = existing_fields_by_name[field['name']]
                                field_conflicts = []
                                if existing_field['type_name'] != field['type_name']:
                                    field_conflicts.append(f"type: existing='{existing_field['type_name']}', new='{field['type_name']}'")
                                if existing_field['required'] != field['required']:
                                    field_conflicts.append(f"required: existing={existing_field['required']}, new={field['required']}")
                                if existing_field['description'] != field['description']:
                                    field_conflicts.append(f"description: existing='{existing_field['description']}', new='{field['description']}'")
                                if existing_field['minimum'] != field['minimum']:
                                    field_conflicts.append(f"minimum: existing={existing_field['minimum']}, new={field['minimum']}")
                                if existing_field['maximum'] != field['maximum']:
                                    field_conflicts.append(f"maximum: existing={existing_field['maximum']}, new={field['maximum']}")
                                
                                if field_conflicts:
                                    conflicts.append(f"field '{field['name']}': {'; '.join(field_conflicts)}")
                            else:
                                conflicts.append(f"field '{field['name']}': new field not in existing payload")
                        
                        # Check for fields that exist in database but not in YAML
                        preview_field_names = {field['name'] for field in payload_preview['fields']}
                        for existing_field in existing_payload['fields']:
                            if existing_field['name'] not in preview_field_names:
                                conflicts.append(f"field '{existing_field['name']}': exists in database but not in YAML")
                    
                    if not conflicts:
                        payload_preview['is_existing'] = True
                        payload_preview['existing_match'] = existing_payload
                    else:
                        payload_preview['is_conflict'] = True
                        payload_preview['conflict_match'] = existing_payload
                        payload_preview['conflict_reason'] = "; ".join(conflicts)
                    break
            
            preview_data['payloads'].append(payload_preview)
            
        elif schema_name.startswith('DB_'):
            # This is a database schema
            db_payload_name = schema_name.replace('DB_', '')
            
            # Create database payload preview
            db_payload_preview = {
                'name': db_payload_name,
                'project_name': project_name,
                'service_name': service_name,
                'create_rest': schema_data.get('x-create-rest', False),
                'x_parser_schema_id': schema_data.get('x-parser-schema-id'),
                'x_derives_from': schema_data.get('x-derives-from'),
                'fields': [],
                'is_existing': False,
                'existing_match': None,
                'is_conflict': False,
                'conflict_match': None,
                'conflict_reason': None
            }
            
            # Check for exact match or conflict in existing database payloads
            # Match by service name and payload name
            for existing_db_payload in existing_data['db_payloads']:
                if existing_db_payload['name'] == db_payload_name and existing_db_payload.get('service_name') == service_name:
                    conflicts = []
                    if existing_db_payload.get('create_rest') != db_payload_preview['create_rest']:
                        conflicts.append(f"create_rest: existing={existing_db_payload.get('create_rest')}, new={db_payload_preview['create_rest']}")
                    if existing_db_payload.get('x_parser_schema_id') != db_payload_preview['x_parser_schema_id']:
                        conflicts.append(f"x_parser_schema_id: existing='{existing_db_payload.get('x_parser_schema_id')}', new='{db_payload_preview['x_parser_schema_id']}'")
                    if existing_db_payload.get('x_derives_from') != db_payload_preview['x_derives_from']:
                        conflicts.append(f"x_derives_from: existing='{existing_db_payload.get('x_derives_from')}', new='{db_payload_preview['x_derives_from']}'")
                    
                    if not conflicts:
                        db_payload_preview['is_existing'] = True
                        db_payload_preview['existing_match'] = existing_db_payload
                    else:
                        db_payload_preview['is_conflict'] = True
                        db_payload_preview['conflict_match'] = existing_db_payload
                        db_payload_preview['conflict_reason'] = "; ".join(conflicts)
                    break
            
            # Process properties
            properties = schema_data.get('properties', {})
            required_fields = schema_data.get('required', [])
            
            for field_name, field_data in properties.items():
                # Create database field preview
                db_field_preview = {
                    'name': field_name,
                    'type_name': field_data.get('type', 'string'),
                    'required': field_name in required_fields,
                    'description': field_data.get('description', ''),
                    'minimum': field_data.get('minimum'),
                    'maximum': field_data.get('maximum'),
                    'x_type': field_data.get('x-type'),
                    'x_unique': field_data.get('x-unique', False),
                    'x_index': field_data.get('x-index', False),
                    'default_value': field_data.get('default'),
                    'x_relation_schema_id': field_data.get('x-relation-schema-id')
                }
                db_payload_preview['fields'].append(db_field_preview)
                preview_data['db_fields'].append(db_field_preview)
            
            preview_data['db_payloads'].append(db_payload_preview)
            
        elif schema_name.endswith('Payload'):
            # This is a main payload schema - we already handled these above
            continue
            
        else:
            # This might be a standalone field type (enum, custom type, etc.)
            if 'enum' in schema_data:
                # This is an enum type
                field_type_preview = {
                    'name': schema_name,
                    'type': 'string',
                    'custom_type': True,
                    'format': schema_data.get('format'),
                    'max_length': schema_data.get('maxLength'),
                    'enum_choices': ','.join(schema_data.get('enum', [])),
                    'is_existing': False,
                    'existing_match': None,
                    'is_conflict': False,
                    'conflict_match': None,
                    'conflict_reason': None
                }
                
                # Check for exact match or conflict in existing field types
                for existing_field_type in existing_data['field_types']:
                    if existing_field_type['name'] == schema_name:
                        conflicts = []
                        if existing_field_type['type'] != field_type_preview['type']:
                            conflicts.append(f"type: existing='{existing_field_type['type']}', new='{field_type_preview['type']}'")
                        if existing_field_type['custom_type'] != field_type_preview['custom_type']:
                            conflicts.append(f"custom_type: existing={existing_field_type['custom_type']}, new={field_type_preview['custom_type']}")
                        if existing_field_type['format'] != field_type_preview['format']:
                            conflicts.append(f"format: existing='{existing_field_type['format']}', new='{field_type_preview['format']}'")
                        if existing_field_type['max_length'] != field_type_preview['max_length']:
                            conflicts.append(f"max_length: existing={existing_field_type['max_length']}, new={field_type_preview['max_length']}")
                        if existing_field_type['enum_choices'] != field_type_preview['enum_choices']:
                            conflicts.append(f"enum_choices: existing='{existing_field_type['enum_choices']}', new='{field_type_preview['enum_choices']}'")
                        
                        if not conflicts:
                            field_type_preview['is_existing'] = True
                            field_type_preview['existing_match'] = existing_field_type
                        else:
                            field_type_preview['is_conflict'] = True
                            field_type_preview['conflict_match'] = existing_field_type
                            field_type_preview['conflict_reason'] = "; ".join(conflicts)
                        break
                
                preview_data['field_types'].append(field_type_preview)
                
            elif schema_data.get('type') in ['string', 'number', 'integer', 'boolean', 'array', 'object']:
                # This is a basic field type
                field_type_preview = {
                    'name': schema_name,
                    'type': schema_data.get('type', 'string'),
                    'custom_type': True,
                    'format': schema_data.get('format'),
                    'max_length': schema_data.get('maxLength'),
                    'enum_choices': None,
                    'is_existing': False,
                    'existing_match': None,
                    'is_conflict': False,
                    'conflict_match': None,
                    'conflict_reason': None
                }
                
                # Check for exact match or conflict in existing field types
                for existing_field_type in existing_data['field_types']:
                    if existing_field_type['name'] == schema_name:
                        conflicts = []
                        if existing_field_type['type'] != field_type_preview['type']:
                            conflicts.append(f"type: existing='{existing_field_type['type']}', new='{field_type_preview['type']}'")
                        if existing_field_type['custom_type'] != field_type_preview['custom_type']:
                            conflicts.append(f"custom_type: existing={existing_field_type['custom_type']}, new={field_type_preview['custom_type']}")
                        if existing_field_type['format'] != field_type_preview['format']:
                            conflicts.append(f"format: existing='{existing_field_type['format']}', new='{field_type_preview['format']}'")
                        if existing_field_type['max_length'] != field_type_preview['max_length']:
                            conflicts.append(f"max_length: existing={existing_field_type['max_length']}, new={field_type_preview['max_length']}")
                        if existing_field_type['enum_choices'] != field_type_preview['enum_choices']:
                            conflicts.append(f"enum_choices: existing='{existing_field_type['enum_choices']}', new='{field_type_preview['enum_choices']}'")
                        
                        if not conflicts:
                            field_type_preview['is_existing'] = True
                            field_type_preview['existing_match'] = existing_field_type
                        else:
                            field_type_preview['is_conflict'] = True
                            field_type_preview['conflict_match'] = existing_field_type
                            field_type_preview['conflict_reason'] = "; ".join(conflicts)
                        break
                
                preview_data['field_types'].append(field_type_preview)
    
    return preview_data, existing_data


@csrf_exempt
@require_http_methods(["POST"])
def import_yaml(request):
    """Import AsyncAPI YAML and create all corresponding objects"""
    try:
        yaml_content = request.POST.get('yaml_content', '')
        confirm = request.POST.get('confirm', 'false').lower() == 'true'
        
        
        if not yaml_content:
            messages.error(request, 'No YAML content provided')
            return redirect('admin:index')
        
        # If not confirmed, show preview
        if not confirm:
            try:
                preview_data, existing_data = parse_yaml_for_preview(yaml_content)
                
                # Calculate summary (only count NEW objects, not existing or conflicts)
                summary = {
                    'projects': len([p for p in preview_data['projects'] if not p.get('is_existing', False) and not p.get('is_conflict', False)]),
                    'services': len([s for s in preview_data['services'] if not s.get('is_existing', False) and not s.get('is_conflict', False)]),
                    'events': len([e for e in preview_data['events'] if not e.get('is_existing', False) and not e.get('is_conflict', False)]),
                    'payloads': len([p for p in preview_data['payloads'] if not p.get('is_existing', False) and not p.get('is_conflict', False)]),
                    'field_types': len([ft for ft in preview_data['field_types'] if not ft.get('is_existing', False) and not ft.get('is_conflict', False)]),
                    'fields': len([f for f in preview_data['fields'] if not f.get('is_existing', False) and not f.get('is_conflict', False)]),
                    'db_payloads': len([dp for dp in preview_data['db_payloads'] if not dp.get('is_existing', False) and not dp.get('is_conflict', False)]),
                    'db_fields': len([df for df in preview_data['db_fields'] if not df.get('is_existing', False) and not df.get('is_conflict', False)])
                }
                
                # Calculate conflict counts
                conflict_summary = {
                    'projects': len([p for p in preview_data['projects'] if p.get('is_conflict', False)]),
                    'services': len([s for s in preview_data['services'] if s.get('is_conflict', False)]),
                    'events': len([e for e in preview_data['events'] if e.get('is_conflict', False)]),
                    'payloads': len([p for p in preview_data['payloads'] if p.get('is_conflict', False)]),
                    'field_types': len([ft for ft in preview_data['field_types'] if ft.get('is_conflict', False)]),
                    'fields': len([f for f in preview_data['fields'] if f.get('is_conflict', False)]),
                    'db_payloads': len([dp for dp in preview_data['db_payloads'] if dp.get('is_conflict', False)]),
                    'db_fields': len([df for df in preview_data['db_fields'] if df.get('is_conflict', False)])
                }
                
                # Calculate existing counts
                existing_summary = {
                    'projects': len(existing_data['projects']),
                    'services': len(existing_data['services']),
                    'events': len(existing_data['events']),
                    'payloads': len(existing_data['payloads']),
                    'field_types': len(existing_data['field_types']),
                    'fields': len(existing_data['fields']),
                    'db_payloads': len(existing_data['db_payloads']),
                    'db_fields': len(existing_data['db_fields'])
                }
                
                return render(request, 'events/import_yaml_confirmation.html', {
                    'preview_data': preview_data,
                    'existing_data': existing_data,
                    'summary': summary,
                    'existing_summary': existing_summary,
                    'conflict_summary': conflict_summary,
                    'yaml_content': yaml_content
                })
                
            except Exception as e:
                # Show the error message to the user instead of redirecting
                return render(request, 'events/import_yaml.html', {
                    'error_message': f'Preview failed: {str(e)}'
                })
        
        # Parse YAML for actual import
        try:
            yaml_data = yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            messages.error(request, f'Invalid YAML: {str(e)}')
            return redirect('admin:index')
        
        # Wrap entire import in a transaction for production database compatibility
        # This ensures all-or-nothing behavior and prevents connection pool issues
        try:
            with transaction.atomic():
                return _perform_import(yaml_data, request)
        except Exception as e:
            # Log the full error for debugging
            import traceback
            error_type = type(e).__name__
            error_details = str(e)
            tb_str = traceback.format_exc()
            logger.error(f"Import failed: {error_type}: {error_details}\n{tb_str}")
            
            # Return detailed error information
            return JsonResponse({
                'success': False,
                'error': f'Import failed: {error_details}',
                'error_type': error_type,
                'error_details': error_details
            }, status=500)
    except Exception as e:
        # Catch any other unexpected errors
        import traceback
        error_type = type(e).__name__
        error_details = str(e)
        tb_str = traceback.format_exc()
        logger.error(f"Unexpected error in import_yaml: {error_type}: {error_details}\n{tb_str}")
        
        return JsonResponse({
            'success': False,
            'error': f'Unexpected error: {error_details}',
            'error_type': error_type,
            'error_details': error_details
        }, status=500)


def _perform_import(yaml_data, request):
    """Perform the actual import operation within a transaction"""
    # Initialize tracking lists
    created_services = []
    created_field_types = []
    created_fields = []
    created_db_payloads = []
    created_db_fields = []
    
    # Extract basic info
    info = yaml_data.get('info', {})
    project_name = info.get('title', 'Imported Project').split(' ')[0]  # Extract project name
    
    # Extract service name: prefer x-service-name, otherwise try better parsing from title
    # x-service-name is in kebab-case, convert to proper name
    x_service_name = info.get('x-service-name', '')
    if x_service_name:
        # Convert kebab-case to title case (e.g., "ocpp-gateway-service" -> "Ocpp Gateway Service")
        service_name = ' '.join(word.capitalize() for word in x_service_name.split('-'))
    else:
        # Fallback: extract from title - take last 2-3 words as service name
        title_words = info.get('title', 'Imported Service').split(' ')
        if len(title_words) >= 3:
            # Take last 2 words (e.g., "OCPP Gateway Service OCPP Bridge" -> "OCPP Bridge")
            service_name = ' '.join(title_words[-2:])
        elif len(title_words) == 2:
            service_name = title_words[1]
        else:
            service_name = 'Imported Service'
    
    # Create or get project
    project_slug = re.sub(r'[^a-zA-Z0-9]', '', project_name.lower())[:20]
    try:
        project, created = _safe_get_or_create(
            Project,
            slug_name=project_slug,
            defaults={'name': project_name}
        )
    except Exception as e:
        logger.error(f"Failed to create/get Project: {str(e)}")
        raise
    
    # Create or get service
    service_slug = re.sub(r'[^a-zA-Z0-9]', '', service_name.lower())[:200]
    original_title = info.get('title', '')
    try:
        service, created = _safe_get_or_create(
            Service,
            created_list=created_services,
            project=project,
            slug_name=service_slug,
            defaults={
                'name': service_name,
                'asyncapi_version': yaml_data.get('asyncapi', '3.0.0'),
                'version': info.get('version', '1.0.0'),
                'description': info.get('description', 'Imported service'),
                'original_title': original_title,
                'x_general_name': info.get('x-general-name', ''),
                'x_service_name': info.get('x-service-name', ''),
                'x_transport': info.get('x-transport'),
                'x_service_ip': info.get('x-service-ip')
            }
        )
    except Exception as e:
        logger.error(f"Failed to create/get Service: {str(e)}")
        raise
    # Update metadata even if service already exists
    if not created:
        service.x_general_name = info.get('x-general-name', '') or service.x_general_name
        service.x_service_name = info.get('x-service-name', '') or service.x_service_name
        service.original_title = original_title or service.original_title
        service.x_transport = info.get('x-transport') or service.x_transport
        service.x_service_ip = info.get('x-service-ip') or service.x_service_ip
        service.save()

    # Import HTTP clients
    for hc_data in info.get('x-http-clients', []):
        hc_name = hc_data.get('name', '')
        if not hc_name:
            continue
        try:
            hc, _ = HTTPClient.objects.get_or_create(service=service, name=hc_name)
            for f_data in hc_data.get('fields', []):
                f_name = f_data.get('name', '')
                if f_name:
                    HTTPClientField.objects.get_or_create(client=hc, name=f_name)
        except Exception as hc_error:
            logger.warning(f"Failed to create HTTPClient {hc_name}: {str(hc_error)}")

    # Import gRPC clients
    for gc_data in info.get('x-grpc-clients', []):
        gc_name = gc_data.get('name', '')
        if not gc_name:
            continue
        try:
            gc, _ = GRPCClient.objects.update_or_create(
                service=service,
                name=gc_name,
                defaults={
                    'module': gc_data.get('module'),
                    'proto_service': gc_data.get('service'),
                }
            )
            for method_data in gc_data.get('methods', []):
                method_name = method_data.get('name', '')
                if not method_name:
                    continue
                GRPCMethod.objects.update_or_create(
                    client=gc,
                    name=method_name,
                    defaults={
                        'request': method_data.get('request', ''),
                        'response': method_data.get('response', ''),
                        'input_field': method_data.get('input_field'),
                        'output_field': method_data.get('output_field'),
                    }
                )
        except Exception as gc_error:
            logger.warning(f"Failed to create GRPCClient {gc_name}: {str(gc_error)}")
    
    # Create domain
    try:
        domain, created = _safe_get_or_create(
            Domain,
            project=project,
            name='default'
        )
    except Exception as e:
        logger.error(f"Failed to create/get Domain: {str(e)}")
        raise
    
    # Create event type
    try:
        event_type, created = _safe_get_or_create(
            EventType,
            name='event'
        )
    except Exception as e:
        logger.error(f"Failed to create/get EventType: {str(e)}")
        raise
    
    # Process channels and create events
    channels = yaml_data.get('channels', {})
    operations = yaml_data.get('operations', {})
    yaml_messages = yaml_data.get('components', {}).get('messages', {})
    schemas = yaml_data.get('components', {}).get('schemas', {})
    operation_jwt_by_channel = {}
    operation_http_method_by_channel = {}

    for op_data in operations.values():
        channel_ref = op_data.get('channel', {}).get('$ref', '')
        if not channel_ref:
            continue
        ch_name = channel_ref.split('/')[-1]
        if not ch_name:
            continue
        if 'x-jwt' in op_data:
            operation_jwt_by_channel[ch_name] = op_data.get('x-jwt')
        if 'x-http-method' in op_data:
            operation_http_method_by_channel[ch_name] = op_data.get('x-http-method')
    
    created_events = []
    created_payloads = []
    
    for channel_name, channel_data in channels.items():
            # Convert PascalCase to snake_case for event name
            event_snake_name = re.sub('([A-Z]+)', r'_\1', channel_name).lower().strip('_')
            
            # Parse messages from channel (support both dict and array formats for backward compatibility)
            channel_messages = channel_data.get('messages', {})
            
            # Extract payloads from messages
            request_payload = None
            response_payload = None
            
            # Handle both dictionary format (new) and array format (old)
            if isinstance(channel_messages, dict):
                # New dictionary format
                for message_name, message_data in channel_messages.items():
                    if message_name in yaml_messages:
                        message_ref = yaml_messages[message_name]
                        payload_ref = message_ref.get('payload', {}).get('$ref', '')
                        if payload_ref:
                            # Extract payload name from reference
                            payload_name = payload_ref.split('/')[-1].replace('Payload', '')
                            
                            # Try to find matching payload first
                            matching_payload = find_matching_payload(project, payload_name, schemas, schemas)
                            if matching_payload:
                                payload = matching_payload
                            else:
                                # Create new payload
                                try:
                                    payload, created = _safe_get_or_create(
                                        Payload,
                                        created_list=created_payloads,
                                        project=project,
                                        name=payload_name,
                                        defaults={
                                            'name': payload_name,
                                            'description': schemas.get(f'Data_{payload_name}Payload', {}).get('description', '')
                                        }
                                    )
                                except Exception as payload_error:
                                    logger.warning(f"Failed to create Payload {payload_name}: {str(payload_error)}")
                                    continue
                            
                            # Assign to request or response based on message name
                            if message_name.endswith('Response'):
                                response_payload = payload
                            else:
                                request_payload = payload
            elif isinstance(channel_messages, list):
                # Old array format (backward compatibility)
                for message_item in channel_messages:
                    if isinstance(message_item, dict):
                        for message_name, message_data in message_item.items():
                            if message_name in yaml_messages:
                                message_ref = yaml_messages[message_name]
                                payload_ref = message_ref.get('payload', {}).get('$ref', '')
                                if payload_ref:
                                    # Extract payload name from reference
                                    payload_name = payload_ref.split('/')[-1].replace('Payload', '')
                                    
                                    # Try to find matching payload first
                                    matching_payload = find_matching_payload(project, payload_name, schemas, schemas)
                                    if matching_payload:
                                        payload = matching_payload
                                    else:
                                        # Create new payload
                                        try:
                                            payload, created = _safe_get_or_create(
                                                Payload,
                                                created_list=created_payloads,
                                                project=project,
                                                name=payload_name,
                                                defaults={
                                                    'name': payload_name,
                                                    'description': schemas.get(f'Data_{payload_name}Payload', {}).get('description', '')
                                                }
                                            )
                                        except Exception as payload_error:
                                            logger.warning(f"Failed to create Payload {payload_name}: {str(payload_error)}")
                                            continue
                                    
                                    # Assign to request or response based on message name
                                    if message_name.endswith('Response'):
                                        response_payload = payload
                                    else:
                                        request_payload = payload
            
            # Create event
            jwt_value = channel_data.get('x-jwt')
            if jwt_value is None:
                jwt_value = operation_jwt_by_channel.get(channel_name)
            if jwt_value is None:
                jwt_value = False
            # Resolve http_method: new format has it on operations; old format had x-is-post on channels
            http_method = operation_http_method_by_channel.get(channel_name)
            if http_method is None:
                is_post_legacy = channel_data.get('x-is-post')
                if is_post_legacy is not None:
                    http_method = 'post' if is_post_legacy else 'get'
            try:
                event, created = _safe_get_or_create(
                    Event,
                    created_list=created_events,
                    domain=domain,
                    name=event_snake_name,
                    defaults={
                        'type': event_type,
                        'payload': request_payload,
                        'response_payload': response_payload,
                        'is_sync': channel_data.get('x-is-sync', True),
                        'http_method': http_method,
                        'is_jwt': jwt_value,
                        'address': channel_data.get('address'),
                        'endpoint': f"/{channel_name}",
                        'description': channel_data.get('description', ''),
                        'summary': channel_data.get('summary', '')
                    }
                )
            except Exception as event_error:
                logger.warning(f"Failed to create Event {event_snake_name}: {str(event_error)}")
                continue

            # Update existing event with new data if not created
            if not created:
                event.payload = request_payload
                event.response_payload = response_payload
                event.is_sync = channel_data.get('x-is-sync', True)
                event.http_method = http_method or event.http_method
                event.is_jwt = jwt_value
                event.address = channel_data.get('address')
                event.endpoint = f"/{channel_name}"
                event.description = channel_data.get('description', '')
                event.summary = channel_data.get('summary', '')
                event.save()

            # Import x-db-operation for async channels
            db_op_data = channel_data.get('x-db-operation')
            if db_op_data and not event.is_sync:
                try:
                    DbOperation.objects.update_or_create(
                        event=event,
                        defaults={
                            'type': db_op_data.get('type', 'block'),
                            'schema': db_op_data.get('schema', ''),
                            'lookup_field': db_op_data.get('lookup-field', ''),
                            'lookup_field_2': db_op_data.get('lookup-field-2'),
                            'status': db_op_data.get('status'),
                            'function_name': db_op_data.get('function-name')
                        }
                    )
                except Exception as db_op_error:
                    logger.warning(f"Failed to create DbOperation for event {event.name}: {str(db_op_error)}")

            created_events.append(event)
    
    # Process operations to set consumes/publishes relationships and store endpoints
    for op_name, op_data in operations.items():
        action = op_data.get('action')
        channel_ref = op_data.get('channel', {}).get('$ref', '')
        endpoint = op_data.get('x-endpoint', '')
        
        if channel_ref:
            channel_name = channel_ref.split('/')[-1]
            
            # Find the corresponding event
            # Try to find the exact event first (for Response channels)
            event_snake_name = re.sub('([A-Z]+)', r'_\1', channel_name).lower().strip('_')
            event = None
            
            try:
                event = Event.objects.get(domain=domain, name=event_snake_name)
            except Event.DoesNotExist:
                # If not found, try without Response suffix (for backward compatibility)
                event_name = channel_name.replace('Response', '')
                event_snake_name = re.sub('([A-Z]+)', r'_\1', event_name).lower().strip('_')
                try:
                    event = Event.objects.get(domain=domain, name=event_snake_name)
                except Event.DoesNotExist:
                    continue
            
            # Store endpoint if provided (for both send and receive operations)
            if endpoint:
                event.endpoint = endpoint
                event.save()
            
            try:
                if action == 'receive':
                    # Check if already exists to avoid duplicate key errors
                    if not service.consumes.filter(id=event.id).exists():
                        service.consumes.add(event)
                elif action == 'send':
                    # Check if already exists to avoid duplicate key errors
                    if not service.publishes.filter(id=event.id).exists():
                        service.publishes.add(event)
            except (OperationalError, IntegrityError, Exception) as m2m_error:
                # Log but continue - don't let ManyToMany errors block the import
                logger.warning(f"Failed to add event {event.name} to service {service.name} ({action}): {str(m2m_error)}")
                continue
    
    # Ensure all Response channels are linked to service
    # This is a safety net to catch any Response events that weren't linked via operations
    # Django's .add() is idempotent, so it's safe to call even if already linked
    for channel_name, channel_data in channels.items():
        if channel_name.endswith('Response'):
            event_snake_name = re.sub('([A-Z]+)', r'_\1', channel_name).lower().strip('_')
            try:
                event = Event.objects.get(domain=domain, name=event_snake_name)
                # Response channels are typically published (sent) by the service
                # Check if already linked to avoid unnecessary database calls
                try:
                    if not service.publishes.filter(id=event.id).exists() and not service.consumes.filter(id=event.id).exists():
                        service.publishes.add(event)
                except Exception as m2m_error:
                    # Log but continue - don't let ManyToMany errors block the import
                    logger.warning(f"Failed to add response event {event.name} to service {service.name}: {str(m2m_error)}")
                    pass
            except Event.DoesNotExist:
                # Event doesn't exist, skip
                pass
    
    # Process schemas to create field types and fields
    # Lists are already initialized at the start of _perform_import
    
    # Collect all payload names that are already linked to channels/operations
    linked_payload_names = set()
    for event in created_events:
        if event.payload:
            linked_payload_names.add(event.payload.name)
        if event.response_payload:
            linked_payload_names.add(event.response_payload.name)
    # Also check existing events
    for event in Event.objects.filter(domain__project=project):
        if event.payload:
            linked_payload_names.add(event.payload.name)
        if event.response_payload:
            linked_payload_names.add(event.response_payload.name)
    
    for schema_name, schema_data in schemas.items():
        if schema_name.startswith('Data_'):
            # This is a data schema, find corresponding payload
            payload_name = schema_name.replace('Data_', '').replace('Payload', '')
            try:
                payload = Payload.objects.get(project=project, name=payload_name)
            except Payload.DoesNotExist:
                # Payload doesn't exist - check if it's a standalone payload
                # (i.e., the corresponding Payload schema exists in schemas)
                payload_schema_name = f'{payload_name}Payload'
                if payload_schema_name in schemas and payload_name not in linked_payload_names:
                    # This is a standalone payload - create it
                    payload_schema_data = schemas[payload_schema_name]
                    try:
                        payload, created = _safe_get_or_create(
                            Payload,
                            created_list=created_payloads,
                            project=project,
                            name=payload_name,
                            defaults={
                                'name': payload_name,
                                'description': payload_schema_data.get('description', '')
                            }
                        )
                    except Exception as payload_error:
                        logger.warning(f"Failed to create Payload {payload_name}: {str(payload_error)}")
                        continue
                else:
                    # Not a standalone payload or already linked, skip
                    continue
            
            # Process properties
            properties = schema_data.get('properties', {})
            required_fields = schema_data.get('required', [])
            
            for field_name, field_data in properties.items():
                    try:
                        # Create or get field type with comprehensive handling
                        field_type = create_or_get_field_type(project, field_data, created_field_types)
                        
                        # Skip if field_type is None (fallback failed)
                        if field_type is None:
                            logger.warning(f"Skipping Field {field_name} for payload: no valid FieldType available")
                            continue
                        
                        # Handle array items and schema references
                        array_items_type = None
                        array_items_ref = None
                        schema_ref = None
                        
                        # Check for array items
                        if field_data.get('type') == 'array' and 'items' in field_data:
                            items = field_data['items']
                            if isinstance(items, dict):
                                if 'type' in items:
                                    array_items_type = items['type']
                                if '$ref' in items:
                                    array_items_ref = items['$ref']
                        
                        # Check for schema reference
                        if '$ref' in field_data:
                            schema_ref = field_data['$ref']
                        
                        # Create field with all attributes
                        try:
                            field, created = _safe_get_or_create(
                                Field,
                                created_list=created_fields,
                                payload=payload,
                                name=field_name,
                                defaults={
                                    'type': field_type,
                                    'required': field_name in required_fields,
                                    'description': field_data.get('description', ''),
                                    'minimum': field_data.get('minimum'),
                                    'maximum': field_data.get('maximum'),
                                    'array_items_type': array_items_type,
                                    'array_items_ref': array_items_ref,
                                    'schema_ref': schema_ref
                                }
                            )
                        except Exception as field_create_error:
                            logger.warning(f"Failed to create Field {field_name} for payload {payload_name}: {str(field_create_error)}")
                            continue
                    except Exception as field_error:
                        # Log but continue - don't let individual field errors block the import
                        logger.warning(f"Failed to create Field {field_name} for payload {payload_name}: {str(field_error)}")
                        continue
        
        elif schema_name.endswith('Payload') and not schema_name.startswith('Data_') and not schema_name.startswith('DB_'):
            # This is a main payload schema - check if it has inline data object
            # (instead of $ref to Data_* schema)
            payload_name = schema_name.replace('Payload', '')
            payload_properties = schema_data.get('properties', {})
            data_property = payload_properties.get('data', {})
            
            # Check if data property is an inline object (not a $ref)
            if isinstance(data_property, dict) and 'type' in data_property and data_property.get('type') == 'object':
                # This payload has inline data object, not a Data_* schema reference
                try:
                    payload = Payload.objects.get(project=project, name=payload_name)
                    
                    # Extract properties from inline data object
                    inline_data_properties = data_property.get('properties', {})
                    inline_required_fields = data_property.get('required', [])
                    
                    for field_name, field_data in inline_data_properties.items():
                        try:
                            # Create or get field type
                            field_type = create_or_get_field_type(project, field_data, created_field_types)
                            
                            # Skip if field_type is None (fallback failed)
                            if field_type is None:
                                logger.warning(f"Skipping Field {field_name} for inline payload: no valid FieldType available")
                                continue
                            
                            # Handle array items and schema references
                            array_items_type = None
                            array_items_ref = None
                            schema_ref = None
                            
                            if field_data.get('type') == 'array' and 'items' in field_data:
                                items = field_data['items']
                                if isinstance(items, dict):
                                    if 'type' in items:
                                        array_items_type = items['type']
                                    if '$ref' in items:
                                        array_items_ref = items['$ref']
                            
                            if '$ref' in field_data:
                                schema_ref = field_data['$ref']
                            
                            # Create field
                            try:
                                field, created = _safe_get_or_create(
                                    Field,
                                    created_list=created_fields,
                                    payload=payload,
                                    name=field_name,
                                    defaults={
                                        'type': field_type,
                                        'required': field_name in inline_required_fields,
                                        'description': field_data.get('description', ''),
                                        'minimum': field_data.get('minimum'),
                                        'maximum': field_data.get('maximum'),
                                        'array_items_type': array_items_type,
                                        'array_items_ref': array_items_ref,
                                        'schema_ref': schema_ref
                                    }
                                )
                            except Exception as field_create_error:
                                logger.warning(f"Failed to create Field {field_name} for inline payload: {str(field_create_error)}")
                                continue
                        except Exception as field_error:
                            # Log but continue - don't let individual field errors block the import
                            logger.warning(f"Failed to create Field {field_name} for inline payload {payload_name}: {str(field_error)}")
                            continue
                except Payload.DoesNotExist:
                    # Payload doesn't exist yet, skip (it will be handled elsewhere)
                    continue
        
        elif schema_name.startswith('DB_'):
                # This is a database schema
                # Wrap in try/except to prevent blocking FieldType/Field creation if service_id column doesn't exist
                try:
                    db_payload_name = schema_name.replace('DB_', '')
                    try:
                        db_payload, created = _safe_get_or_create(
                            DatabasePayload,
                            created_list=created_db_payloads,
                            project=project,
                            service=service,
                            name=db_payload_name,
                            defaults={
                                'name': db_payload_name,
                                'service': service,
                                'description': schema_data.get('description'),
                                'create_rest': schema_data.get('x-create-rest', False),
                                'x_parser_schema_id': schema_data.get('x-parser-schema-id'),
                                'x_derives_from': schema_data.get('x-derives-from')
                            }
                        )
                    except Exception as db_payload_error:
                        logger.warning(f"Failed to create DatabasePayload {db_payload_name}: {str(db_payload_error)}")
                        # Continue with FieldType creation even if DatabasePayload fails
                        continue
                    
                    # Process properties
                    properties = schema_data.get('properties', {})
                    required_fields = schema_data.get('required', [])
                    
                    for field_name, field_data in properties.items():
                        try:
                            # Create or get field type with comprehensive handling
                            # This should work independently of DatabasePayload
                            field_type = create_or_get_field_type(project, field_data, created_field_types)
                            
                            # Skip if field_type is None (fallback failed)
                            if field_type is None:
                                logger.warning(f"Skipping DatabaseField {field_name} for {db_payload_name}: no valid FieldType available")
                                continue
                            
                            # Create database field with all attributes
                            try:
                                db_field, created = _safe_get_or_create(
                                    DatabaseField,
                                    created_list=created_db_fields,
                                    payload=db_payload,
                                    name=field_name,
                                    defaults={
                                        'type': field_type,
                                        'required': field_name in required_fields,
                                        'description': field_data.get('description', ''),
                                        'minimum': field_data.get('minimum'),
                                        'maximum': field_data.get('maximum'),
                                        'x_type': field_data.get('x-type'),
                                        'x_type_override': field_data.get('x-type-override'),
                                        'x_size': field_data.get('x-size'),
                                        'x_unique': field_data.get('x-unique', False),
                                        'x_unique_index': field_data.get('x-unique-index', False),
                                        'x_index': field_data.get('x-index', False),
                                        'x_not_null': field_data.get('x-not-null', False),
                                        'x_nullable': field_data.get('x-nullable', False),
                                        'x_check': field_data.get('x-check'),
                                        'x_column': field_data.get('x-column'),
                                        'x_comment': field_data.get('x-comment'),
                                        'x_serializer': field_data.get('x-serializer'),
                                        'x_ignore': field_data.get('x-ignore', False),
                                        'x_precision': field_data.get('x-precision'),
                                        'x_scale': field_data.get('x-scale'),
                                        'x_auto_create_time': field_data.get('x-auto-create-time', False),
                                        'x_auto_update_time': field_data.get('x-auto-update-time', False),
                                        'x_auto_increment': field_data.get('x-auto-increment', False),
                                        'default_value': field_data.get('x-default'),
                                        'x_relation_schema_id': field_data.get('x-relation-schema-id'),
                                        'x_foreign_key': field_data.get('x-foreign-key'),
                                        'x_references': field_data.get('x-references'),
                                        'x_cascade_update': field_data.get('x-cascade-update'),
                                        'x_cascade_delete': field_data.get('x-cascade-delete'),
                                        'x_many_to_many': field_data.get('x-many-to-many'),
                                        'x_join_table': field_data.get('x-join-table'),
                                        'x_join_foreign_key': field_data.get('x-join-foreign-key'),
                                        'x_join_references': field_data.get('x-join-references'),
                                        'x_association_autocreate': field_data.get('x-association-autocreate', False),
                                        'x_association_autoupdate': field_data.get('x-association-autoupdate', False),
                                        'x_association_save_reference': field_data.get('x-association-save-reference', False),
                                        'x_embedded': field_data.get('x-embedded', False),
                                        'x_embedded_prefix': field_data.get('x-embedded-prefix'),
                                        'x_polymorphic': field_data.get('x-polymorphic'),
                                        'x_polymorphic_value': field_data.get('x-polymorphic-value'),
                                        'x_association_foreign_key': field_data.get('x-association-foreign-key'),
                                        'x_constraint': field_data.get('x-constraint'),
                                        'x_preload': field_data.get('x-preload', False),
                                        'x_primary_key': field_data.get('x-primary-key', False),
                                    }
                                )
                            except Exception as db_field_error:
                                logger.warning(f"Failed to create DatabaseField {field_name} for {db_payload_name}: {str(db_field_error)}")
                                continue
                        except Exception as field_error:
                            # Log but continue - don't let individual field errors block the import
                            logger.warning(f"Failed to create DatabaseField {field_name} for {db_payload_name}: {str(field_error)}")
                            continue
                    
                    # Add to service (only if column exists)
                    try:
                        if not service.database_payloads.filter(id=db_payload.id).exists():
                            service.database_payloads.add(db_payload)
                    except (OperationalError, IntegrityError) as db_error:
                        # Skip if service_id column doesn't exist or if there's an integrity error
                        logger.warning(f"Failed to add database payload {db_payload.name} to service {service.name}: {str(db_error)}")
                        pass
                except OperationalError as db_error:
                    # If service_id column doesn't exist, skip DatabasePayload creation
                    # but continue with FieldType creation for the properties
                    if 'no such column' in str(db_error).lower() and 'service_id' in str(db_error):
                        # Still create FieldTypes from the properties even if we can't create DatabasePayload
                        properties = schema_data.get('properties', {})
                        for field_name, field_data in properties.items():
                            try:
                                field_type = create_or_get_field_type(project, field_data, created_field_types)
                            except Exception as ft_error:
                                logger.warning(f"Failed to create FieldType for {field_name} in {schema_name}: {str(ft_error)}")
                                continue
                    else:
                        # Re-raise if it's a different error
                        raise
                except Exception as db_payload_error:
                    # Log other errors but continue
                    logger.error(f"Failed to create DatabasePayload {schema_name}: {str(db_payload_error)}")
                    # Still try to create FieldTypes from properties
                    properties = schema_data.get('properties', {})
                    for field_name, field_data in properties.items():
                        try:
                            field_type = create_or_get_field_type(project, field_data, created_field_types)
                        except Exception:
                            continue
        
        elif schema_name.endswith('Payload'):
                # This is a main payload schema
                # Check if it's already linked to a channel/operation
                payload_name = schema_name.replace('Payload', '')
                if payload_name not in linked_payload_names:
                    # This is a standalone payload not linked to any channel/operation
                    # Import it as a standalone payload
                    try:
                        payload, created = _safe_get_or_create(
                            Payload,
                            created_list=created_payloads,
                            project=project,
                            name=payload_name,
                            defaults={
                                'name': payload_name,
                                'description': schema_data.get('description', '')
                            }
                        )
                    except Exception as payload_error:
                        logger.warning(f"Failed to create Payload {payload_name}: {str(payload_error)}")
                        continue
                    
                    # Check if payload has inline data object or Data_* schema
                    payload_properties = schema_data.get('properties', {})
                    data_property = payload_properties.get('data', {})
                    
                    # Check if data property is an inline object (not a $ref)
                    if isinstance(data_property, dict) and 'type' in data_property and data_property.get('type') == 'object':
                        # Process inline data object
                        inline_data_properties = data_property.get('properties', {})
                        inline_required_fields = data_property.get('required', [])
                        
                        for field_name, field_data in inline_data_properties.items():
                            try:
                                field_type = create_or_get_field_type(project, field_data, created_field_types)
                                
                                # Skip if field_type is None (fallback failed)
                                if field_type is None:
                                    logger.warning(f"Skipping Field {field_name} for inline payload: no valid FieldType available")
                                    continue
                                
                                array_items_type = None
                                array_items_ref = None
                                schema_ref = None
                                
                                if field_data.get('type') == 'array' and 'items' in field_data:
                                    items = field_data['items']
                                    if isinstance(items, dict):
                                        if 'type' in items:
                                            array_items_type = items['type']
                                        if '$ref' in items:
                                            array_items_ref = items['$ref']
                                
                                if '$ref' in field_data:
                                    schema_ref = field_data['$ref']
                                
                                field, created = Field.objects.get_or_create(
                                    payload=payload,
                                    name=field_name,
                                    defaults={
                                        'type': field_type,
                                        'required': field_name in inline_required_fields,
                                        'description': field_data.get('description', ''),
                                        'minimum': field_data.get('minimum'),
                                        'maximum': field_data.get('maximum'),
                                        'array_items_type': array_items_type,
                                        'array_items_ref': array_items_ref,
                                        'schema_ref': schema_ref
                                    }
                                )
                                if created:
                                    created_fields.append(field)
                            except Exception as field_error:
                                # Log but continue - don't let individual field errors block the import
                                logger.warning(f"Failed to create Field {field_name} for inline payload {payload_name}: {str(field_error)}")
                                continue
                    else:
                        # Process the corresponding Data_* schema if it exists
                        data_schema_name = f'Data_{schema_name}'
                        if data_schema_name in schemas:
                            data_schema_data = schemas[data_schema_name]
                            properties = data_schema_data.get('properties', {})
                            required_fields = data_schema_data.get('required', [])
                        
                        for field_name, field_data in properties.items():
                            try:
                                # Create or get field type with comprehensive handling
                                field_type = create_or_get_field_type(project, field_data, created_field_types)
                                
                                # Handle array items and schema references
                                array_items_type = None
                                array_items_ref = None
                                schema_ref = None
                                
                                # Check for array items
                                if field_data.get('type') == 'array' and 'items' in field_data:
                                    items = field_data['items']
                                    if isinstance(items, dict):
                                        if 'type' in items:
                                            array_items_type = items['type']
                                        if '$ref' in items:
                                            array_items_ref = items['$ref']
                                
                                # Check for schema reference
                                if '$ref' in field_data:
                                    schema_ref = field_data['$ref']
                                
                                # Create field with all attributes
                                try:
                                    field, created = _safe_get_or_create(
                                        Field,
                                        created_list=created_fields,
                                        payload=payload,
                                        name=field_name,
                                        defaults={
                                            'type': field_type,
                                            'required': field_name in required_fields,
                                            'description': field_data.get('description', ''),
                                            'minimum': field_data.get('minimum'),
                                            'maximum': field_data.get('maximum'),
                                            'array_items_type': array_items_type,
                                            'array_items_ref': array_items_ref,
                                            'schema_ref': schema_ref
                                        }
                                    )
                                except Exception as field_create_error:
                                    logger.warning(f"Failed to create Field {field_name} for payload: {str(field_create_error)}")
                                    continue
                            except Exception as field_error:
                                # Log but continue - don't let individual field errors block the import
                                logger.warning(f"Failed to create Field {field_name} for payload {payload_name}: {str(field_error)}")
                                continue
                # If already linked, skip (it was handled above)
                continue
        
        else:
                # This might be a standalone field type (enum, custom type, etc.)
                if 'enum' in schema_data:
                    # This is an enum type
                    try:
                        field_type, created = _safe_get_or_create(
                            FieldType,
                            created_list=created_field_types,
                            project=project,
                            name=schema_name,
                            defaults={
                                'type': 'string',
                                'custom_type': True,
                                'enum_choices': ','.join(schema_data.get('enum', [])),
                                'format': schema_data.get('format'),
                                'max_length': schema_data.get('maxLength')
                            }
                        )
                    except Exception as ft_error:
                        logger.warning(f"Failed to create FieldType {schema_name}: {str(ft_error)}")
                
                elif schema_data.get('type') in ['string', 'number', 'integer', 'boolean', 'array', 'object']:
                    # This is a basic field type
                    try:
                        # For complex object types, store the full schema definition
                        schema_def = None
                        if schema_data.get('type') == 'object' and 'properties' in schema_data:
                            # Store full schema definition for complex objects
                            schema_def = json.dumps(schema_data)
                        
                        field_type, created = _safe_get_or_create(
                            FieldType,
                            created_list=created_field_types,
                            project=project,
                            name=schema_name,   
                            defaults={
                                'type': schema_data.get('type', 'string'),
                                'custom_type': True,
                                'format': schema_data.get('format'),
                                'max_length': schema_data.get('maxLength'),
                                'schema_definition': schema_def
                            }
                        )
                        # Update schema definition if it exists and wasn't set
                        if not created and schema_data.get('type') == 'object' and 'properties' in schema_data and not field_type.schema_definition:
                            field_type.schema_definition = json.dumps(schema_data)
                            field_type.save()
                    except Exception as ft_error:
                        logger.warning(f"Failed to create FieldType {schema_name}: {str(ft_error)}")
        
    # Add success message
    success_message = f'Successfully imported YAML! Created: {len(created_events)} events, {len(created_payloads)} payloads, {len(created_field_types)} field types, {len(created_fields)} fields, {len(created_db_payloads)} database payloads, {len(created_db_fields)} database fields.'
    
    # Return JSON response for AJAX requests
    return JsonResponse({
        'success': True,
        'message': success_message,
        'created_counts': {
            'events': len(created_events),
            'payloads': len(created_payloads),
            'field_types': len(created_field_types),
            'fields': len(created_fields),
            'db_payloads': len(created_db_payloads),
            'db_fields': len(created_db_fields)
        }
    })
