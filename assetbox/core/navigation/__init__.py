from collections.abc import Sequence
from dataclasses import dataclass, field

from django.urls import reverse_lazy

__all__ = (
    'Menu',
    'MenuGroup',
    'MenuItem',
    'MenuItemButton',
    'get_model_buttons',
    'get_model_item',
)


@dataclass
class MenuItemButton:

    link: str
    title: str
    icon_class: str
    _url: str | None = field(default=None, init=False)
    permissions: Sequence[str] = ()
    color: str | None = None

    def __post_init__(self):
        if self.link:
            self._url = reverse_lazy(self.link)

    @property
    def url(self):
        return self._url


@dataclass
class MenuItem:

    link: str
    link_text: str
    _url: str | None = field(default=None, init=False)
    permissions: Sequence[str] = ()
    auth_required: bool = False
    staff_only: bool = False
    buttons: Sequence[MenuItemButton] = ()

    def __post_init__(self):
        if self.link:
            self._url = reverse_lazy(self.link)

    @property
    def url(self):
        return self._url


@dataclass
class MenuGroup:

    label: str
    items: Sequence[MenuItem]


@dataclass
class Menu:

    label: str
    icon_class: str
    groups: Sequence[MenuGroup]

    @property
    def name(self):
        return self.label.replace(' ', '_').replace('&', '')


def get_model_item(app_label, model_name, label, actions=('add',)):
    return MenuItem(
        link=f'{app_label}:{model_name}_list',
        link_text=label,
        permissions=[f'{app_label}.view_{model_name}'],
        buttons=get_model_buttons(app_label, model_name, actions)
    )


def get_model_buttons(app_label, model_name, actions=('add',)):
    buttons = []

    if 'add' in actions:
        buttons.append(
            MenuItemButton(
                link=f'{app_label}:{model_name}_create',
                title='Add',
                icon_class='mdi mdi-plus-thick',
                permissions=[f'{app_label}.add_{model_name}']
            )
        )

    return buttons
