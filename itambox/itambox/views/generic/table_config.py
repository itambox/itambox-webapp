import logging

from django.apps import apps
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.template.loader import get_template
from django.utils.module_loading import import_string

from users.forms import TableConfigForm
from users.models import UserPreference

logger = logging.getLogger(__name__)


@login_required
def table_config(request, model_name):
    app_label, table_part = model_name.split('.')
    app_config = apps.get_app_config(app_label)
    table_module = import_string(f'{app_config.name}.tables')
    TableClass = getattr(table_module, table_part)

    table = TableClass([])
    table_verbose_name = str(TableClass.Meta.model._meta.verbose_name_plural).title()

    prefs, _ = UserPreference.objects.get_or_create(user=request.user)
    table_key_for_form = f'{app_label}.{table_part}'
    user_config = prefs.data.get('tables', {}).get(app_label, {}).get(table_part, {})
    logger.debug("Fetched user_config for %s.%s: %s", app_label, table_part, user_config)

    form = TableConfigForm(table=table, user_config=user_config)

    template = get_template('core/includes/table_config_modal.html')
    context = {
        'form': form,
        'table_name': table_key_for_form,
        'table_verbose_name': table_verbose_name,
    }
    return HttpResponse(template.render(context, request))
