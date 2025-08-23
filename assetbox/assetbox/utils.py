from assetbox.constants import DEFAULT_PAGINATE_COUNT, PAGINATE_COUNT_CHOICES
import datetime
from decimal import Decimal
import logging
from django.conf import settings
from django.core.paginator import Paginator
from django.contrib.contenttypes.models import ContentType
from django.utils.module_loading import import_string
from django.shortcuts import reverse
from users.models import UserPreference
from django.db.models import Model
from django.forms.models import model_to_dict

logger = logging.getLogger(__name__)


def get_model_viewname(model, action):
    app_label = model._meta.app_label
    if app_label in ['components', 'inventory', 'compliance']:
        app_label = 'assets'
    model_name = model._meta.model_name
    return f"{app_label}:{model_name}_{action}"


def get_paginate_count(request):
    try:
        per_page_param = request.GET.get('per_page')
        if per_page_param:
            per_page = int(per_page_param)
            if per_page in dict(PAGINATE_COUNT_CHOICES):
                return per_page
    except (ValueError, TypeError):
        logger.debug("Invalid per_page query parameter: '%s'", request.GET.get('per_page'))

    if request.user.is_authenticated:
        try:
            prefs = UserPreference.objects.filter(user=request.user).first()
            if prefs and prefs.data:
                user_pref_val = prefs.data.get('pagination', {}).get('per_page')
                if user_pref_val:
                    try:
                        user_pref = int(user_pref_val)
                        if user_pref in dict(PAGINATE_COUNT_CHOICES):
                            return user_pref
                    except (ValueError, TypeError):
                        logger.debug("Invalid user preference pagination value: '%s'", user_pref_val)
        except Exception as e:
            logger.debug("Error reading user pagination preferences: %s", e)

    return DEFAULT_PAGINATE_COUNT


class ChoiceSet:
    CHOICES = []

    def __iter__(self):
        yield from [(c[0], c[1]) for c in self.CHOICES]


from django.contrib.contenttypes.models import ContentType
from assetbox.middleware import get_current_request_id, get_current_user


def serialize_object(obj: Model, extra_fields=None, exclude_fields=None) -> dict:
    if not obj:
        return None

    if extra_fields is None:
        extra_fields = set()
    if exclude_fields is None:
        exclude_fields = set()

    data = {}
    m2m_fields = {f.name for f in obj._meta.many_to_many}

    for field in obj._meta.get_fields():
        field_name = field.name

        if field_name in exclude_fields:
            continue

        if not field.concrete or field.name == obj._meta.pk.name:
            if field.name not in extra_fields:
                continue

        try:
            field_value = getattr(obj, field_name)
        except AttributeError:
            continue

        if field_name in m2m_fields:
            if hasattr(field_value, 'all'):
                data[field_name] = sorted(list(field_value.values_list('pk', flat=True)))
            else:
                data[field_name] = []
        elif field.is_relation:
            if field_value is not None:
                data[field_name] = field_value.pk
            else:
                data[field_name] = None
        else:
            if isinstance(field_value, (datetime.date, datetime.datetime)):
                data[field_name] = field_value.isoformat()
            elif isinstance(field_value, Decimal):
                data[field_name] = str(field_value)
            else:
                data[field_name] = field_value

    return data


def log_change(instance, action, prechange_data=None, postchange_data=None, user=None, request_id=None):
    from core.choices import ObjectChangeActionChoices
    from core.models import ObjectChange

    if user is None:
        user = get_current_user()
    if request_id is None:
        request_id = get_current_request_id()

    logger.debug("User from middleware: %s", user)
    logger.debug("Request ID from middleware: %s", request_id)

    if not request_id:
        logger.debug("Skipping changelog for %s (%s) - no request_id found.", instance, action)
        return

    try:
        logger.debug("Attempting ObjectChange.objects.create for %s", instance)
        oc = ObjectChange.objects.create(
            user=user,
            user_name=user.username if user else 'System',
            request_id=request_id,
            action=action,
            changed_object_type=ContentType.objects.get_for_model(instance),
            changed_object_id=instance.pk,
            object_repr=str(instance),
            prechange_data=prechange_data,
            postchange_data=postchange_data
        )
        logger.debug("ObjectChange created with PK: %s", oc.pk)
        logger.info("Changelog: Logged %s for %s (User: %s, Request: %s)", action, instance, user, request_id)
    except Exception as e:
        logger.error("Error logging change for %s: %s", instance, e, exc_info=True)


def get_content_type_by_natural_key(natural_key):
    try:
        app_label, model = natural_key.lower().split('.')
        return ContentType.objects.get(app_label=app_label, model=model)
    except (ContentType.DoesNotExist, ValueError, AttributeError):
        return None


def get_table_for_model(model):
    app_label = model._meta.app_label
    model_name = model.__name__
    table_class_name = f"{model_name}Table"
    try:
        tables_module = import_string(f'{app_label}.tables')
        return getattr(tables_module, table_class_name)
    except (ImportError, AttributeError):
        logger.warning("Could not find %s in %s.tables", table_class_name, app_label)
        return None


def get_model_from_string(model_string):
    try:
        app_label, model_name = model_string.split('.')
        return ContentType.objects.get(app_label=app_label, model=model_name.lower()).model_class()
    except (ContentType.DoesNotExist, ValueError):
        return None


def build_breadcrumbs(request, obj=None):
    breadcrumbs = [{'url': reverse('dashboard'), 'name': 'Home'}]
    path_parts = request.path.strip('/').split('/')

    if len(path_parts) > 0 and path_parts[0]:
        app_url_name = f"{path_parts[0]}:index"
        try:
            if path_parts[0] == 'assets':
                list_url = reverse('assets:asset_list')
                breadcrumbs.append({'url': list_url, 'name': path_parts[0].capitalize()})
        except Exception:
            breadcrumbs.append({'url': None, 'name': path_parts[0].capitalize()})

    if obj:
        model_meta = obj._meta
        app_label = model_meta.app_label
        if app_label in ['components', 'inventory', 'compliance']:
            app_label = 'assets'
        list_view_name = f"{app_label}:{model_meta.model_name}_list"
        try:
            list_url = reverse(list_view_name)
            breadcrumbs.append({'url': list_url, 'name': model_meta.verbose_name_plural.capitalize()})
        except Exception:
            logger.debug("List view URL not found for %s, skipping list breadcrumb", list_view_name)
        breadcrumbs.append({'url': obj.get_absolute_url(), 'name': str(obj)})
    elif len(path_parts) > 1:
        page_title = path_parts[-1].replace('-', ' ').capitalize()
        if breadcrumbs[-1]['name'].lower() != page_title.lower():
            breadcrumbs.append({'url': request.path, 'name': page_title})

    if breadcrumbs:
        breadcrumbs[-1]['is_active'] = True

    return breadcrumbs
