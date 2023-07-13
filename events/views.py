import yaml
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.shortcuts import HttpResponse

from events.models import *


@staff_member_required
def generate_full_proto(request, service_id):
    service = Service.objects.get(id=service_id)

    proto_file = 'syntax = "proto3";\n\n'

    proto_file += 'package {0}_{1};\n\n'.format(service.project.slug_name, service.slug_name)

    proto_file += 'service {0} {{\n'.format(service.grpcpackage.service_name)

    has_empty = False
    for grpcservice in service.grpcpackage.grpcservice_set.all():
        if grpcservice.request is None:
            has_empty = True
            request = 'Empty'
        else:
            request = grpcservice.request.name

        if grpcservice.response.name is None:
            has_empty = True
            response = 'Empty'
        else:
            response = grpcservice.response.name
        proto_file += '    rpc {0} ({1}) returns ({2}){{}}\n'.format(grpcservice.name, request, response)
    proto_file += '}\n\n'

    if has_empty:
        proto_file += 'message Empty {}\n\n'

    for message in Payload.objects.filter(Q(grpc_request_payload__in=service.grpcpackage.grpcservice_set.all()) | Q(
            grpc_response_payload__in=service.grpcpackage.grpcservice_set.all())).distinct():
        proto_file += 'message {0} {{\n'.format(message.name)
        field_id = 1
        enum_to_append = ''
        for field in message.field_set.all():
            if field.type.custom_type:
                if field.type.enum_choices is not None:
                    enum_choices = field.type.enum_choices.replace(" ", "").split(",")
                    proto_file += '    {3}{0} {1} = {2};\n'.format(field.type.name, field.name, field_id,
                                                                   '' if field.required else 'optional ')
                    enum_to_append += 'enum {0} {{\n'.format(field.type.name)
                    enum_id = 0
                    for enum_choice in enum_choices:
                        enum_to_append += '    {0} = {1};\n'.format(enum_choice.upper(), enum_id)
                        enum_id += 1
                    enum_to_append += '}\n\n'
            else:
                proto_file += '    {3}{0} {1} = {2};\n'.format(field.type.protobuf_type, field.name, field_id,
                                                               '' if field.required else 'optional ')

            field_id += 1
        proto_file += '}\n\n'
        proto_file += enum_to_append

    response = HttpResponse(proto_file, content_type="text/plain", )
    response['Content-Disposition'] = 'attachment; filename="{0}_{1}_{2}.proto"'.format(service.project.slug_name,
                                                                                        service.slug_name,
                                                                                        service.grpcpackage.name)
    return response


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
        channel_key = "{0}.{1}".format(service.project.slug_name, event.slug_name(with_params=True))
        configuration['channels'][channel_key] = {
            'description': "{0} {1} channel".format(event.domain.project.name, event.name),
        }

        if event in service.consumes.all():
            configuration['channels'][channel_key]['publish'] = {
                'summary': "{0}".format(event.name),
                'operationId': "{1}{0}".format(event.pascal_name(), 'publish'),
                # 'traits': "", #$ref: '#/components/operationTraits/kafka'
                'message': {
                    '$ref': '#/components/messages/{0}'.format(event.pascal_name())
                }
            }
            if event.response_payload is not None:
                channel_key = "{0}.{1}".format(service.project.slug_name,
                                               event.slug_name(with_params=True, response=True))
                configuration['channels'][channel_key] = {
                    'description': "{0} {1} channel".format(event.domain.project.name, event.name),
                    'x-response-of': "{0}.{1}".format(service.project.slug_name, event.slug_name(with_params=True))
                }
                configuration['channels'][channel_key]['subscribe'] = {
                    'summary': "{0}".format(event.name),
                    'operationId': "{1}{0}".format(event.pascal_name(response=True), 'subscribe'),
                    # 'traits': "", #$ref: '#/components/operationTraits/kafka'
                    'message': {
                        '$ref': '#/components/messages/{0}'.format(event.pascal_name(response=True))
                    }
                }
                configuration['components']['messages'][event.pascal_name(response=True)] = {
                    'name': "{0}".format(event.pascal_name(response=True)),
                    'title': "{0}".format(event.pascal_name(response=True)),
                    'summary': "Summary for {0} event response".format(event.name),
                    'payload': {
                        'type': 'object',
                        'properties': {
                            'eventName': {
                                'type': 'string',
                                'default': event.pascal_name(response=True),
                                'description': 'Name of the event response',
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
                                'default': 3600,
                                'description': 'Time to live for the message',
                                'x-parser-schema-id': 'timeToLive'
                            }
                            # 'payload': {
                            #     '$ref': '#/components/schemas/{0}'.format(event.pascal_name(response=True))
                            # }
                        }
                    }
                }
        if event in service.publishes.all():
            configuration['channels'][channel_key]['subscribe'] = {
                'summary': "{0}".format(event.name),
                'operationId': "{1}{0}".format(event.pascal_name(response=True), 'subscribe'),
                # 'traits': "", #$ref: '#/components/operationTraits/kafka'
                'message': {
                    '$ref': '#/components/messages/{0}'.format(event.pascal_name())
                }
            }
            if event.response_payload is not None:
                # append response message to event
                configuration['channels'][channel_key]['subscribe']['x-response-message'] = {
                    '$ref': '#/components/messages/{0}'.format(event.pascal_name(response=True))
                }
                configuration['channels'][channel_key]['subscribe']['x-response-channel'] = "{0}.{1}".format(
                    service.project.slug_name,
                    event.slug_name(with_params=True, response=True))
                # change channel key to response channel
                channel_key = "{0}.{1}".format(service.project.slug_name,
                                               event.slug_name(with_params=True, response=True))
                configuration['channels'][channel_key] = {
                    'description': "{0} {1} channel".format(event.domain.project.name, event.name),
                    'x-response-of-channel': "{0}.{1}".format(service.project.slug_name,
                                                              event.slug_name(with_params=True))
                }
                configuration['channels'][channel_key]['publish'] = {
                    'summary': "{0}".format(event.name),
                    'operationId': "{1}{0}".format(event.pascal_name(response=True), 'publish'),
                    # 'traits': "", #$ref: '#/components/operationTraits/kafka'
                    'message': {
                        '$ref': '#/components/messages/{0}'.format(event.pascal_name(response=True))
                    }
                }
                configuration['components']['messages'][event.pascal_name(response=True)] = {
                    'name': "{0}".format(event.pascal_name(response=True)),
                    'title': "{0}".format(event.pascal_name(response=True)),
                    'summary': "Summary for {0} event response".format(event.name),
                    'payload': {
                        'type': 'object',
                        'properties': {
                            'eventName': {
                                'type': 'string',
                                'default': event.pascal_name(response=True),
                                'description': 'Name of the event response',
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
                                'default': 3600,
                                'description': 'Time to live for the message',
                                'x-parser-schema-id': 'timeToLive'
                            }
                            # 'payload': {
                            #     '$ref': '#/components/schemas/{0}'.format(event.pascal_name(response=True))
                            # }
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
                    'from': {
                        'type': 'string',
                        'default': '',
                        'description': 'ServiceID of the service that sent the event',
                        'x-parser-schema-id': 'from'
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
        if event.response_payload is not None:
            configuration['components']['messages'][event.pascal_name(response=True)]['payload']['properties'][
                'data'] = {
                '$ref': '#/components/schemas/{0}'.format(event.response_payload.name)
            }

    for payload in Payload.objects.filter(
            Q(event__in=service.consumes.all()) |
            Q(event__in=service.publishes.all()) |
            Q(response_of_event__in=service.publishes.all())
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

        configuration['components']['schemas'][payload.name] = {
            'type': 'object',
            'properties': properties,
            'required': required,
            'x-parser-schema-id': payload.name
        }

    yaml_out = yaml.dump(configuration, sort_keys=False)

    response = HttpResponse(yaml_out, content_type='application/x-yaml')
    response['Content-Disposition'] = 'attachment; filename={0}.yaml'.format(service.slug_name)

    return response
