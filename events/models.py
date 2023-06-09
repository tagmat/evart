import re

from django.db import models


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

    def __str__(self):
        return "{0}/{1} [{2}]".format(self.domain.name, self.name, self.type.name)

    def pascal_name(self, with_params=False, response=False):
        name = self.name if with_params else re.sub(r"\[(.*?)\]", "",
                                                    re.sub(r"\.\[(.*?)\]", "", self.name))
        return "%s%s%s" % (name[0].upper(), name[1:], 'Response' if response else '')

    def slug_name(self, with_params=False, response=False):
        return "{0}.{2}.{1}".format(
            self.domain.name.replace("/", "_"),
            self.name if with_params else re.sub(r"\[(.*?)\]", "",
                                                 re.sub(r"\.\[(.*?)\]", "", self.name)),
            self.type.name.lower() if not response else "response")


class Domain(models.Model):
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name


class Payload(models.Model):
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    name = models.CharField(max_length=200)

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

    def __str__(self):
        return self.name


class Service(models.Model):
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    slug_name = models.CharField(max_length=200)
    asyncapi_version = models.CharField(max_length=20, default="2.6.0")
    version = models.CharField(max_length=20, default="1.0.0")
    description = models.TextField(max_length=1000, default='Service description')
    consumes = models.ManyToManyField("Event", related_name="event_consumers", blank=True)
    publishes = models.ManyToManyField("Event", related_name="event_publishers", blank=True)

    def __str__(self):
        return self.name


class GrpcPackage(models.Model):
    name = models.CharField(max_length=200)
    service = models.OneToOneField("Service", on_delete=models.CASCADE)
    syntax = models.CharField(max_length=20, default="proto3")

    def __str__(self):
        return self.name

    @property
    def service_name(self):
        return "%s%sService" % (self.name[0].upper(), self.name[1:])


class GrpcService(models.Model):
    name = models.CharField(max_length=200)
    package = models.ForeignKey("GrpcPackage", on_delete=models.CASCADE)
    request = models.ForeignKey("Payload", verbose_name="Request Payload", on_delete=models.CASCADE,
                                related_name="grpc_request_payload", null=True,
                                blank=True)
    response = models.ForeignKey("Payload", verbose_name="Response Payload", on_delete=models.CASCADE,
                                 related_name="grpc_response_payload", null=True,
                                 blank=True)

    def __str__(self):
        return self.name


class Project(models.Model):
    name = models.CharField(max_length=200)
    slug_name = models.CharField(max_length=20)

    def __str__(self):
        return self.name
