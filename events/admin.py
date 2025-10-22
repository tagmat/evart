from django.contrib import admin
from django.urls import reverse
from django.utils.safestring import mark_safe
from nested_admin.nested import NestedStackedInline, NestedModelAdmin, NestedTabularInline

from events.models import *


class EventAdmin(admin.ModelAdmin):
    list_display = ["name", "type", "domain", "payload", "response_payload", "is_sync", "is_post", "address"]
    list_filter = ["domain", "type", "payload", "response_payload", "is_sync", "is_post", "address"]


class EventInline(admin.TabularInline):
    model = Event


# class GrpcServiceInline(NestedTabularInline):
#     model = GrpcService


# class GrpcPackageAdmin(admin.ModelAdmin):
#     list_display = ["name", "service"]
#     inlines = [GrpcServiceInline, ]
#
#
# class GrpcPackageInline(NestedStackedInline):
#     model = GrpcPackage
#     inlines = [GrpcServiceInline, ]


class ConsumesInline(NestedStackedInline):
    model = Service.consumes.through
    # inlines = [EventInline, ]


class PublishesInline(NestedStackedInline):
    model = Service.publishes.through
    # inlines = [EventInline, ]


class FieldInline(admin.TabularInline):
    model = Field


class DatabaseFieldInline(admin.TabularInline):
    model = DatabaseField


class DomainAdmin(admin.ModelAdmin):
    list_display = ["name"]
    inlines = [EventInline, ]


class PayloadAdmin(admin.ModelAdmin):
    list_display = ["name"]
    inlines = [FieldInline, ]
    # list_filter = ["grpc_request_payload", "grpc_response_payload"]


class DatabasePayloadAdmin(admin.ModelAdmin):
    list_display = ["name"]
    inlines = [DatabaseFieldInline, ]


class ServiceAdmin(NestedModelAdmin):
    list_display = ["name", "download_yaml_url",
                    # "download_proto_url"
                    ]
    # inlines = [GrpcPackageInline, ]
    filter_horizontal = ["consumes", "publishes", "database_payloads"]

    @admin.display(description="Download YAML")
    def download_yaml_url(self, obj):
        return mark_safe("<a href=\"{0}\">Download YAML</a>".format(reverse("download-yaml", args=(obj.id,))))

    # @admin.display(description="Download Proto")
    # def download_proto_url(self, obj):
    #     return mark_safe("<a href=\"{0}\">Download Proto</a>".format(reverse("download-proto", args=(obj.id,))))


admin.site.register(Event, EventAdmin)
admin.site.register(Domain, DomainAdmin)
admin.site.register(Payload, PayloadAdmin)
admin.site.register(Field)
admin.site.register(FieldType)
admin.site.register(Service, ServiceAdmin)
admin.site.register(Project)
admin.site.register(EventType)
admin.site.register(DatabasePayload, DatabasePayloadAdmin)
admin.site.register(DatabaseField)
# admin.site.register(GrpcPackage, GrpcPackageAdmin)
# admin.site.register(GrpcService)
