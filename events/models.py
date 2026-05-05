import re
import json

from django.db import models
from django.db.utils import OperationalError
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
    http_method = models.CharField(
        max_length=10, blank=True, null=True,
        choices=[('get', 'GET'), ('post', 'POST'), ('put', 'PUT'), ('delete', 'DELETE'), ('patch', 'PATCH')],
        help_text="HTTP method for sync endpoints (emitted as x-http-method on the operation). Defaults to 'post' if blank."
    )
    is_jwt = models.BooleanField(default=False, help_text="Whether this endpoint requires JWT authentication")
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
    service = models.ForeignKey("Service", on_delete=models.CASCADE, help_text="Service that owns this database object")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True, help_text="Schema description")
    create_rest = models.BooleanField(default=False)

    # Database-specific properties
    x_parser_schema_id = models.CharField(max_length=200, blank=True, null=True, help_text="Parser schema ID")
    x_derives_from = models.CharField(max_length=200, blank=True, null=True, help_text="Schema this derives from")

    class Meta:
        unique_together = [['service', 'name']]

    def __str__(self):
        try:
            # Check if service_id column exists by trying to access service
            if hasattr(self, 'service_id') and self.service_id:
                return f"{self.service.name}/{self.name}"
            elif hasattr(self, 'service') and self.service:
                return f"{self.service.name}/{self.name}"
            else:
                return self.name
        except (AttributeError, OperationalError):
            # Fallback if service column doesn't exist (migration not run)
            return self.name


class Field(models.Model):
    name = models.CharField(
        max_length=200,
        help_text="Field name as it appears in the YAML schema. Use camelCase (e.g. 'orderId', 'fromService'). Becomes the property key in the generated JSON Schema."
    )
    type = models.ForeignKey(
        "FieldType", on_delete=models.RESTRICT,
        help_text="Determines the JSON Schema 'type' and 'format' emitted in the YAML. For Go-specific types (int64, uuid, etc.) pick a FieldType whose 'x-type' field is set. Create new FieldTypes in the FieldType admin if needed."
    )
    payload = models.ForeignKey("Payload", on_delete=models.CASCADE)
    required = models.BooleanField(
        default=False,
        help_text="If checked, this field is listed under the schema's 'required' array, meaning API consumers must always provide it."
    )
    minimum = models.IntegerField(
        blank=True, null=True,
        help_text="Minimum allowed numeric value (JSON Schema 'minimum'). Leave blank to omit the constraint."
    )
    maximum = models.IntegerField(
        blank=True, null=True,
        help_text="Maximum allowed numeric value (JSON Schema 'maximum'). Leave blank to omit the constraint."
    )
    description = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="Human-readable explanation of what this field contains. Emitted verbatim in the YAML schema and shown in generated API docs."
    )
    # Additional fields for complex types
    array_items_type = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="For array fields: the primitive JSON Schema type of each item (e.g. 'string', 'integer'). Use this when items are plain scalars with no $ref. Leave blank if using 'Array items ref' instead."
    )
    array_items_ref = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="For array fields: a $ref path to a schema component for complex item types (e.g. '#/components/schemas/OrderItem'). Takes precedence over 'Array items type' when both are set."
    )
    schema_ref = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="Emits a $ref on this field pointing to another component schema (e.g. '#/components/schemas/Address'). Use for inline nested object references instead of listing properties here."
    )

    def __str__(self):
        return self.name


