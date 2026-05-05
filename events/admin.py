from django.contrib import admin
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.db import connection
from django.db.models import Q
from django.db.utils import OperationalError
from nested_admin.nested import NestedStackedInline, NestedModelAdmin, NestedTabularInline

from events.models import *


def service_column_exists():
    """Check if service_id column exists in DatabasePayload table"""
    try:
        with connection.cursor() as cursor:
            if 'sqlite' in connection.vendor:
                cursor.execute("PRAGMA table_info(events_databasepayload)")
                columns = [row[1] for row in cursor.fetchall()]
                return 'service_id' in columns
            elif 'postgresql' in connection.vendor:
                cursor.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'events_databasepayload' AND column_name = 'service_id'"
                )
                return cursor.fetchone() is not None
            else:
                # For other databases, try to query and catch error
                try:
                    DatabasePayload.objects.first()
                    return True
                except OperationalError:
                    return False
    except Exception:
        return False


class DbOperationInline(admin.StackedInline):
    model = DbOperation
    extra = 0
    can_delete = True


class EventAdmin(admin.ModelAdmin):
    list_display = ["name", "type", "domain", "payload", "response_payload", "is_sync", "http_method", "is_jwt", "address"]
    list_filter = ["domain", "type", "payload", "response_payload", "is_sync", "http_method", "is_jwt", "address"]
    inlines = [DbOperationInline]


class EventInline(admin.TabularInline):
    model = Event
    extra = 0
    show_change_link = True
    # Limit the number of inline items shown to prevent too many fields
    # Users can use "Show all" or pagination if needed
    can_delete = True


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


class HTTPClientFieldInline(admin.TabularInline):
    model = HTTPClientField
    extra = 1
    fields = ['name']


class HTTPClientInline(admin.TabularInline):
    model = HTTPClient
    extra = 0
    fields = ['name']
    show_change_link = True


class GRPCMethodInline(admin.TabularInline):
    model = GRPCMethod
    extra = 0
    fields = ['name', 'request', 'response', 'input_field', 'output_field']


class GRPCClientInline(admin.TabularInline):
    model = GRPCClient
    extra = 0
    fields = ['name', 'module', 'proto_service']
    show_change_link = True


class FieldInline(admin.TabularInline):
    model = Field


class DatabaseFieldInline(admin.TabularInline):
    model = DatabaseField
    fields = [
        'name', 'type', 'description', 'required',
        'x_type', 'x_type_override', 'x_size',
        'x_unique', 'x_unique_index', 'x_index', 'x_not_null', 'x_nullable',
        'x_check', 'x_column', 'x_comment', 'x_serializer',
        'x_ignore', 'x_precision', 'x_scale',
        'x_auto_create_time', 'x_auto_update_time', 'x_auto_increment',
        'default_value',
        'x_relation_schema_id', 'x_foreign_key', 'x_references',
        'x_cascade_update', 'x_cascade_delete',
        'x_many_to_many', 'x_join_table', 'x_join_foreign_key', 'x_join_references',
        'x_association_autocreate', 'x_association_autoupdate', 'x_association_save_reference',
        'x_embedded', 'x_embedded_prefix',
        'x_polymorphic', 'x_polymorphic_value', 'x_association_foreign_key', 'x_constraint',
        'x_preload', 'x_primary_key',
    ]


class DatabasePayloadInline(NestedTabularInline):
    model = DatabasePayload
    extra = 0
    fields = ['name', 'service', 'description', 'create_rest', 'x_parser_schema_id', 'x_derives_from']
    readonly_fields = ['service']  # Service is automatically set to parent Service, show as read-only
    show_change_link = True
    
    def get_fields(self, request, obj=None):
        """Conditionally show service field only if column exists"""
        fields = list(super().get_fields(request, obj))
        if not service_column_exists():
            # Remove service field if column doesn't exist
            fields = [f for f in fields if f != 'service']
            # Update readonly_fields
            self.readonly_fields = [f for f in self.readonly_fields if f != 'service']
        return fields
    
    def get_queryset(self, request):
        """Filter to show only database payloads for this service"""
        try:
            qs = super().get_queryset(request)
            if service_column_exists():
                # The parent object (Service) will automatically filter via ForeignKey
                return qs.select_related('service', 'project')
            else:
                # If column doesn't exist, return empty queryset to avoid errors
                return DatabasePayload.objects.none()
        except OperationalError:
            # Return empty queryset if there's a schema error
            return DatabasePayload.objects.none()


