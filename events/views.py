import yaml
import re
import json
import logging
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
        field_type_name = field_data.get('type', 'string')
        
        # Handle enum types
        if 'enum' in field_data:
            enum_choices = ','.join(field_data.get('enum', []))
            # Create a unique name for enum types
            enum_type_name = f"{field_type_name}_enum_{hash(enum_choices) % 10000}"
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
            # Handle regular field types - create unique types for different formats
            format_value = field_data.get('format')
            if format_value:
                # Create a unique name for field types with formats
                field_type_name_with_format = f"{field_type_name}_{format_value}"
            else:
                field_type_name_with_format = field_type_name
            
            field_type = _safe_get_or_create_field_type(
                project=project,
                name=field_type_name_with_format,
                defaults={
                    'type': field_type_name,
                    'custom_type': False,
                    'format': format_value,
                    'max_length': field_data.get('maxLength')
                },
                created_field_types=created_field_types
            )
        
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
            # Close any stale connections before attempting operation
            connection.close_if_unusable_or_obsolete()
            
            # Try to get existing first
            try:
                field_type = FieldType.objects.get(project=project, name=name)
                return field_type
            except FieldType.DoesNotExist:
                # Doesn't exist, try to create it in a separate transaction
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
                        # Reset connection and retry
                        connection.close()
                        continue
                    else:
                        # Last attempt - try to get it one more time
                        try:
                            return FieldType.objects.get(project=project, name=name)
                        except FieldType.DoesNotExist:
                            logger.error(f"FieldType {name} does not exist and cannot be created after {max_retries} attempts: {str(db_error)}")
                            return _get_fallback_field_type(project)
                except (DatabaseError, Exception) as db_error:
                    # For other database errors, reset connection and retry
                    if attempt < max_retries - 1:
                        logger.warning(f"Database error creating FieldType {name} (attempt {attempt + 1}): {str(db_error)}")
                        connection.close()
                        continue
                    else:
                        logger.error(f"Failed to create FieldType {name} after {max_retries} attempts: {str(db_error)}")
                        return _get_fallback_field_type(project)
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Unexpected error creating FieldType {name} (attempt {attempt + 1}): {str(e)}")
                connection.close()
                continue
            else:
                logger.error(f"Failed to create FieldType {name} after {max_retries} attempts: {str(e)}")
                return _get_fallback_field_type(project)
    
    # Should never reach here, but just in case
    return _get_fallback_field_type(project)