class DatabaseField(models.Model):
    name = models.CharField(
        max_length=200,
        help_text="Field name in camelCase as it appears in the DB schema (e.g. 'referenceID', 'tenantId'). Becomes the Go struct field name after code generation."
    )
    type = models.ForeignKey(
        "FieldType", on_delete=models.RESTRICT,
        help_text="Base JSON Schema type for this column. Typically overridden by 'X type' for Go-specific types. If you need int64 or uuid, set x_type on the FieldType."
    )
    payload = models.ForeignKey("DatabasePayload", on_delete=models.CASCADE)
    required = models.BooleanField(
        default=False,
        help_text="Lists this field under the schema's 'required' array."
    )
    minimum = models.IntegerField(
        blank=True, null=True,
        help_text="Minimum numeric value constraint (JSON Schema 'minimum')."
    )
    maximum = models.IntegerField(
        blank=True, null=True,
        help_text="Maximum numeric value constraint (JSON Schema 'maximum')."
    )
    description = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="Human-readable description of what this column stores. Emitted verbatim in the YAML schema."
    )

    # -------------------------------------------------------------------------
    # Type & column definition
    # -------------------------------------------------------------------------
    x_type = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="Go type for this column. Emitted as 'x-type' in YAML and drives Go struct field type in code generation. "
                  "Common values: 'int64', 'string', 'bool', 'float64', 'uuid', 'time.Time', 'jsonb', 'array'. "
                  "Leave blank to rely on the JSON Schema type only."
    )
    x_type_override = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="Raw SQL column type that bypasses Go type inference. Emitted as 'x-type-override' and maps to GORM's 'column:type' tag. "
                  "Use when you need an exact SQL type like 'varchar(32)', 'decimal(10,2)', or 'text'. "
                  "Takes precedence over 'x-type' at the database DDL level."
    )
    x_size = models.IntegerField(
        blank=True, null=True,
        help_text="Maximum character/byte length for the column. Emitted as 'x-size' and maps to GORM's 'size' tag. "
                  "E.g. 64 for a 64-character reference ID. Leave blank to let the DB use its default size."
    )

    # -------------------------------------------------------------------------
    # Constraints & indexing
    # -------------------------------------------------------------------------
    x_unique = models.BooleanField(
        default=False,
        help_text="Adds a UNIQUE constraint on this single column (GORM 'uniqueIndex' on just this field). "
                  "Emitted as 'x-unique: true'. Use 'x-unique-index' instead for composite unique constraints spanning multiple columns."
    )
    x_unique_index = models.BooleanField(
        default=False,
        help_text="Adds a unique index that can span multiple columns for composite uniqueness (e.g. email + tenant_id). "
                  "Emitted as 'x-unique-index: true'. Multiple fields in the same schema can share this flag to form a composite index."
    )
    x_index = models.BooleanField(
        default=False,
        help_text="Creates a non-unique database index on this column to speed up WHERE / ORDER BY queries. "
                  "Emitted as 'x-index: true'. Does not enforce uniqueness."
    )
    x_not_null = models.BooleanField(
        default=False,
        help_text="Adds a NOT NULL constraint at the database level. Emitted as 'x-not-null: true'. "
                  "Use together with 'Default value' if existing rows need a backfill value during migration."
    )
    x_nullable = models.BooleanField(
        default=False,
        help_text="Explicitly marks the column as nullable, generating a Go pointer type (e.g. *string instead of string). "
                  "Emitted as 'x-nullable: true'. Without this, GORM may treat the column as NOT NULL by default."
    )
    x_check = models.CharField(
        max_length=500, blank=True, null=True,
        help_text="SQL CHECK constraint expression enforced by the database engine. "
                  "Emitted as 'x-check'. Example: \"status IN ('ACTIVE','SUSPENDED','DELETED')\". "
                  "Write the raw SQL expression without the CHECK(...) wrapper."
    )

    # -------------------------------------------------------------------------
    # Column metadata
    # -------------------------------------------------------------------------
    x_column = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="Override the physical database column name when it differs from the Go field name. "
                  "Emitted as 'x-column'. Example: field name 'referenceID' → column name 'reference_id'. "
                  "Leave blank to let GORM derive the column name automatically from the field name."
    )
    x_comment = models.CharField(
        max_length=500, blank=True, null=True,
        help_text="Comment stored on the column in the database (PostgreSQL COMMENT ON COLUMN, MySQL COMMENT). "
                  "Emitted as 'x-comment'. Useful for DBA documentation and introspection tools."
    )
    x_serializer = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="GORM serializer for transparent (de)serialization of the column value. "
                  "Emitted as 'x-serializer'. Use 'json' to store a Go struct as a JSON blob in the DB column. "
                  "Combine with 'x-type: jsonb' for PostgreSQL native JSONB columns."
    )
    x_ignore = models.BooleanField(
        default=False,
        help_text="Marks the field with the GORM tag 'gorm:\"-\"', telling GORM to completely ignore it — "
                  "the column is not created, read, written, or migrated. "
                  "Emitted as 'x-ignore: true'. Use for computed, transient, or virtual fields."
    )

    # -------------------------------------------------------------------------
    # Numeric precision
    # -------------------------------------------------------------------------
    x_precision = models.IntegerField(
        blank=True, null=True,
        help_text="Total number of significant digits for DECIMAL / NUMERIC columns (precision). "
                  "Emitted as 'x-precision'. Example: 12 allows values up to 9999999999.99 with scale 2. "
                  "Only meaningful for number types; leave blank for integers or strings."
    )
    x_scale = models.IntegerField(
        blank=True, null=True,
        help_text="Number of digits after the decimal point (scale). Emitted as 'x-scale'. "
                  "Example: 2 for monetary values like 1234.56. Must be less than or equal to 'x-precision'."
    )

    # -------------------------------------------------------------------------
    # Automatic timestamps & auto-increment
    # -------------------------------------------------------------------------
    x_auto_create_time = models.BooleanField(
        default=False,
        help_text="Automatically sets this column to the current UTC timestamp on INSERT (GORM autoCreateTime). "
                  "Emitted as 'x-auto-create-time: true'. Field type should be 'time.Time'."
    )
    x_auto_update_time = models.BooleanField(
        default=False,
        help_text="Automatically updates this column to the current UTC timestamp on every UPDATE (GORM autoUpdateTime). "
                  "Emitted as 'x-auto-update-time: true'. Field type should be 'time.Time'."
    )
    x_auto_increment = models.BooleanField(
        default=False,
        help_text="Makes this integer column auto-incrementing in the database (GORM autoIncrement). "
                  "Emitted as 'x-auto-increment: true'. Typically used for surrogate sequence columns, not the primary key."
    )

    # -------------------------------------------------------------------------
    # Default value
    # -------------------------------------------------------------------------
    default_value = models.TextField(
        blank=True, null=True,
        help_text="Default value applied by the database when no value is provided on INSERT. "
                  "Emitted as 'x-default' in YAML. Write the value directly: true/false for booleans, "
                  "numbers without quotes, strings without quotes (e.g. ACTIVE, not 'ACTIVE'). "
                  "Leave blank to omit the default."
    )

    # -------------------------------------------------------------------------
    # Associations — has-many / belongs-to
    # -------------------------------------------------------------------------
    x_relation_schema_id = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="Name of the related DB schema for ORM associations. Emitted as 'x-relation-schema-id'. "
                  "Example: 'DB_order' on a User schema creates a has-many relation to DB_order. "
                  "The code generator uses this to emit the correct Go struct reference."
    )
    x_foreign_key = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="Override the foreign key column name for has-many / belongs-to relations. "
                  "Emitted as 'x-foreign-key'. Example: 'UserID' on a has-many orders relation. "
                  "Leave blank to let GORM derive it from the parent struct name."
    )
    x_references = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="The primary key column being referenced by the foreign key. Emitted as 'x-references'. "
                  "Example: 'ID'. Leave blank to default to the related model's primary key."
    )
    x_cascade_update = models.CharField(
        max_length=50, blank=True, null=True,
        help_text="ON UPDATE behaviour for the foreign key constraint. Emitted as 'x-cascade-update'. "
                  "Common values: 'CASCADE', 'SET NULL', 'RESTRICT', 'NO ACTION'."
    )
    x_cascade_delete = models.CharField(
        max_length=50, blank=True, null=True,
        help_text="ON DELETE behaviour for the foreign key constraint. Emitted as 'x-cascade-delete'. "
                  "Common values: 'CASCADE', 'SET NULL', 'RESTRICT', 'NO ACTION'."
    )

    # -------------------------------------------------------------------------
    # Associations — many-to-many
    # -------------------------------------------------------------------------
    x_many_to_many = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="Identifier for the many-to-many association, used in GORM's 'many2many' tag. "
                  "Emitted as 'x-many-to-many'. Example: 'product_tag_lnk'. "
                  "Also set 'x-join-table', 'x-join-foreign-key', and 'x-join-references' to fully define the M2M."
    )
    x_join_table = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="Physical name of the junction/pivot table for the M2M relation. Emitted as 'x-join-table'. "
                  "Example: 'product_tags'. This table holds two FK columns linking the two models."
    )
    x_join_foreign_key = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="Foreign key column in the junction table that points to this model's primary key. "
                  "Emitted as 'x-join-foreign-key'. Example: 'product_id' on a Product→Tag M2M."
    )
    x_join_references = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="Foreign key column in the junction table that points to the related model's primary key. "
                  "Emitted as 'x-join-references'. Example: 'tag_id' on a Product→Tag M2M."
    )
    x_association_autocreate = models.BooleanField(
        default=False,
        help_text="When saving the parent, GORM will automatically CREATE new associated records that don't yet exist. "
                  "Emitted as 'x-association-autocreate: true'. Use with caution — can create unintended rows."
    )
    x_association_autoupdate = models.BooleanField(
        default=False,
        help_text="When saving the parent, GORM will automatically UPDATE existing associated records. "
                  "Emitted as 'x-association-autoupdate: true'."
    )
    x_association_save_reference = models.BooleanField(
        default=False,
        help_text="GORM will write the foreign key reference back to the associated record after saving the parent. "
                  "Emitted as 'x-association-save-reference: true'."
    )

    # -------------------------------------------------------------------------
    # Embedded structs
    # -------------------------------------------------------------------------
    x_embedded = models.BooleanField(
        default=False,
        help_text="Embeds this struct's fields directly into the parent table (GORM 'embedded' tag). "
                  "Emitted as 'x-embedded: true'. The field's 'x-type' must be a Go struct type. "
                  "Use 'x-embedded-prefix' to avoid column name collisions."
    )
    x_embedded_prefix = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="Column name prefix applied to all fields of an embedded struct to avoid collisions. "
                  "Emitted as 'x-embedded-prefix'. Example: 'audit_' makes embedded fields like 'audit_created_at', 'audit_updated_at'."
    )

    # -------------------------------------------------------------------------
    # Polymorphic associations
    # -------------------------------------------------------------------------
    x_polymorphic = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="Name of the polymorphic type discriminator field used in GORM polymorphic belongs-to. "
                  "Emitted as 'x-polymorphic'. Example: 'Owner' creates columns 'OwnerID' and 'OwnerType' in the table."
    )
    x_polymorphic_value = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="The concrete type value this model registers as in the polymorphic discriminator column. "
                  "Emitted as 'x-polymorphic-value'. Example: 'product' means OwnerType = 'product' for this model's rows."
    )
    x_association_foreign_key = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="Override the foreign key column used by an association (distinct from 'x-foreign-key' which is for has-many). "
                  "Emitted as 'x-association-foreign-key'. Example: 'ProductID' for a polymorphic owner association."
    )
    x_constraint = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="Named database constraint for this association (e.g. 'fk_attachment_owner'). "
                  "Emitted as 'x-constraint'. Naming constraints makes it easier to reference them in migrations and error messages."
    )

    # -------------------------------------------------------------------------
    # Eager-loading & primary key
    # -------------------------------------------------------------------------
    x_preload = models.BooleanField(
        default=False,
        help_text="Adds 'preload' to the GORM tag so this association is eager-loaded whenever the parent is fetched. "
                  "Emitted as 'x-preload: true'. Only meaningful on relation fields (x-relation-schema-id set)."
    )
    x_primary_key = models.BooleanField(
        default=False,
        help_text="Marks this field as the table's primary key (GORM 'primaryKey' tag). "
                  "Emitted as 'x-primary-key: true'. Use only when you need a custom PK type/name — "
                  "by default every DB_* model embeds gorm.Model which provides a uint ID primary key."
    )

    def __str__(self):
        return self.name


