import django_tables2 as tables
from django_tables2.utils import A
from django.contrib.auth import get_user_model
from core.tables import ActionsColumn, BaseTable, ToggleColumn, BooleanColumn

User = get_user_model()

class UserTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    username = tables.LinkColumn('users:user_detail', args=[A('pk')], verbose_name='Username')
    first_name = tables.Column(verbose_name='First Name')
    last_name = tables.Column(verbose_name='Last Name')
    email = tables.EmailColumn(verbose_name='Email')
    is_active = BooleanColumn(verbose_name='Active')
    is_staff = BooleanColumn(verbose_name='Staff')
    is_superuser = BooleanColumn(verbose_name='Superuser')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = User
        fields = ('pk', 'username', 'first_name', 'last_name', 'email', 'is_active', 'is_staff', 'is_superuser', 'actions')
        default_columns = ('pk', 'username', 'first_name', 'last_name', 'email', 'is_active', 'actions')
