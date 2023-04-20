from django.contrib import admin
from events.models import *
from django.urls import reverse
from django.utils.safestring import mark_safe


class EventAdmin(admin.ModelAdmin):
    list_display = ["name", "type", "domain", "payload", "response_payload"]
    list_filter = ["domain"]


class EventInline(admin.TabularInline):
    model = Event


class FieldInline(admin.TabularInline):
    model = Field


class DomainAdmin(admin.ModelAdmin):
    list_display = ["name"]
    inlines = [EventInline, ]


class PayloadAdmin(admin.ModelAdmin):
    list_display = ["name"]
    inlines = [FieldInline, ]


class ServiceAdmin(admin.ModelAdmin):
    list_display = ["name", "download_yaml_url"]

    def download_yaml_url(self, obj):
        return mark_safe("<a href=\"{0}\">Download YAML</a>".format(reverse("download-yaml", args=(obj.id,))))


admin.site.register(Event, EventAdmin)
admin.site.register(Domain, DomainAdmin)
admin.site.register(Payload, PayloadAdmin)
admin.site.register(Field)
admin.site.register(FieldType)
admin.site.register(Service, ServiceAdmin)
admin.site.register(Project)
admin.site.register(EventType)