class FieldType(models.Model):
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    name = models.CharField(
        max_length=200,
        help_text="Unique identifier for this type within the project. "
                  "For primitives use the JSON Schema keyword (e.g. 'string', 'integer'). "
                  "For Go-specific overrides use the Go type name (e.g. 'int64', 'uuid', 'time.Time'). "
                  "This name is what Field records reference when selecting a type."
    )
    custom_type = models.BooleanField(
        default=False,
        help_text="Mark as a project-specific type (enum, complex object, or custom scalar). "
                  "Leave unchecked for standard JSON Schema primitives (string, integer, boolean, etc.)."
    )
    enum_choices = models.CharField(
        max_length=1000, null=True, blank=True,
        help_text="Comma-separated list of allowed enum values. Only used when 'Custom type' is checked. "
                  "Example: 'STANDARD,PREMIUM,ENTERPRISE'. Emitted as a JSON Schema 'enum' array in the YAML."
    )
    max_length = models.IntegerField(
        null=True, blank=True,
        help_text="Maximum string length constraint (JSON Schema 'maxLength'). Leave blank to omit."
    )
    type = models.CharField(
        max_length=200, default='string',
        help_text="Base JSON Schema type keyword emitted in the YAML 'type' field. "
                  "Must be one of: 'string', 'integer', 'number', 'boolean', 'array', 'object'. "
                  "For Go-specific types, set 'x-type' in addition to this field."
    )
    x_type = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="Go-specific type emitted as 'x-type' in the YAML schema. "
                  "Overrides the raw JSON Schema type for Go struct generation. "
                  "Common values: 'int64', 'float64', 'bool', 'uuid', 'time.Time', 'jsonb'. "
                  "Leave blank when the JSON Schema 'type' is sufficient."
    )
    protobuf_type = models.CharField(
        max_length=200, default='string',
        help_text="Protobuf scalar type used during gRPC code generation. "
                  "Common values: 'string', 'int64', 'int32', 'bool', 'float', 'double', 'bytes', "
                  "'google.protobuf.Timestamp'. Only relevant when x-transport includes gRPC."
    )
    format = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="JSON Schema 'format' keyword emitted alongside 'type'. "
                  "Common values: 'date-time', 'uuid', 'email', 'uri', 'byte'. "
                  "Leave blank to omit the format hint."
    )
    schema_definition = models.TextField(
        blank=True, null=True,
        help_text="Full JSON Schema definition stored as a JSON string for complex object types. "
                  "Used when this type is an inline object with its own properties (e.g. a reusable nested struct). "
                  "Leave blank for simple scalar or enum types."
    )

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
    x_service_ip = models.CharField(max_length=100, blank=True, null=True, help_text="x-service-ip placeholder for deployment config")
    x_transport = models.CharField(
        max_length=10, blank=True, null=True,
        choices=[('rest', 'REST'), ('grpc', 'gRPC'), ('both', 'Both')],
        help_text="x-transport: rest | grpc | both"
    )
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


