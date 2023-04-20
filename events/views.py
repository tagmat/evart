from django.shortcuts import HttpResponse
from django.contrib.admin.views.decorators import staff_member_required
from events.models import *
from django.db.models import Q
import yaml


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
        'servers': {},
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
                    # 'operationId':"",
                    # 'traits': "", #$ref: '#/components/operationTraits/kafka'
                    'message': {
                        '$ref': '#/components/messages/{0}'.format(event.name)
                    }
                }
        }

        configuration['components']['messages'][event.name] = {
            'name': "{0} event".format(event.name),
            'title': "{0}".format(event.name),
            'summary': "Summary for {0} event".format(event.name),
            'payload': {
                '$ref': '#/components/schemas/{0}'.format(event.payload.name)
            }
            # 'traits':{},

        }

    for payload in Payload.objects.filter(Q(event__in=service.consumes.all()) | Q(event__in=service.publishes.all())):
        properties = {}
        for field in payload.field_set.all():
            if field.type.custom_type:
                if field.type.enum_choices is not None:
                    enum_choices = field.type.enum_choices.replace(" ", "").split(",")
                    properties[field.name] = {
                        'type': field.type.type,
                        'enum': enum_choices
                    }
            else:
                properties[field.name] = {
                    'type': field.type.type,
                    # 'format': field.type.format
                    # 'description': field.description
                }

                if field.type.format is not None:
                    properties[field.name]['format'] = field.type.format

        configuration['components']['schemas'][payload.name] = {
            'type': 'object',
            'properties': properties

        }

    yaml_out = yaml.dump(configuration, sort_keys=False)
    return HttpResponse(yaml_out)