class DomainAdmin(admin.ModelAdmin):
    list_display = ["name"]
    inlines = [EventInline, ]


class PayloadDomainFilter(admin.SimpleListFilter):
    title = "Domain"
    parameter_name = "domain"
    
    def lookups(self, request, model_admin):
        return Domain.objects.order_by("name").values_list("id", "name")
    
    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        return queryset.filter(
            Q(event__domain_id=self.value())
            | Q(response_of_event__domain_id=self.value())
        ).distinct()


class PayloadAdmin(admin.ModelAdmin):
    list_display = ["name", "domain_list"]
    list_filter = [PayloadDomainFilter]
    inlines = [FieldInline, ]
    # list_filter = ["grpc_request_payload", "grpc_response_payload"]
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related("event_set__domain", "response_of_event__domain")
    
    @admin.display(description="Domains", empty_value="-")
    def domain_list(self, obj):
        domains = {
            event.domain.name
            for event in obj.event_set.all()
            if event.domain_id and event.domain
        }
        domains.update(
            event.domain.name
            for event in obj.response_of_event.all()
            if event.domain_id and event.domain
        )
        if not domains:
            return None
        return ", ".join(sorted(domains))


class FieldTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "type", "x_type", "custom_type", "format", "max_length"]
    list_filter = ["custom_type", "type", "format"]
    search_fields = ["name", "type", "x_type"]
    fields = [
        "project",
        "name",
        "type",
        "x_type",
        "custom_type",
        "enum_choices",
        "format",
        "max_length",
        "protobuf_type",
        "schema_definition",
    ]


class DatabasePayloadAdmin(admin.ModelAdmin):
    list_display = ["name", "service", "project"]
    list_filter = ["service", "project"]
    inlines = [DatabaseFieldInline, ]
    
    def get_list_display(self, request):
        """Conditionally show service field only if column exists"""
        if not service_column_exists():
            return ["name", "project"]
        return list(super().get_list_display(request))
    
    def get_list_filter(self, request):
        """Conditionally filter by service only if column exists"""
        if not service_column_exists():
            return ["project"]
        return list(super().get_list_filter(request))
    
    def get_queryset(self, request):
        """Handle missing service_id column gracefully"""
        if not service_column_exists():
            # Return empty queryset to prevent errors
            return DatabasePayload.objects.none()
        try:
            qs = super().get_queryset(request)
            return qs.select_related('service', 'project')
        except OperationalError:
            # Return empty queryset if there's a schema error
            return DatabasePayload.objects.none()


class ServiceAdmin(NestedModelAdmin):
    list_display = ["name", "download_yaml_url",
                    # "download_proto_url"
                    ]
    # inlines = [GrpcPackageInline, ]
    inlines = [DatabasePayloadInline, HTTPClientInline, GRPCClientInline]
    filter_horizontal = ["consumes", "publishes", "database_payloads"]
    fields = [
        "project", "name", "slug_name", "asyncapi_version", "version",
        "description", "original_title", "x_general_name", "x_service_name", "x_service_ip", "x_transport",
        "consumes", "publishes", "database_payloads",
    ]
    
    class Media:
        js = ("events/admin/service_copy_paste.js",)
    
    def get_inlines(self, request, obj):
        """Conditionally show DatabasePayloadInline only if column exists"""
        inlines = list(super().get_inlines(request, obj))
        if not service_column_exists():
            # Remove DatabasePayloadInline if column doesn't exist
            inlines = [inline for inline in inlines if inline != DatabasePayloadInline]
        return inlines
    
    def get_queryset(self, request):
        """Override to prefetch related database payloads"""
        try:
            qs = super().get_queryset(request)
            if service_column_exists():
                return qs.prefetch_related('database_payloads')
            else:
                return qs
        except OperationalError:
            return Service.objects.all()

    @admin.display(description="Download YAML")
    def download_yaml_url(self, obj):
        return mark_safe("<a href=\"{0}\">Download YAML</a>".format(reverse("download-yaml", args=(obj.id,))))

    # @admin.display(description="Download Proto")
    # def download_proto_url(self, obj):
    #     return mark_safe("<a href=\"{0}\">Download Proto</a>".format(reverse("download-proto", args=(obj.id,))))


