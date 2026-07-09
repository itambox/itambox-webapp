from django.db import models
from django.contrib.contenttypes.fields import GenericRelation
from django.utils.translation import gettext_lazy as _

from itambox.registry import registry




class BookmarkableMixin:
    """
    Mixin for models that users can bookmark (star) and watch (bell) in the UI.
    Registering here sets both 'bookmarkable' (personal quick-access pin, no
    notifications) and 'watchable' (notify-on-change via ObjectWatch) features on
    the model. Every bookmarkable model is also watchable; do NOT add a second mixin.
    """

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        registry.register_feature(cls, 'bookmarkable')
        registry.register_feature(cls, 'watchable')


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


class CustomFieldDataMixin(models.Model):
    """
    Abstract model providing custom field value storage. Which fields apply to
    a model is declared on extras.CustomField.object_types; this mixin only
    supplies the JSON storage and registers the feature flag.
    """

    custom_field_data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("Custom Field Data"),
    )

    class Meta:
        abstract = True

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
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


class JournalingMixin(models.Model):
    """
    Mixin for models that support persistent free-form commentary
    via JournalEntry records.
    Models using this mixin will have a reverse GenericRelation
    to JournalEntry for easy lookup.
    """
    journal_entries = GenericRelation(
        'extras.JournalEntry',
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
        'extras.ImageAttachment',
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
        'extras.FileAttachment',
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

    deleted_at = models.DateTimeField(null=True, blank=True, editable=False, db_index=True)

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
        performs a standard physical delete (still change-logged when a
        ChangeLoggingMixin sits later in the MRO). Otherwise, soft-deletes.
        """
        # force_hard_delete may also have been stashed on the instance by
        # ChangeLoggingMixin earlier in the MRO. Resolve from either source and
        # re-stash so the partner mixin sees it regardless of MRO order; never
        # forward the kwarg to super() (models.Model.delete() rejects it).
        force_hard = force_hard_delete or getattr(self, '_force_hard_delete', False)
        self._force_hard_delete = force_hard
        if force_hard:
            super().delete(*args, **kwargs)
        else:
            from django.db import transaction

            with transaction.atomic():
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
                
                from django.utils import timezone
                now = timezone.now()
                
                for model, instances in list(collector.data.items()):
                    pks_to_soft_delete = []
                    for instance in instances:
                        if instance == self:
                            continue
                        if isinstance(instance, SoftDeleteMixin):
                            if instance.deleted_at is None:
                                pks_to_soft_delete.append(instance.pk)
                                # Cascade changelog generation to prevent audit trail blind spots
                                if hasattr(instance, '_log_change') and callable(instance._log_change):
                                    from itambox.utils import serialize_object
                                    excluded = getattr(instance, '_change_logging_excluded_fields', ['updated_at'])
                                    prechange_data = serialize_object(instance, exclude_fields=excluded)
                                    instance._log_change(action='delete', prechange_data=prechange_data)
                        else:
                            if instance.pk is not None:
                                instance.delete()
                    
                    if pks_to_soft_delete:
                        # _base_manager (unscoped): these are cascade children of `self`
                        # collected by the ORM, so they MUST be soft-deleted regardless of
                        # the active tenant context. The tenant-scoped manager would match
                        # zero rows for a child in a different/None tenant — leaving it active
                        # while a 'delete' audit entry was already written above (divergence).
                        model._base_manager.filter(pk__in=pks_to_soft_delete).update(deleted_at=now)
                
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
            from itambox.utils import generate_unique_slug
            generate_unique_slug(self, self.slug_source)
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


