from django.db import models
from django.contrib.contenttypes.fields import GenericRelation
from django.db.models.signals import class_prepared

from assetbox.registry import registry




class BookmarkableMixin:
    """
    Mixin for models that users can bookmark in the UI.
    Models using this mixin will have a reverse GenericRelation
    to the Bookmark model for easy lookup.
    """

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        registry.register_feature(cls, 'bookmarkable')


class CloneableMixin:
    """
    Mixin for models that support deep-copy via clone() method.
    """

    def clone(self):
        fields = [f for f in self._meta.concrete_fields if f.name != self._meta.pk.name]
        clone = self.__class__()
        for field in fields:
            value = getattr(self, field.name)
            if isinstance(value, models.Manager):
                continue
            setattr(clone, field.name, value)
        return clone

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        registry.register_feature(cls, 'cloneable')


class CustomFieldDataMixin:
    """
    Mixin for models that support per-type custom fields
    via a JSON column. Models using this mixin must define
    a `custom_field_data` JSONField or inherit it from this mixin.
    """

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Register the feature eagerly; field validation is deferred
        # to the class_prepared signal handler below.
        registry.register_feature(cls, 'custom_field_data')


def _validate_custom_field_data(sender, **kwargs):
    """
    Deferred validation for CustomFieldDataMixin.
    Called by Django's class_prepared signal after the model class is fully
    built and the app registry is ready. This avoids the AppRegistryNotReady
    error that occurs when accessing cls._meta inside __init_subclass__.

    Uses _meta.local_fields instead of get_fields() because get_fields()
    triggers _relation_tree population, which requires ALL models to be loaded.
    """
    if issubclass(sender, CustomFieldDataMixin) and not getattr(sender._meta, 'abstract', False):
        # Check local_fields (safe during class_prepared — no relation tree lookup)
        local_field_names = {f.name for f in sender._meta.local_fields}
        # Also check MRO for fields defined on parent abstract models
        for klass in sender.__mro__:
            if hasattr(klass, '_meta') and hasattr(klass._meta, 'local_fields'):
                local_field_names.update(f.name for f in klass._meta.local_fields)

        if 'custom_field_data' not in local_field_names and 'custom_values' not in local_field_names:
            raise TypeError(
                f"{sender.__name__} using CustomFieldDataMixin must define a "
                f"'custom_field_data' or 'custom_values' JSONField."
            )


class_prepared.connect(_validate_custom_field_data)


class ExportableMixin:
    """
    Mixin for models that support export templates (CSV, JSON, XML).
    """

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        registry.register_feature(cls, 'exportable')


class ImportableMixin:
    """
    Mixin for models that support CSV/JSON bulk import.
    """

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        registry.register_feature(cls, 'importable')


class JournalingMixin(models.Model):
    """
    Mixin for models that support persistent free-form commentary
    via JournalEntry records.
    Models using this mixin will have a reverse GenericRelation
    to JournalEntry for easy lookup.
    """
    journal_entries = GenericRelation(
        'core.JournalEntry',
        content_type_field='model',
        object_id_field='object_id'
    )

    class Meta:
        abstract = True

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        registry.register_feature(cls, 'journaling')


class ImageAttachmentMixin(models.Model):
    """
    Mixin for models that can have uploaded images.
    """
    image_attachments = GenericRelation(
        'core.ImageAttachment',
        content_type_field='model',
        object_id_field='object_id'
    )

    class Meta:
        abstract = True

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        registry.register_feature(cls, 'image_attachments')


class FileAttachmentMixin(models.Model):
    """
    Mixin for models that can have uploaded files.
    """
    file_attachments = GenericRelation(
        'core.FileAttachment',
        content_type_field='model',
        object_id_field='object_id'
    )

    class Meta:
        abstract = True

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        registry.register_feature(cls, 'file_attachments')


