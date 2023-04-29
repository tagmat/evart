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
            'description': service.description if service.description is not None else "N/A"
        },
        'servers': {
            'test':
                {
                    'url': "127.0.0.1:9092",
                    'protocol': "kafka-secure",
                    "description": "Test broker",
                    "security": []
                    # {"saslScram": []}
                }
        },
        'defaultContentType': 'application/json',
        'channels': {},
        'components': {
            'messages': {},
            'schemas': {},
        }
    }

    for event in service.consumes.all().union(service.publishes.all()):
        configuration['channels']["{0}.{1}".format(service.project.slug_name, event.slug_name())] = {
            'description': "{0} {1} channel".format(event.domain.project.name, event.name),
            'publish' if event in service.consumes.all() else 'subscribe':
                {
                    'summary': "{0}".format(event.name),
                    'operationId': "{1}{0}".format(event.pascal_name(),
                                                   'publish' if event in service.consumes.all() else 'subscribe'),
                    # 'traits': "", #$ref: '#/components/operationTraits/kafka'
                    'message': {
                        '$ref': '#/components/messages/{0}'.format(event.pascal_name())
                    }
                }
        }

        configuration['components']['messages'][event.pascal_name()] = {
            'name': "{0}".format(event.pascal_name()),
            'title': "{0}".format(event.pascal_name()),
            'summary': "Summary for {0} event".format(event.name),
            'payload': {
                'type': 'object',
                'properties': {
                    'eventName': {
                        'type': 'string',
                        'default': event.pascal_name(),
                        'description': 'Name of the event',
                        'x-parser-schema-id': 'eventName'
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
                    }
                }
            },

            # 'traits':{},
        }
        if event.payload is not None:
            configuration['components']['messages'][event.pascal_name()]['payload']['properties']['data'] = {
                '$ref': '#/components/schemas/{0}'.format(event.payload.name)
            }

    for payload in Payload.objects.filter(Q(event__in=service.consumes.all()) | Q(event__in=service.publishes.all())):
        properties = {}
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
                properties[field.name] = {
                    'type': field.type.type,
                    'x-parser-schema-id': field.type.name,
                    # 'format': field.type.format
                    # 'description': field.description
                }

                if field.type.format is not None:
                    properties[field.name]['format'] = field.type.format

        configuration['components']['schemas'][payload.name] = {
            'type': 'object',
            'properties': properties,
            'x-parser-schema-id': payload.name
        }

    yaml_out = yaml.dump(configuration, sort_keys=False)

    response = HttpResponse(yaml_out, content_type='application/x-yaml')
    response['Content-Disposition'] = 'attachment; filename={0}.yaml'.format(service.slug_name)

    return response