class ProjectAdmin(admin.ModelAdmin):
    """Admin for Project model with graceful handling of missing service_id column"""
    
    def get_deleted_objects(self, objs, request):
        """Override to handle missing service_id column gracefully"""
        # If service_id column doesn't exist, we need to handle deletion differently
        if not service_column_exists():
            # Use a custom collector that skips DatabasePayload
            from django.contrib.admin.utils import NestedObjects
            
            collector = NestedObjects(using='default')
            # Manually collect objects, skipping DatabasePayload relations
            for obj in objs:
                collector.add([obj])  # collector.add expects a list
                # Manually add related objects, but skip DatabasePayload
                for related in obj._meta.related_objects:
                    if related.related_model != DatabasePayload:
                        try:
                            accessor_name = related.get_accessor_name()
                            related_objs = list(getattr(obj, accessor_name).all())
                            if related_objs:
                                collector.add(related_objs)
                        except (OperationalError, AttributeError, TypeError):
                            # Skip if we can't access the relation
                            pass
            
            # Remove DatabasePayload if it somehow got added
            if DatabasePayload in collector.data:
                del collector.data[DatabasePayload]
            
            perms_needed = set()
            model_count = {model._meta.verbose_name_plural: len(instances)
                          for model, instances in collector.data.items()}
            
            return collector.data, model_count, perms_needed
        else:
            # Normal flow - column exists
            try:
                return super().get_deleted_objects(objs, request)
            except OperationalError as e:
                if 'no such column' in str(e).lower() and 'service_id' in str(e):
                    # Fallback to manual collection
                    from django.contrib.admin.utils import NestedObjects
                    
                    collector = NestedObjects(using='default')
                    for obj in objs:
                        collector.add([obj])
                        for related in obj._meta.related_objects:
                            if related.related_model != DatabasePayload:
                                try:
                                    accessor_name = related.get_accessor_name()
                                    related_objs = list(getattr(obj, accessor_name).all())
                                    if related_objs:
                                        collector.add(related_objs)
                                except (OperationalError, AttributeError, TypeError):
                                    pass
                    
                    if DatabasePayload in collector.data:
                        del collector.data[DatabasePayload]
                    
                    perms_needed = set()
                    model_count = {model._meta.verbose_name_plural: len(instances)
                                  for model, instances in collector.data.items()}
                    
                    return collector.data, model_count, perms_needed
                raise


admin.site.register(Event, EventAdmin)
admin.site.register(Domain, DomainAdmin)
admin.site.register(Payload, PayloadAdmin)
admin.site.register(Field)
admin.site.register(FieldType, FieldTypeAdmin)
admin.site.register(Service, ServiceAdmin)
admin.site.register(Project, ProjectAdmin)
admin.site.register(EventType)
admin.site.register(DatabasePayload, DatabasePayloadAdmin)
admin.site.register(DatabaseField)
class GRPCClientAdmin(admin.ModelAdmin):
    list_display = ["name", "service", "module", "proto_service"]
    inlines = [GRPCMethodInline]


admin.site.register(HTTPClient)
admin.site.register(GRPCClient, GRPCClientAdmin)
admin.site.register(GRPCMethod)
admin.site.register(DbOperation)
# admin.site.register(GrpcPackage, GrpcPackageAdmin)
# admin.site.register(GrpcService)
