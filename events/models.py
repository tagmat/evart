from django.db import models


class EventType(models.Model):
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name


class Event(models.Model):
    name = models.CharField(max_length=200)
    domain = models.ForeignKey("Domain", on_delete=models.CASCADE)
    payload = models.ForeignKey("Payload", null=True, blank=True, on_delete=models.SET_NULL)
    response_payload = models.ForeignKey("Payload", null=True, blank=True, on_delete=models.SET_NULL,
                                         related_name="response_payload")
    type = models.ForeignKey("EventType", default=1, on_delete=models.CASCADE)

    def __str__(self):
        return "{0}/{1} [{2}]".format(self.domain.name, self.name, self.type.name)

    def slug_name(self):
        return "{0}.{1}".format(self.domain.name.replace("/", "_"), self.name)


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
    format = models.CharField(max_length=200, blank=True, null=True)

    def __str__(self):
        return self.name


class Service(models.Model):
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    consumes = models.ManyToManyField("Event", related_name="event_consumers")
    publishes = models.ManyToManyField("Event", related_name="event_publishers")

    def __str__(self):
        return self.name


class Project(models.Model):
    name = models.CharField(max_length=200)
    slug_name = models.CharField(max_length=20)

    def __str__(self):
        return self.name
