import yaml
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.shortcuts import HttpResponse

from events.models import *


@staff_member_required
def generate_full_yaml(request, service_id):
    service = Service.objects.get(id=service_id)

    configuration = {
        'asyncapi': service.asyncapi_version,
        'info': {
            'title': "{0} {1} Service API".format(service.project.name, service.name),
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
        channel_key = "{0}.{1}".format(service.project.slug_name, event.slug_name(with_params=True))
        configuration['channels'][event.slug_name(with_params=True)] = {
            'description': "{0} {1} channel".format(event.domain.project.name, event.name),
            'summary': "{0}".format(event.name),
            'address': channel_key,
            'messages': ({
                '{0}'.format(event.pascal_name()): {
                    '$ref': '#/components/messages/{0}'.format(event.pascal_name())
                }
            }) if not event.is_sync else ({
                '{0}'.format(event.pascal_name()): {
                    '$ref': '#/components/messages/{0}'.format(event.pascal_name())
                },
                '{0}Response'.format(event.pascal_name()): {
                    '$ref': '#/components/messages/{0}Response'.format(event.pascal_name())
                }
            }),
            'x-is-sync': event.is_sync,
        }
        if event in service.consumes.all():
            configuration['operations']['receive{0}'.format(channel_key)] = {
                'action': 'receive',
                'channel': {
                    '$ref': '#/channels/{0}'.format(event.slug_name(with_params=True))
                },
                'messages': [
                    {
                        '$ref': '#/channels/{0}/messages/{1}'.format(event.slug_name(with_params=True),
                                                                     event.pascal_name())
                    }
                ],
                'x-operation-name': "{0}".format(event.camel_name()),
                'x-endpoint': event.endpoint,
            }
        if event in service.publishes.all():
            configuration['operations']['send{0}'.format(channel_key)] = {
                'action': 'send',
                'channel': {
                    '$ref': '#/channels/{0}'.format(event.slug_name(with_params=True))
                },
                'messages': [
                    {
                        '$ref': '#/channels/{0}/messages/{1}'.format(event.slug_name(with_params=True),
                                                                     event.pascal_name())
                    }
                ],
                'x-operation-name': "{0}".format(event.camel_name()),
            }

        configuration['components']['messages'][event.pascal_name()] = {
            'name': "{0}".format(event.pascal_name()),
            'title': "{0}".format(event.pascal_name()),
            'summary': "Summary for {0} event".format(event.name),
            'contentType': 'application/json',
            'payload': {
                '$ref': '#/components/schemas/{0}{1}'.format(event.pascal_name(), 'Payload')
            },
        }

        if event.is_sync:
            configuration['components']['messages']['{0}Response'.format(event.pascal_name())] = {
                'name': "{0}Response".format(event.pascal_name()),
                'title': "{0}Response".format(event.pascal_name()),
                'summary': "Summary for {0} event response".format(event.name),
                'contentType': 'application/json',
                'payload': {
                    '$ref': '#/components/schemas/{0}{1}'.format(event.pascal_name(), 'ResponsePayload')
                },
            }

        configuration['components']['schemas']['{0}{1}'.format(event.pascal_name(), 'Payload')] = {
            'type': 'object',
            'properties': {
                'eventName': {
                    'type': 'string',
                    'default': event.pascal_name(),
                    'description': 'Name of the event',
                    'x-parser-schema-id': 'eventName'
                },
                'fromService': {
                    'type': 'string',
                    'default': '',
                    'description': 'ServiceID of the service that sent the event',
                    'x-parser-schema-id': 'fromService'
                },
                'sentAt': {
                    'type': 'string',
                    'format': 'date-time',
                    'default': 'now()',
                    'description': 'Date and time when the message was sent',
                    'x-parser-schema-id': 'sentAt'
                },
                'timeToLive': {
                    'type': 'integer',
                    'default': 3600000,
                    'description': 'Message time to live in milliseconds',
                    'x-parser-schema-id': 'timeToLive'
                },
            }
        }

        if event.payload is not None:
            configuration['components']['schemas']['{0}{1}'.format(event.pascal_name(), 'Payload')]['properties'][
                'data'] = {
                '$ref': '#/components/schemas/{1}{0}'.format(event.payload.name, "Data_")
            }

        if event.is_sync and event.response_payload is not None:
            configuration['components']['schemas']['{0}{1}'.format(event.pascal_name(), 'ResponsePayload')] = {
                'type': 'object',
                'properties': {
                    'eventName': {
                        'type': 'string',
                        'default': event.pascal_name(),
                        'description': 'Name of the event',
                        'x-parser-schema-id': 'eventName'
                    },
                    'fromService': {
                        'type': 'string',
                        'default': '',
                        'description': 'ServiceID of the service that sent the event',
                        'x-parser-schema-id': 'fromService'
                    },
                    'sentAt': {
                        'type': 'string',
                        'format': 'date-time',
                        'default': 'now()',
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
                        '$ref': '#/components/schemas/{1}{0}'.format(event.response_payload.name, "Data_")
                    }
                }
            }

    for payload in Payload.objects.filter(
            Q(event__in=service.consumes.all()) |
            Q(event__in=service.publishes.all()) |
            Q(response_of_event__in=service.publishes.all()) |
            Q(response_of_event__in=service.consumes.all())
    ):
        properties = {}
        required = []
        for field in payload.field_set.all():
            if field.type.custom_type:
                if field.type.enum_choices is not None:
                    enum_choices = field.type.enum_choices.replace(" ", "").split(",")
                    properties[field.name] = {
                        '$ref': '#/components/schemas/{0}'.format(field.type.name)
                    }
                    configuration['components']['schemas'][field.type.name] = {
                        'type': field.type.type,
                        'enum': enum_choices,
                        'x-parser-schema-id': field.type.name
                    }
                else:
                    if field.type.type == "string":
                        properties[field.name] = {
                            '$ref': '#/components/schemas/{0}'.format(field.type.name)
                        }
                        configuration['components']['schemas'][field.type.name] = {
                            'type': field.type.type,
                            'x-parser-schema-id': field.type.name
                        }
                        if field.type.max_length > 0:
                            configuration['components']['schemas'][field.type.name]['maxLength'] = field.type.max_length

            else:
                properties[field.name] = {
                    'type': field.type.type,
                    # 'format': field.type.format
                }

                if field.description is not None:
                    properties[field.name]['description'] = field.description

                if field.type.format is not None:
                    properties[field.name]['format'] = field.type.format

            if field.required:
                required.append(field.name)

        configuration['components']['schemas']["{1}{0}".format(payload.name, "Data_")] = {
            'type': 'object',
            'properties': properties,
            'required': required,
            'x-parser-schema-id': payload.name
        }

    for dbpayload in DatabasePayload.objects.all():
        properties = {}

        for field in dbpayload.databasefield_set.all():
            if field.type.custom_type:
                if field.type.enum_choices is not None:
                    enum_choices = field.type.enum_choices.replace(" ", "").split(",")
                    properties[field.name] = {
                        '$ref': '#/components/schemas/{0}'.format(field.type.name)
                    }
                    configuration['components']['schemas'][field.type.name] = {
                        'type': field.type.type,
                        'enum': enum_choices,
                        'x-parser-schema-id': field.type.name
                    }
                else:
                    if field.type.type == "string":
                        properties[field.name] = {
                            '$ref': '#/components/schemas/{0}'.format(field.type.name)
                        }
                        configuration['components']['schemas'][field.type.name] = {
                            'type': field.type.type,
                            'x-parser-schema-id': field.type.name
                        }
                        if field.type.max_length > 0:
                            configuration['components']['schemas'][field.type.name]['maxLength'] = field.type.max_length

            else:
                properties[field.name] = {
                    'type': field.type.type,
                    # 'format': field.type.format
                }

                if field.description is not None:
                    properties[field.name]['description'] = field.description

                if field.type.format is not None:
                    properties[field.name]['format'] = field.type.format

        configuration['components']['schemas']["{1}{0}".format(dbpayload.name, "DB_")] = {
            'type': 'object',
            'properties': properties,
            'x-parser-schema-id': dbpayload.name
        }

    yaml_out = yaml.dump(configuration, sort_keys=False)

    response = HttpResponse(yaml_out, content_type='application/x-yaml')
    response['Content-Disposition'] = 'attachment; filename={0}.yaml'.format(service.slug_name)

    return response
