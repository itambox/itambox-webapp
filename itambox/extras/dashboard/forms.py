from django import forms
from django.utils.translation import gettext_lazy as _
from extras.dashboard.widgets import get_registered_widgets, get_widget, WidgetConfigForm


class DashboardWidgetAddForm(forms.Form):
    widget = forms.ChoiceField(
        label=_('Widget'),
        choices=[],
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    title = forms.CharField(
        label=_('Title'),
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('Default title will be used')})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        widgets = get_registered_widgets()
        self.fields['widget'].choices = [
            (w.widget_id, f"{w.title} — {w.description}")
            for w in sorted(widgets, key=lambda w: w.title)
        ]


class DashboardWidgetConfigForm(forms.Form):
    title = forms.CharField(
        label=_('Title'),
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    visible = forms.BooleanField(
        label=_('Visible'),
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    style = forms.ChoiceField(
        label=_('Header Color Style'),
        choices=[
            ('default', _('Default')),
            ('info', _('Info (Blue)')),
            ('warning', _('Warning (Yellow)')),
            ('success', _('Success (Green)')),
            ('danger', _('Danger (Red)')),
        ],
        initial='default',
        widget=forms.Select(attrs={'class': 'form-select'}),
        required=False
    )

    def __init__(self, *args, widget_id=None, initial_config=None, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.widget_config_form = None
        if widget_id:
            widget_cls = get_widget(widget_id)
            if widget_cls:
                widget_instance = widget_cls(config=initial_config or {})
                self.widget_config_form = widget_instance.get_config_form(
                    data=self.data if self.is_bound else None,
                    request=request
                )

    def is_valid(self):
        valid = super().is_valid()
        if self.widget_config_form and self.widget_config_form.fields:
            valid = valid and self.widget_config_form.is_valid()
        return valid

    def get_widget_config(self):
        if self.widget_config_form and self.widget_config_form.fields:
            return self.widget_config_form.cleaned_data
        return {}
