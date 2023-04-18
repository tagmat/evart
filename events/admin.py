from django.contrib import admin
from events.models import *


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


admin.site.register(Event, EventAdmin)
admin.site.register(Domain, DomainAdmin)
admin.site.register(Payload, PayloadAdmin)
admin.site.register(Field)
admin.site.register(FieldType)
admin.site.register(Service)
admin.site.register(Project)
admin.site.register(EventType)
