# This file is adapted from NetBox (https://github.com/netbox-community/netbox).
# Copyright (c) DigitalOcean, LLC.
# Licensed under the Apache License, Version 2.0.

import csv
import io
import logging
import yaml

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class BulkImportForm(forms.Form):
    """
    Base form for CSV/TSV or YAML bulk import of objects.
    Subclasses define their model, required fields, optional fields,
    and field mapping logic.

    Usage:
        class AssetBulkImportForm(BulkImportForm):
            model = Asset
            required_fields = ['name', 'asset_tag']
            optional_fields = ['serial_number', 'purchase_date']

            def map_row(self, row):
                return {k: row.get(k, '') for k in self.field_names}
    """

    model = None
    required_fields = []
    optional_fields = []

    active_tab = forms.CharField(
        widget=forms.HiddenInput(),
        initial='upload',
        required=False
    )
    import_format = forms.ChoiceField(
        choices=[('csv', 'CSV'), ('yaml', 'YAML')],
        initial='csv',
        widget=forms.RadioSelect(attrs={'class': 'form-selectgroup-input'}),
        required=False
    )
    csv_file = forms.FileField(
        label=_('File'),
        help_text=_('Upload a CSV or YAML file with headers matching the field names.'),
        widget=forms.FileInput(attrs={'class': 'form-control'}),
        required=False
    )
    import_text = forms.CharField(
        label=_('Direct Data Input'),
        help_text=_('Paste CSV or YAML data matching the field names.'),
        widget=forms.Textarea(attrs={'class': 'form-control font-monospace', 'rows': 8}),
        required=False
    )
    delimiter = forms.ChoiceField(
        label=_('CSV Delimiter'),
        choices=[(',', 'Comma (,)'), ('\t', 'Tab'), (';', 'Semicolon (;)')],
        initial=',',
        widget=forms.Select(attrs={'class': 'form-select'}),
        required=False
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.imported_count = 0
        self.errors_list = []
        self._rows_data = []

    @property
    def field_names(self):
        return list(self.required_fields) + list(self.optional_fields)

    def clean_csv_file(self):
        return self.cleaned_data.get('csv_file')

    def clean(self):
        cleaned_data = super().clean()
        import_format = cleaned_data.get('import_format') or self.data.get('import_format', 'csv')

        csv_file = cleaned_data.get('csv_file')
        import_text = cleaned_data.get('import_text') or ''
        active_tab = cleaned_data.get('active_tab') or self.data.get('active_tab', 'upload')

        # Auto-detect active tab based on which data is provided
        if csv_file:
            active_tab = 'upload'
        elif import_text.strip():
            active_tab = 'editor'

        self._rows_data = []
        raw_data = ""

        if active_tab == 'upload':
            if not csv_file:
                raise ValidationError(_('Please select a file to upload.'))
            try:
                raw_data = csv_file.read().decode('utf-8-sig')
            except UnicodeDecodeError:
                try:
                    csv_file.seek(0)
                    raw_data = csv_file.read().decode('latin-1')
                except Exception:
                    raise ValidationError(_('Unable to decode file. Please upload a valid text-based CSV or YAML file.'))
        else:
            if not import_text.strip():
                raise ValidationError(_('Please paste data in the editor tab.'))
            raw_data = import_text

        if import_format == 'csv':
            delimiter = cleaned_data.get('delimiter') or ','
            try:
                reader = csv.DictReader(io.StringIO(raw_data), delimiter=delimiter)
                rows = list(reader)
            except Exception as e:
                raise ValidationError(_('Failed to parse CSV data: {error}').format(error=str(e)))

            if not rows:
                raise ValidationError(_('CSV data is empty.'))

            headers = set(rows[0].keys())
            headers = {h.strip() if h else '' for h in headers}
            missing_required = [f for f in self.required_fields if f not in headers]
            if missing_required:
                raise ValidationError(
                    _('Missing required columns: {columns}').format(columns=", ".join(missing_required))
                )

            self._rows_data = []
            for row in rows:
                cleaned_row = {k.strip() if k else '': (v.strip() if v else '') for k, v in row.items()}
                self._rows_data.append(cleaned_row)

        elif import_format == 'yaml':
            try:
                parsed_yaml = yaml.safe_load(raw_data)
            except Exception as e:
                raise ValidationError(_('Failed to parse YAML data: {error}').format(error=str(e)))

            if not parsed_yaml:
                raise ValidationError(_('YAML document is empty.'))

            if not isinstance(parsed_yaml, list):
                if isinstance(parsed_yaml, dict):
                    parsed_yaml = [parsed_yaml]
                else:
                    raise ValidationError(_('YAML input must be a list of objects or a single object mapping.'))

            if not parsed_yaml or not isinstance(parsed_yaml[0], dict):
                raise ValidationError(_('YAML data elements must be mappings (key-value pairs).'))

            headers = set(parsed_yaml[0].keys())
            missing_required = [f for f in self.required_fields if f not in headers]
            if missing_required:
                raise ValidationError(
                    _('Missing required fields: {fields}').format(fields=", ".join(missing_required))
                )

            self._rows_data = []
            for row in parsed_yaml:
                cleaned_row = {}
                for k, v in row.items():
                    if k is None:
                        continue
                    key_str = str(k).strip()
                    if v is None:
                        cleaned_row[key_str] = ''
                    elif isinstance(v, bool):
                        cleaned_row[key_str] = str(v)
                    elif isinstance(v, (int, float)):
                        cleaned_row[key_str] = str(v)
                    else:
                        cleaned_row[key_str] = str(v).strip()
                self._rows_data.append(cleaned_row)

        return cleaned_data

    def import_data(self, request=None):
        from django.db import transaction

        if not self.model:
            raise NotImplementedError('BulkImportForm subclass must define a `model` attribute.')

        if not self._rows_data:
            return 0, ['No data to import.']

        imported = 0
        errors = []

        for i, row in enumerate(self._rows_data, start=2):
            try:
                mapped = self.map_row(row)
                self._validate_row(mapped, i)
                
                # Check for primary key to perform UPSERT (in-place update)
                pk_name = self.model._meta.pk.name
                pk_val = mapped.get(pk_name)
                
                if pk_val:
                    try:
                        instance = self.model.objects.get(pk=pk_val)
                        # Perform in-place field updates
                        for key, val in mapped.items():
                            if key != pk_name:
                                setattr(instance, key, val)
                    except self.model.DoesNotExist:
                        # Raise ValidationError matching NetBox gold standard
                        raise ValidationError(
                            _("Object with ID {id} does not exist").format(id=pk_val)
                        )
                else:
                    instance = self._create_instance(mapped)

                if hasattr(instance, 'full_clean'):
                    instance.full_clean()
                instance.save()
                imported += 1
            except ValidationError as e:
                errors.append(f'Row {i}: {"; ".join(e.messages if hasattr(e, "messages") else [str(e)])}')
            except Exception as e:
                logger.exception(f'Import error row {i}')
                errors.append(f'Row {i}: {e}')

        self.imported_count = imported
        self.errors_list = errors
        return imported, errors

    def map_row(self, row):
        """Map CSV row dict to model field values. Override in subclass."""
        # Only map fields that are actually present in the row
        mapped = {k: row[k].strip() for k in self.field_names if k in row and row[k] is not None}
        if self.model:
            pk_name = self.model._meta.pk.name
            pk_val = row.get('id') or row.get(pk_name)
            if pk_val and pk_val.strip():
                mapped[pk_name] = pk_val.strip()
        return mapped

    def _validate_row(self, mapped_data, row_number):
        """Validate a mapped row. Override for custom validation."""
        for field in self.required_fields:
            # When updating an existing object (PK is provided), required fields are not strictly required to be re-supplied
            pk_name = self.model._meta.pk.name if self.model else 'id'
            if pk_name in mapped_data:
                continue
            if not mapped_data.get(field):
                raise ValidationError(f'Row {row_number}: "{field}" is required.')

    def _create_instance(self, mapped_data):
        """Create a model instance from mapped data. Override in subclass."""
        return self.model(**mapped_data)

