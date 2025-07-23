from django.db import models

from core.registry import registry


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
        if not any(f.name == 'custom_field_data' for f in cls._meta.get_fields() if hasattr(f, 'name')):
            raise TypeError(
                f"{cls.__name__} using CustomFieldDataMixin must define a "
                f"'custom_field_data' JSONField."
            )
        registry.register_feature(cls, 'custom_field_data')


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


class JournalingMixin:
    """
    Mixin for models that support persistent free-form commentary
    via JournalEntry records.
    Models using this mixin will have a reverse GenericRelation
    to JournalEntry for easy lookup.
    """

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        registry.register_feature(cls, 'journaling')


class ImageAttachmentMixin:
    """
    Mixin for models that can have uploaded images.
    """

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        registry.register_feature(cls, 'image_attachments')


class FileAttachmentMixin:
    """
    Mixin for models that can have uploaded files.
    """

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

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        registry.register_feature(cls, 'soft_delete')