class HTTPClient(models.Model):
    """External HTTP service client config — generates typed config struct + viper keys."""
    service = models.ForeignKey("Service", on_delete=models.CASCADE, related_name="http_clients")
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name


class HTTPClientField(models.Model):
    """A named field on an HTTPClient config struct."""
    client = models.ForeignKey("HTTPClient", on_delete=models.CASCADE, related_name="fields")
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name


class GRPCClient(models.Model):
    """External gRPC service client config — generates <Name>GrpcConfig{Address}."""
    service = models.ForeignKey("Service", on_delete=models.CASCADE, related_name="grpc_clients")
    name = models.CharField(max_length=200, help_text="Client identifier used in generated config structs (e.g. 'catalog').")
    module = models.CharField(max_length=200, blank=True, null=True, help_text="Go import module path for this gRPC client (e.g. 'catalog'). Emitted as x-grpc-clients[].module.")
    proto_service = models.CharField(max_length=200, blank=True, null=True, help_text="Proto service name (e.g. 'CatalogPublicService'). Emitted as x-grpc-clients[].service.")

    def __str__(self):
        return self.name


class GRPCMethod(models.Model):
    """A typed method on a GRPCClient — generates convenience wrapper functions."""
    client = models.ForeignKey("GRPCClient", on_delete=models.CASCADE, related_name="methods")
    name = models.CharField(max_length=200, help_text="gRPC method name (e.g. 'GetProduct').")
    request = models.CharField(max_length=200, help_text="Request message type (e.g. 'GetProductRequest').")
    response = models.CharField(max_length=200, help_text="Response message type (e.g. 'GetProductResponse').")
    input_field = models.CharField(max_length=200, blank=True, null=True, help_text="Input field name for typed wrapper (e.g. 'product_id'). If blank, raw req/resp stub is forwarded.")
    output_field = models.CharField(max_length=200, blank=True, null=True, help_text="Output field name for typed wrapper (e.g. 'product'). If blank, raw req/resp stub is forwarded.")

    def __str__(self):
        return f"{self.client.name}.{self.name}"


class DbOperation(models.Model):
    """x-db-operation helper attached to an async Event channel."""
    DB_OP_CHOICES = [
        ('block', 'block'),
        ('unblock', 'unblock'),
        ('delete-by-field', 'delete-by-field'),
        ('update', 'update'),
    ]
    event = models.OneToOneField("Event", on_delete=models.CASCADE, related_name="db_operation")
    type = models.CharField(max_length=50, choices=DB_OP_CHOICES)
    schema = models.CharField(max_length=200, help_text="Target DB schema name (e.g. DB_user)")
    lookup_field = models.CharField(max_length=200)
    lookup_field_2 = models.CharField(max_length=200, blank=True, null=True, help_text="Second lookup field for composite queries")
    status = models.CharField(max_length=200, blank=True, null=True, help_text="Status value to set (for block type)")
    function_name = models.CharField(max_length=200, blank=True, null=True, help_text="Override generated function name")

    def __str__(self):
        return f"{self.event.name} → {self.type}"
