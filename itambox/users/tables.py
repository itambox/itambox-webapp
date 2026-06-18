import django_tables2 as tables
from django_tables2.utils import A
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from core.tables import ActionsColumn, BaseTable, ToggleColumn, BooleanColumn

User = get_user_model()

class UserTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    username = tables.LinkColumn('users:user_detail', args=[A('pk')], verbose_name=_('Username'))
    first_name = tables.Column(verbose_name=_('First Name'))
    last_name = tables.Column(verbose_name=_('Last Name'))
    email = tables.EmailColumn(verbose_name=_('Email'))
    is_active = BooleanColumn(verbose_name=_('Active'))
    is_staff = BooleanColumn(verbose_name=_('Staff'))
    is_superuser = BooleanColumn(verbose_name=_('Superuser'))
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = User
        fields = ('pk', 'username', 'first_name', 'last_name', 'email', 'is_active', 'is_staff', 'is_superuser', 'actions')
        default_columns = ('pk', 'username', 'first_name', 'last_name', 'email', 'is_active', 'actions')