def _get_fallback_field_type(project):
    """Get or create a default string field type as fallback"""
    try:
        connection.close_if_unusable_or_obsolete()
        
        # Try to get existing first
        try:
            return FieldType.objects.get(project=project, name='string')
        except FieldType.DoesNotExist:
            # Doesn't exist, try to create it in a separate transaction
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
                connection.close()
        
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
            connection.close()
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
            # Close any stale connections before attempting operation
            connection.close_if_unusable_or_obsolete()
            
            # Build lookup kwargs (everything except defaults)
            lookup_kwargs = kwargs.copy()
            
            # Try to get existing first
            try:
                instance = model_class.objects.get(**lookup_kwargs)
                return instance, False
            except model_class.DoesNotExist:
                # Doesn't exist, try to create it in a separate transaction
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
                        # Reset connection and retry
                        connection.close()
                        continue
                    else:
                        # Last attempt - try to get it one more time
                        try:
                            return model_class.objects.get(**lookup_kwargs), False
                        except model_class.DoesNotExist:
                            logger.error(f"{model_class.__name__} does not exist and cannot be created after {max_retries} attempts: {str(db_error)}")
                            raise
                except (DatabaseError, Exception) as db_error:
                    # For other database errors, reset connection and retry
                    if attempt < max_retries - 1:
                        logger.warning(f"Database error creating {model_class.__name__} (attempt {attempt + 1}): {str(db_error)}")
                        connection.close()
                        continue
                    else:
                        logger.error(f"Failed to create {model_class.__name__} after {max_retries} attempts: {str(db_error)}")
                        raise
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Unexpected error creating {model_class.__name__} (attempt {attempt + 1}): {str(e)}")
                connection.close()
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

    configuration = {
        'asyncapi': service.asyncapi_version,
        'info': {
            'title': service.original_title if service.original_title else "{0} V2 {1} Service".format(service.project.name, service.name),
            'version': service.version,
            'description': service.description if service.description is not None else "N/A",
            'x-general-name': service.x_general_name if service.x_general_name else service.name.lower(),
            'x-service-name': service.x_service_name if service.x_service_name else service.kebab_name(),
            'x-service-ip': "change-this"
        },
        'servers': {
            'test':
                {
                    'host': "127.0.0.1:9092",
                    'protocol': "kafka-secure",
                    "description": "Test broker",
                }
        },
        'channels': {},
        'operations': {},
        'components': {
            'messages': {},
            'schemas': {},
        }
    }

    for event in service.consumes.all().union(service.publishes.all()):
        # Convert snake_case to PascalCase for channel names
        # Special handling for ID -> Id
        channel_name = ''.join(word.upper() if word.upper() in ['OTP', 'API', 'URL'] else word.capitalize() for word in event.name.split('_'))
        # Fix ID to Id
        channel_name = channel_name.replace('ID', 'Id')
        # Convert snake_case to camelCase for address
        address_name = ''.join(word.upper() if word.upper() in ['OTP', 'API', 'ID', 'URL'] else word.capitalize() for word in event.name.split('_'))
        
        # Use stored description and summary, fallback to generated ones
        description = event.description if event.description else event.name.replace('_', ' ').title()
        summary = event.summary if event.summary else f"{description} for web and mobile"
        
        # Build messages dictionary
        messages_dict = {
            channel_name: {'$ref': "#/components/messages/{0}".format(channel_name)}
        }
        
        # Note: We don't add Response messages to main channels anymore
        # Response channels are exported as separate channels if they exist as separate events

        # Build channel configuration with proper property ordering
        channel_config = {
            'description': description,
            'x-is-sync': event.is_sync
        }
        
        # Add x-is-post before address for sync events
        if event.is_sync:
            channel_config['x-is-post'] = event.is_post
            
        channel_config['address'] = event.address if event.address else address_name[0].lower() + address_name[1:]
        channel_config['summary'] = summary
            
        # Add messages last to match expected format
        channel_config['messages'] = messages_dict
            
        configuration['channels'][channel_name] = channel_config

        # Create operations with simple names and direct message references
        # For Response channels, create operations if:
        # 1. Base event is a receive (command) - most common case
        # 2. Special bidirectional cases: certificateSignedResponse, dataTransferResponse (base is send but Response has operation)
        # Notification responses (base is send) don't have operations except for special cases
        is_response_channel = event.name.endswith('_response')
        should_create_operation = True
        
        if is_response_channel:
            # Find the base event name (without _response)
            base_event_name = event.name.replace('_response', '')
            # Check if base event exists
            try:
                base_event = Event.objects.get(domain=event.domain, name=base_event_name)
                # Special bidirectional cases that have operations even though base is send
                special_cases = ['certificate_signed', 'data_transfer']
                is_special_case = base_event_name in special_cases
                
                # Create operation if:
                # - Base event is a command (in consumes), OR
                # - It's a special bidirectional case (certificateSignedResponse, dataTransferResponse)
                should_create_operation = (base_event in service.consumes.all()) or \
                                         (is_special_case and event in service.publishes.all() and base_event in service.publishes.all())
            except Event.DoesNotExist:
                # Base event doesn't exist, don't create operation
                should_create_operation = False
        
        if should_create_operation:
            # Convert channel name to camelCase for operation name
            base_operation_name = channel_name[0].lower() + channel_name[1:]
            
            # Add proper prefixes based on operation type
            if channel_name.startswith('Create'):
                operation_name = base_operation_name
            elif channel_name.startswith('Get'):
                operation_name = base_operation_name
            elif channel_name.startswith('Update'):
                operation_name = base_operation_name
            elif channel_name.startswith('Delete'):
                operation_name = base_operation_name
            elif channel_name.startswith('User'):
                operation_name = 'create' + channel_name
            else:
                operation_name = base_operation_name
            
            # Use stored endpoint or generate default
            endpoint = event.endpoint if event.endpoint else "/{0}".format(channel_name)
            
            # Determine action based on service relationship
            # If event is in consumes, it's a receive operation
            # If event is in publishes, it's a send operation
            # If in both, default to receive
            if event in service.consumes.all():
                action = 'receive'
            elif event in service.publishes.all():
                action = 'send'
            else:
                # Default to receive if somehow not in either (shouldn't happen)
                action = 'receive'
            
            configuration['operations'][operation_name] = {
                'action': action,
                'channel': {
                    '$ref': "#/channels/{0}".format(channel_name)
                },
                'messages': [
                    {'$ref': "#/channels/{0}/messages/{0}".format(channel_name)}
                ],
                'x-operation-name': channel_name,
                'x-endpoint': endpoint
            }

        # Create message components
        # Generate proper title from channel name with spacing
        title = ' '.join(word.capitalize() for word in re.findall(r'[A-Z][a-z]*', channel_name))
        
        configuration['components']['messages'][channel_name] = {
            'name': channel_name,
            'title': title,
            'summary': event.summary if event.summary else event.name.replace('_', ' ').title(),
            'contentType': 'application/json',
            'payload': {
                '$ref': "#/components/schemas/{0}Payload".format(channel_name)
            }
        }

        # Create response message for sync operations only if there's no separate Response channel event
        # Check if a separate Response channel event exists
        if event.is_sync and event.response_payload and not event.name.endswith('_response'):
            response_channel_name = channel_name + "Response"
            # Check if a separate Response channel event exists
            response_event_exists = service.consumes.filter(name=event.name + '_response').exists() or \
                                   service.publishes.filter(name=event.name + '_response').exists()
            
            # Only create response message if there's no separate Response channel
            if not response_event_exists:
                # Create proper response title formatting with spacing
                # Generate response title from channel name with proper spacing
                response_title = ' '.join(word.capitalize() for word in re.findall(r'[A-Z][a-z]*', response_channel_name))
                
                # Generate response summary based on the original event description
                response_summary = f"Response with {event.description.lower()}" if event.description else f"{event.name.replace('_', ' ').title()} response"
                
                configuration['components']['messages'][response_channel_name] = {
                    'name': response_channel_name,
                    'title': response_title,
                    'summary': response_summary,
                    'contentType': 'application/json',
                    'payload': {
                        '$ref': "#/components/schemas/{0}Payload".format(response_channel_name)
                    }
                }


    # Create payload schemas
    for payload in Payload.objects.all():
        # First, collect data properties to determine if Data_* schema will be empty
        data_schema_name = "Data_{0}Payload".format(payload.name)
        data_properties = {}
        required_fields = []
        
        for field in payload.field_set.all():
            field_property = {}
            
            if field.type.custom_type:
                if field.type.enum_choices is not None:
                    enum_choices = field.type.enum_choices.replace(" ", "").split(",")
                    field_property = {
                        'type': 'string',
                        'enum': enum_choices,
                        'x-type': 'string'
                    }
                    # Don't create separate enum schemas - enums should be inline in properties
                    # Only create enum schema if it's referenced elsewhere (via $ref)
                    # For now, keep enums inline to match original YAML format
                else:
                    field_property = {
                        'type': field.type.type,
                        'x-type': field.type.type
                    }
                    if field.type.max_length and field.type.max_length > 0:
                        field_property['maxLength'] = field.type.max_length
            else:
                field_property = {
                    'type': field.type.type,
                    'x-type': field.type.type
                }
                if field.type.format:
                    field_property['format'] = field.type.format
                if field.type.max_length and field.type.max_length > 0:
                    field_property['maxLength'] = field.type.max_length
                
                # Handle array items
                if field.type.type == 'array':
                    if field.array_items_type:
                        field_property['items'] = {'type': field.array_items_type}
                    elif field.array_items_ref:
                        field_property['items'] = {'$ref': field.array_items_ref}
                
                # Handle schema reference
                if field.schema_ref:
                    field_property = {'$ref': field.schema_ref}
            
            # Add description if field has one (but not if field_property is a $ref)
            if field.description and '$ref' not in field_property:
                field_property['description'] = field.description

            data_properties[field.name] = field_property
            
            if field.required:
                required_fields.append(field.name)

        # Create main payload schema
        payload_schema_name = "{0}Payload".format(payload.name)
        
        # Determine if this is a Response payload and what type
        is_response_payload = payload.name.endswith('Response')
        is_notification_response = is_response_payload and any(
            keyword in payload.name for keyword in ['Notification', 'StatusNotification']
        )
        
        # Determine data property handling:
        # 1. Empty Data_* for notification Response payloads → separate empty schema with $ref
        # 2. Empty Data_* for other payloads → inline empty object
        # 3. Non-empty Data_* for certain Response payloads → inline data (if it's a command response with simple structure)
        # 4. Non-empty Data_* for others → separate schema with $ref
        
        # Response payloads that should use inline data (command responses with simple structure)
        inline_response_payloads = [
            'CustomerInformationResponse', 'GetDisplayMessagesResponse', 
            'GetInstalledCertificateIdsResponse', 'GetMonitoringReportResponse',
            'GetReportResponse', 'GetTransactionStatusResponse'
        ]
        should_use_inline = payload.name in inline_response_payloads and data_properties
        
        if should_use_inline:
            # Use inline data object for these specific Response payloads
            data_property = {
                'type': 'object',
                'properties': data_properties
            }
            if required_fields:
                data_property['required'] = required_fields
        elif data_properties:
            # Data_* schema has properties, use reference
            data_property = {
                '$ref': "#/components/schemas/{0}".format(data_schema_name)
            }
        elif is_notification_response:
            # Empty Data_* for notification Response payloads → separate empty schema with $ref
            data_property = {
                '$ref': "#/components/schemas/{0}".format(data_schema_name)
            }
        else:
            # Empty Data_* for other payloads → inline empty object
            data_property = {
                'type': 'object',
                'properties': {}
            }
        
        configuration['components']['schemas'][payload_schema_name] = {
            'type': 'object',
            'properties': {
                'fromService': {
                    'type': 'string',
                    'default': '',
                    'x-parser-schema-id': 'fromService'
                },
                'sentAt': {
                    'type': 'string',
                    'format': 'date-time',
                    'default': '2025-01-01T00:00:00Z',
                    'x-parser-schema-id': 'sentAt'
                },
                'timeToLive': {
                    'type': 'integer',
                    'default': 3600000,
                    'x-parser-schema-id': 'timeToLive'
                },
                'data': data_property
            }
        }

        # Create Data_* schema if:
        # 1. It has properties and we're not using inline data, OR
        # 2. It's empty but it's a notification Response payload (needs separate empty schema)
        if (data_properties and not should_use_inline) or (not data_properties and is_notification_response):
            schema_config = {
                'type': 'object',
                'properties': data_properties
            }
            
            # Add description if payload has one
            if payload.description:
                schema_config['description'] = payload.description
            
            # Only add required if there are required fields
            if required_fields:
                schema_config['required'] = required_fields
                
            configuration['components']['schemas'][data_schema_name] = schema_config

    # Don't create messages for orphaned payloads - standalone payloads are just schemas, not messages
    # Messages are only created for payloads linked to events (channels)
    # This matches the original YAML format where standalone payloads like GetVariablesAckPayload
    # exist as schemas but don't have corresponding messages

    for dbpayload in DatabasePayload.objects.filter(service=service):
        properties = {}

        for field in dbpayload.databasefield_set.all():
            if field.type.custom_type:
                if field.type.enum_choices is not None:
                    enum_choices = field.type.enum_choices.replace(" ", "").split(",")
                    # Use inline enum instead of separate schema to match original YAML format
                    properties[field.name] = {
                        'type': 'string',
                        'enum': enum_choices,
                        'description': field.description or field.name
                    }
                    # Don't create separate enum schema - keep enums inline
                else:
                    if field.type.type == "string":
                        properties[field.name] = {
                            '$ref': "#/components/schemas/{0}".format(field.type.name)
                        }
                        configuration['components']['schemas'][field.type.name] = {
                            'type': field.type.type,
                            'x-parser-schema-id': field.type.name
                        }
                        if field.type.max_length > 0:
                            configuration['components']['schemas'][field.type.name]['maxLength'] = field.type.max_length

            else:
                properties[field.name] = {
                    'type': field.type.type
                }

                # Add description if available
                if field.description is not None:
                    properties[field.name]['description'] = field.description

                # Add format if available
                if field.type.format is not None:
                    properties[field.name]['format'] = field.type.format
                    
                # Add database-specific x- properties
                if field.x_type is not None:
                    properties[field.name]['x-type'] = field.x_type
                    
                if field.x_unique:
                    properties[field.name]['x-unique'] = True
                    
                if field.x_index:
                    properties[field.name]['x-index'] = True
                    
                if field.default_value is not None:
                    properties[field.name]['default'] = field.default_value
                    
                if field.x_relation_schema_id is not None:
                    properties[field.name]['x-relation-schema-id'] = field.x_relation_schema_id

        schema_config = {
            'type': 'object'
        }
        
        # Add x-parser-schema-id if available
        if dbpayload.x_parser_schema_id:
            schema_config['x-parser-schema-id'] = dbpayload.x_parser_schema_id
        else:
            schema_config['x-parser-schema-id'] = dbpayload.name
            
        # Add x-create-rest if available
        if hasattr(dbpayload, 'create_rest'):
            schema_config['x-create-rest'] = dbpayload.create_rest
            
        # Add x-derives-from if available
        if dbpayload.x_derives_from:
            schema_config['x-derives-from'] = dbpayload.x_derives_from
            
        # Add properties last
        schema_config['properties'] = properties
            
        configuration['components']['schemas']["{1}{0}".format(dbpayload.name, "DB_")] = schema_config

    # Export complex object FieldType schemas (type='object', custom_type=True)
    # These are standalone schemas like ChargingProfile, ChargingSchedule, etc.
    for field_type in FieldType.objects.filter(project=service.project, type='object', custom_type=True):
        schema_def = field_type.get_schema_definition()
        if schema_def:
            # Use the stored schema definition as-is (don't add x-parser-schema-id)
            configuration['components']['schemas'][field_type.name] = schema_def.copy()

    # Custom YAML representer to force double quotes for strings
    class DoubleQuotedString(str):
        pass
    
    def represent_double_quoted_string(dumper, data):
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')
    
    yaml.add_representer(DoubleQuotedString, represent_double_quoted_string)
    
    # Custom YAML representer to force double quotes for endpoints
    class DoubleQuotedEndpoint(str):
        pass

    def represent_double_quoted_endpoint(dumper, data):
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')

    yaml.add_representer(DoubleQuotedEndpoint, represent_double_quoted_endpoint)
    
    # Convert address strings and endpoints to double-quoted strings
    def convert_addresses_to_double_quoted(obj, current_path=""):
        if isinstance(obj, dict):
            new_obj = {}
            for key, value in obj.items():
                new_path = f"{current_path}.{key}" if current_path else key
                if key == 'address' and current_path.startswith('channels.'):
                    # Only convert channel addresses, not database field addresses
                    new_obj[key] = DoubleQuotedString(value)
                elif key == 'x-endpoint':
                    new_obj[key] = DoubleQuotedEndpoint(value)
                else:
                    new_obj[key] = convert_addresses_to_double_quoted(value, new_path)
            return new_obj
        elif isinstance(obj, list):
            return [convert_addresses_to_double_quoted(item, current_path) for item in obj]
        else:
            return obj
    
    configuration_with_quotes = convert_addresses_to_double_quoted(configuration)
    yaml_out = yaml.dump(configuration_with_quotes, sort_keys=False, default_flow_style=False)

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
        event_preview = {
            'name': event_snake_name,
            'domain_name': 'default',
            'type_name': 'event',
            'is_sync': channel_data.get('x-is-sync', True),
            'is_post': channel_data.get('x-is-post', False),
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
                    'x_service_name': info.get('x-service-name', '')
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
            service.save()
        
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
                                        payload, created = Payload.objects.get_or_create(
                                            project=project,
                                            name=payload_name,
                                            defaults={
                                                'name': payload_name,
                                                'description': schemas.get(f'Data_{payload_name}Payload', {}).get('description', '')
                                            }
                                        )
                                        if created:
                                            created_payloads.append(payload)
                                    
                                    # Assign to request or response based on message name
                                    if message_name.endswith('Response'):
                                        response_payload = payload
                                    else:
                                        request_payload = payload
            
            # Create event
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
                        'is_post': channel_data.get('x-is-post', False),
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
                event.is_post = channel_data.get('x-is-post', False)
                event.address = channel_data.get('address')
                event.endpoint = f"/{channel_name}"
                event.description = channel_data.get('description', '')
                event.summary = channel_data.get('summary', '')
                event.save()
            
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
        created_field_types = []
        created_fields = []
        created_db_payloads = []
        created_db_fields = []
        
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
                                        'x_unique': field_data.get('x-unique', False),
                                        'x_index': field_data.get('x-index', False),
                                        'default_value': field_data.get('default'),
                                        'x_relation_schema_id': field_data.get('x-relation-schema-id')
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
                                import logging
                                logger = logging.getLogger(__name__)
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
        
    except Exception as e:
        # Add detailed error message with exception type and message
        import traceback
        error_details = str(e)
        error_type = type(e).__name__
        
        # Get the traceback for debugging
        tb_str = traceback.format_exc()
        
        # Create a detailed error message
        error_message = f'Import failed: {error_type}: {error_details}'
        
        # Log the full traceback for debugging
        logger.error(f"Import failed with error: {error_type}: {error_details}\n{tb_str}")
        
        # Return JSON response for AJAX requests with detailed error
        return JsonResponse({
            'success': False,
            'error': error_message,
            'error_type': error_type,
            'error_details': error_details
        })