class TaggableMixin:
    """
    Mixin for models that support colored tags via a standard M2M field.
    When adopted, models should declare:
        tags = models.ManyToManyField('extras.Tag', related_name='%(app_label)s_%(class)s', blank=True)
    or equivalent.
    """

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        registry.register_feature(cls, 'taggable')


class SoftDeleteMixin(models.Model):
    """
    Mixin for models that support soft deletion via a `deleted_at` timestamp.
    Objects with a non-null `deleted_at` are considered deleted.
    """

    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True

    def soft_delete(self):
        from django.utils import timezone
        self.deleted_at = timezone.now()
        self.save(update_fields=['deleted_at'])

    def restore(self):
        self.deleted_at = None
        self.save(update_fields=['deleted_at'])

    def delete(self, *args, force_hard_delete=False, **kwargs):
        """
        Overrides Django's standard delete. If force_hard_delete is True,
        performs a standard physical delete. Otherwise, soft-deletes the record.
        """
        if force_hard_delete:
            super().delete(*args, **kwargs)
        else:
            if hasattr(self, '_changelog_action'):
                from core.choices import ObjectChangeActionChoices
                self._changelog_action = ObjectChangeActionChoices.ACTION_DELETE
            
            if hasattr(self, 'snapshot') and callable(self.snapshot):
                self.snapshot()
            
            # Recurse and soft-delete/hard-delete cascading relations
            from django.db.models.deletion import Collector
            
            collector = Collector(using=self._state.db or 'default')
            collector.collect([self])
            collector.sort()
            
            for model, instances in list(collector.data.items()):
                for instance in instances:
                    if instance == self:
                        continue
                    if isinstance(instance, SoftDeleteMixin):
                        if instance.deleted_at is None:
                            instance.delete(force_hard_delete=False)
                    else:
                        if instance.pk is not None:
                            instance.delete()
            
            self.soft_delete()

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        registry.register_feature(cls, 'soft_delete')


class AutoSlugMixin:
    """
    Mixin to automatically generate a unique slug field on save.
    
    By default, it will slugify the field specified by `slug_source` (default: 'name').
    It can also take a tuple/list of field names as `slug_source`, which will be
    concatenated together.
    
    If there is a collision, it will append a counter to ensure uniqueness.
    """
    slug_source = 'name'

    def save(self, *args, **kwargs):
        if not getattr(self, 'slug', None):
            from django.utils.text import slugify
            
            # Resolve slug source
            if isinstance(self.slug_source, (list, tuple)):
                source_values = []
                for field_name in self.slug_source:
                    # Support double underscore relation lookup (e.g. manufacturer__name)
                    if '__' in field_name:
                        parts = field_name.split('__')
                        obj = self
                        for part in parts:
                            obj = getattr(obj, part, None) if obj else None
                        val = str(obj) if obj else ""
                    else:
                        val = getattr(self, field_name, "")
                    if val:
                        source_values.append(str(val))
                slug_src = "-".join(source_values)
            else:
                slug_src = getattr(self, self.slug_source, "")
            
            # Slugify the resolved source string
            self.slug = slugify(slug_src or "auto-slug")
            
            # Handle collision
            base_slug = self.slug
            counter = 1
            model_class = self.__class__
            manager = getattr(model_class, '_base_manager', model_class.objects)
            
            while manager.filter(slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f"{base_slug}-{counter}"
                counter += 1
                
        super().save(*args, **kwargs)


class SubscribableMixin(models.Model):
    """
    Mixin for models that can have SaaS subscriptions assigned to them.
    Models using this mixin will have a reverse GenericRelation
    to SubscriptionAssignment.
    """
    subscriptions = GenericRelation(
        'subscriptions.SubscriptionAssignment',
        content_type_field='content_type',
        object_id_field='object_id',
        related_query_name='%(app_label)s_%(class)s'
    )

    class Meta:
        abstract = True

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        registry.register_feature(cls, 'subscribable')


