import re
import json

from django.db import models
from django.core.exceptions import ValidationError


class EventType(models.Model):
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name


class Event(models.Model):
    name = models.CharField(max_length=200, help_text="You can use [paramName] in name to pass parameters to event.")
    domain = models.ForeignKey("Domain", on_delete=models.CASCADE)
    payload = models.ForeignKey("Payload", null=True, blank=True, on_delete=models.SET_NULL)
    response_payload = models.ForeignKey("Payload", null=True, blank=True, on_delete=models.SET_NULL,
                                         related_name="response_of_event")
    type = models.ForeignKey("EventType", default=1, on_delete=models.CASCADE)
    is_sync = models.BooleanField(default=True)
    is_post = models.BooleanField(default=False, help_text="Whether this endpoint should use POST method")
    address = models.CharField(max_length=200, blank=True, null=True, help_text="Resource address for grouping channels (e.g., 'invoices', 'user')")
    endpoint = models.CharField(max_length=200, blank=True, null=True)
    description = models.TextField(max_length=500, blank=True, null=True)
    summary = models.TextField(max_length=500, blank=True, null=True)

    def __str__(self):
        return "{0}/{1} [{2}]".format(self.domain.name, self.name, self.type.name)

    def pascal_name(self, with_params=False, response=False):
        name = self.name if with_params else re.sub(r"\[(.*?)\]", "",
                                                    re.sub(r"\.\[(.*?)\]", "", self.name))
        return "%s%s%s" % (name[0].upper(), name[1:], 'Response' if response else '')

    def camel_name(self, with_params=False, response=False):
        name = self.name if with_params else re.sub(r"\[(.*?)\]", "",
                                                    re.sub(r"\.\[(.*?)\]", "", self.name))
        return "%s%s%s" % (name[0].lower(), name[1:], 'Response' if response else '')

    def slug_name(self, with_params=False, response=False):
        return "{0}.{2}.{1}".format(
            self.domain.name.replace("/", "_"),
            self.name if with_params else re.sub(r"\[(.*?)\]", "",
                                                 re.sub(r"\.\[(.*?)\]", "", self.name)),
            self.type.name.lower() if not response else "response")

    def clean(self):
        """Validate that sync events must have a response_payload set"""
        if self.is_sync and not self.response_payload:
            raise ValidationError({
                'response_payload': 'Sync events must have a response payload set.'
            })


class Domain(models.Model):
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name


class Payload(models.Model):
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    description = models.TextField(max_length=500, blank=True, null=True)

    def __str__(self):
        return self.name


class DatabasePayload(models.Model):
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    create_rest = models.BooleanField(default=False)
    
    # Database-specific properties
    x_parser_schema_id = models.CharField(max_length=200, blank=True, null=True, help_text="Parser schema ID")
    x_derives_from = models.CharField(max_length=200, blank=True, null=True, help_text="Schema this derives from")

    def __str__(self):
        return self.name


class Field(models.Model):
    name = models.CharField(max_length=200)
    type = models.ForeignKey("FieldType", on_delete=models.RESTRICT)
    payload = models.ForeignKey("Payload", on_delete=models.CASCADE)
    required = models.BooleanField(default=False)
    minimum = models.IntegerField(blank=True, null=True)
    maximum = models.IntegerField(blank=True, null=True)
    description = models.CharField(max_length=200, blank=True, null=True)
    # Additional fields for complex types
    array_items_type = models.CharField(max_length=200, blank=True, null=True, help_text="Type of array items")
    array_items_ref = models.CharField(max_length=200, blank=True, null=True, help_text="Schema reference for array items")
    schema_ref = models.CharField(max_length=200, blank=True, null=True, help_text="Schema reference for complex types")

    def __str__(self):
        return self.name


class DatabaseField(models.Model):
    name = models.CharField(max_length=200)
    type = models.ForeignKey("FieldType", on_delete=models.RESTRICT)
    payload = models.ForeignKey("DatabasePayload", on_delete=models.CASCADE)
    required = models.BooleanField(default=False)
    minimum = models.IntegerField(blank=True, null=True)
    maximum = models.IntegerField(blank=True, null=True)
    description = models.CharField(max_length=200, blank=True, null=True)
    
    # Database-specific properties
    x_type = models.CharField(max_length=200, blank=True, null=True, help_text="Database type (e.g., int64, string, boolean)")
    x_unique = models.BooleanField(default=False, help_text="Whether this field is unique")
    x_index = models.BooleanField(default=False, help_text="Whether this field is indexed")
    default_value = models.TextField(blank=True, null=True, help_text="Default value for this field")
    x_relation_schema_id = models.CharField(max_length=200, blank=True, null=True, help_text="Reference to another schema for relations")

    def __str__(self):
        return self.name


class FieldType(models.Model):
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    custom_type = models.BooleanField(default=False)
    enum_choices = models.CharField(max_length=1000, null=True, blank=True)
    max_length = models.IntegerField(null=True, blank=True)
    type = models.CharField(max_length=200, default='string')
    protobuf_type = models.CharField(max_length=200, default='string')
    format = models.CharField(max_length=200, blank=True, null=True)
    schema_definition = models.TextField(blank=True, null=True, help_text="Full JSON schema definition for complex object types")

    def __str__(self):
        return self.name
    
    def get_schema_definition(self):
        """Get schema definition as dict, or None if not set"""
        if self.schema_definition:
            try:
                return json.loads(self.schema_definition)
            except json.JSONDecodeError:
                return None
        return None
    
    def set_schema_definition(self, schema_dict):
        """Set schema definition from dict"""
        if schema_dict:
            self.schema_definition = json.dumps(schema_dict)
        else:
            self.schema_definition = None


class Service(models.Model):
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    slug_name = models.CharField(max_length=200)
    asyncapi_version = models.CharField(max_length=20, default="3.0.0")  # Updated to 3.0.0
    version = models.CharField(max_length=20, default="1.0.0")
    description = models.TextField(max_length=1000, default='Service description')
    original_title = models.CharField(max_length=500, blank=True, null=True, help_text="Original title from AsyncAPI YAML info section")
    x_general_name = models.CharField(max_length=200, blank=True, null=True, help_text="x-general-name from AsyncAPI YAML")
    x_service_name = models.CharField(max_length=200, blank=True, null=True, help_text="x-service-name from AsyncAPI YAML (kebab-case)")
    consumes = models.ManyToManyField("Event", related_name="event_consumers", blank=True)
    publishes = models.ManyToManyField("Event", related_name="event_publishers", blank=True)
    database_payloads = models.ManyToManyField("DatabasePayload", related_name="service_payloads", blank=True)

    def __str__(self):
        return self.name

    def kebab_name(self):
        return '-'.join(
            re.sub(r"(\s|_|-)+", " ",
                   re.sub(r"[A-Z]{2,}(?=[A-Z][a-z]+[0-9]*|\b)|[A-Z]?[a-z]+[0-9]*|[A-Z]|[0-9]+",
                    lambda mo: ' ' + mo.group(0).lower(), self.name)).split())

class DatabaseTables(models.Model):
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    slug_name = models.CharField(max_length=200)
    description = models.TextField(max_length=1000, default='Table description')
    fields = models.ManyToManyField("Field", related_name="table_fields", blank=True)

    def __str__(self):
        return self.name


class Project(models.Model):
    name = models.CharField(max_length=200)
    slug_name = models.CharField(max_length=20)

    def __str__(self):
        return self.name
