import yaml
import re
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.shortcuts import HttpResponse, render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib import messages

from events.models import *


def create_or_get_field_type(project, field_data, created_field_types):
    """Helper function to create or get field type with comprehensive handling"""
    field_type_name = field_data.get('type', 'string')
    
    # Handle enum types
    if 'enum' in field_data:
        enum_choices = ','.join(field_data.get('enum', []))
        # Create a unique name for enum types
        enum_type_name = f"{field_type_name}_enum_{hash(enum_choices) % 10000}"
        field_type, created = FieldType.objects.get_or_create(
            project=project,
            name=enum_type_name,
            defaults={
                'type': 'string',
                'custom_type': True,
                'enum_choices': enum_choices,
                'format': field_data.get('format'),
                'max_length': field_data.get('maxLength')
            }
        )
    else:
        # Handle regular field types - create unique types for different formats
        format_value = field_data.get('format')
        if format_value:
            # Create a unique name for field types with formats
            field_type_name_with_format = f"{field_type_name}_{format_value}"
        else:
            field_type_name_with_format = field_type_name
            
        field_type, created = FieldType.objects.get_or_create(
            project=project,
            name=field_type_name_with_format,
            defaults={
                'type': field_type_name,
                'custom_type': False,
                'format': format_value,
                'max_length': field_data.get('maxLength')
            }
        )
    
    if created:
        created_field_types.append(field_type)
    
    return field_type


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
            'title': "{0} V2 {1} Service".format(service.project.name, service.name),
            'version': service.version,
            'description': service.description if service.description is not None else "N/A",
            'x-general-name': service.name.lower(),
            'x-service-name': service.kebab_name(),
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
        
        # Add response message for sync events
        if event.is_sync and event.response_payload:
            response_channel_name = channel_name + "Response"
            messages_dict[response_channel_name] = {
                '$ref': "#/components/messages/{0}".format(response_channel_name)
            }

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
        if not event.name.endswith('Response'):
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
            
            configuration['operations'][operation_name] = {
                'action': 'receive',
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

        # Create response message for sync operations
        if event.is_sync and event.response_payload:
            response_channel_name = channel_name + "Response"
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
        # Create main payload schema
        payload_schema_name = "{0}Payload".format(payload.name)
        configuration['components']['schemas'][payload_schema_name] = {
            'type': 'object',
            'properties': {
                'fromService': {
                    'type': 'string',
                    'default': '',
                    'description': 'ServiceID and name of the service that sent the event',
                    'x-parser-schema-id': 'fromService'
                },
                'sentAt': {
                    'type': 'string',
                    'format': 'date-time',
                    'default': '2025-01-01T00:00:00Z',
                    'description': 'Date and time when the message was sent',
                    'x-parser-schema-id': 'sentAt'
                },
                'timeToLive': {
                    'type': 'integer',
                    'default': 3600000,
                    'description': 'Message time to live in milliseconds',
                    'x-parser-schema-id': 'timeToLive'
                },
                'data': {
                    '$ref': "#/components/schemas/Data_{0}Payload".format(payload.name)
                }
            }
        }

        # Create data schema
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
                        'description': field.description or field.name
                    }
                    # Create enum schema if not exists
                    if field.type.name not in configuration['components']['schemas']:
                        configuration['components']['schemas'][field.type.name] = {
                            'type': 'string',
                            'enum': enum_choices,
                            'x-parser-schema-id': field.type.name
                        }
                else:
                    field_property = {
                        'type': field.type.type,
                        'description': field.description or field.name
                    }
                    if field.type.max_length and field.type.max_length > 0:
                        field_property['maxLength'] = field.type.max_length
            else:
                field_property = {
                    'type': field.type.type,
                    'description': field.description or field.name
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

            data_properties[field.name] = field_property
            
            if field.required:
                required_fields.append(field.name)

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

    # Create messages for payloads that aren't linked to events (orphaned payloads)
    for payload in Payload.objects.all():
        # Check if this payload is already linked to an event
        is_linked = False
        for event in service.consumes.all().union(service.publishes.all()):
            if (event.payload and event.payload.id == payload.id) or \
               (event.response_payload and event.response_payload.id == payload.id):
                is_linked = True
                break
        
        # If not linked, create a message for it
        if not is_linked:
            message_name = payload.name
            title = ' '.join(word.capitalize() for word in re.findall(r'[A-Z][a-z]*', message_name))
            
            # Generate appropriate summary for orphaned messages
            if message_name.endswith('Response'):
                # For response messages, use a simple response format with proper capitalization
                base_name = message_name.replace('Response', '')
                # Convert PascalCase to proper case
                base_name_proper = ' '.join(re.findall(r'[A-Z][a-z]*', base_name)).lower()
                summary = f"{base_name_proper} response"
            else:
                # For regular messages, use the payload description if available
                summary = payload.description if payload.description else title.lower()
            
            configuration['components']['messages'][message_name] = {
                'name': message_name,
                'title': title,
                'summary': summary,
                'contentType': 'application/json',
                'payload': {
                    '$ref': "#/components/schemas/{0}Payload".format(payload.name)
                }
            }

    for dbpayload in DatabasePayload.objects.all():
        properties = {}

        for field in dbpayload.databasefield_set.all():
            if field.type.custom_type:
                if field.type.enum_choices is not None:
                    enum_choices = field.type.enum_choices.replace(" ", "").split(",")
                    properties[field.name] = {
                        '$ref': "#/components/schemas/{0}".format(field.type.name)
                    }
                    configuration['components']['schemas'][field.type.name] = {
                        'type': field.type.type,
                        'enum': enum_choices,
                        'x-parser-schema-id': field.type.name
                    }
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


@csrf_exempt
@require_http_methods(["POST"])
def import_yaml(request):
    """Import AsyncAPI YAML and create all corresponding objects"""
    try:
        yaml_content = request.POST.get('yaml_content', '')
        if not yaml_content:
            messages.error(request, 'No YAML content provided')
            return redirect('admin:index')
        
        # Parse YAML
        try:
            yaml_data = yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            messages.error(request, f'Invalid YAML: {str(e)}')
            return redirect('admin:index')
        
        # Extract basic info
        info = yaml_data.get('info', {})
        project_name = info.get('title', 'Imported Project').split(' ')[0]  # Extract project name
        service_name = info.get('title', 'Imported Service').split(' ')[-2] if len(info.get('title', '').split(' ')) > 1 else 'Imported Service'
        
        # Create or get project
        project_slug = re.sub(r'[^a-zA-Z0-9]', '', project_name.lower())[:20]
        project, created = Project.objects.get_or_create(
            slug_name=project_slug,
            defaults={'name': project_name}
        )
        
        # Create or get service
        service_slug = re.sub(r'[^a-zA-Z0-9]', '', service_name.lower())[:200]
        service, created = Service.objects.get_or_create(
            project=project,
            slug_name=service_slug,
            defaults={
                'name': service_name,
                'asyncapi_version': yaml_data.get('asyncapi', '3.0.0'),
                'version': info.get('version', '1.0.0'),
                'description': info.get('description', 'Imported service')
            }
        )
        
        # Create domain
        domain, created = Domain.objects.get_or_create(
            project=project,
            name='default'
        )
        
        # Create event type
        event_type, created = EventType.objects.get_or_create(name='event')
        
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
            event, created = Event.objects.get_or_create(
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
                event_name = channel_name.replace('Response', '')
                event_snake_name = re.sub('([A-Z]+)', r'_\1', event_name).lower().strip('_')
                
                try:
                    event = Event.objects.get(domain=domain, name=event_snake_name)
                    
                    # Store endpoint if provided
                    if endpoint and action == 'receive':
                        event.endpoint = endpoint
                        event.save()
                    
                    if action == 'receive':
                        service.consumes.add(event)
                    elif action == 'send':
                        service.publishes.add(event)
                except Event.DoesNotExist:
                    continue
        
        # Process schemas to create field types and fields
        created_field_types = []
        created_fields = []
        created_db_payloads = []
        created_db_fields = []
        
        for schema_name, schema_data in schemas.items():
            if schema_name.startswith('Data_'):
                # This is a data schema, find corresponding payload
                payload_name = schema_name.replace('Data_', '').replace('Payload', '')
                try:
                    payload = Payload.objects.get(project=project, name=payload_name)
                    
                    # Process properties
                    properties = schema_data.get('properties', {})
                    required_fields = schema_data.get('required', [])
                    
                    for field_name, field_data in properties.items():
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
                        field, created = Field.objects.get_or_create(
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
                        if created:
                            created_fields.append(field)
                        
                except Payload.DoesNotExist:
                    continue
            
            elif schema_name.startswith('DB_'):
                # This is a database schema
                db_payload_name = schema_name.replace('DB_', '')
                db_payload, created = DatabasePayload.objects.get_or_create(
                    project=project,
                    name=db_payload_name,
                    defaults={
                        'name': db_payload_name,
                        'create_rest': schema_data.get('x-create-rest', False),
                        'x_parser_schema_id': schema_data.get('x-parser-schema-id'),
                        'x_derives_from': schema_data.get('x-derives-from')
                    }
                )
                if created:
                    created_db_payloads.append(db_payload)
                
                # Process properties
                properties = schema_data.get('properties', {})
                required_fields = schema_data.get('required', [])
                
                for field_name, field_data in properties.items():
                    # Create or get field type with comprehensive handling
                    field_type = create_or_get_field_type(project, field_data, created_field_types)
                    
                    # Create database field with all attributes
                    db_field, created = DatabaseField.objects.get_or_create(
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
                    if created:
                        created_db_fields.append(db_field)
                
                # Add to service
                service.database_payloads.add(db_payload)
            
            elif schema_name.endswith('Payload'):
                # This is a main payload schema - we already handled these above
                continue
            
            else:
                # This might be a standalone field type (enum, custom type, etc.)
                if 'enum' in schema_data:
                    # This is an enum type
                    field_type, created = FieldType.objects.get_or_create(
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
                    if created:
                        created_field_types.append(field_type)
                
                elif schema_data.get('type') in ['string', 'number', 'integer', 'boolean', 'array', 'object']:
                    # This is a basic field type
                    field_type, created = FieldType.objects.get_or_create(
                        project=project,
                        name=schema_name,   
                        defaults={
                            'type': schema_data.get('type', 'string'),
                            'custom_type': True,
                            'format': schema_data.get('format'),
                            'max_length': schema_data.get('maxLength')
                        }
                    )
                    if created:
                        created_field_types.append(field_type)
        
        # Add success message
        success_message = f'Successfully imported YAML! Created: {len(created_events)} events, {len(created_payloads)} payloads, {len(created_field_types)} field types, {len(created_fields)} fields, {len(created_db_payloads)} database payloads, {len(created_db_fields)} database fields.'
        messages.success(request, success_message)
        
        # Redirect to admin page
        return redirect('admin:index')
        
    except Exception as e:
        # Add error message
        error_message = f'Import failed: {str(e)}'
        messages.error(request, error_message)
        
        # Redirect to admin page
        return redirect('admin:index')
