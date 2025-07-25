import csv
import io
import logging

from django import forms
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)


class BulkImportForm(forms.Form):
    """
    Base form for CSV/TSV bulk import of objects.
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

    csv_file = forms.FileField(
        label='CSV File',
        help_text='Upload a CSV file with headers matching the field names.',
        widget=forms.FileInput(attrs={'class': 'form-control'})
    )
    delimiter = forms.ChoiceField(
        choices=[(',', 'Comma (,)'), ('\t', 'Tab'), (';', 'Semicolon (;)')],
        initial=',',
        widget=forms.Select(attrs={'class': 'form-select'})
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
        csv_file = self.cleaned_data.get('csv_file')
        if not csv_file:
            return None

        delimiter = self.cleaned_data.get('delimiter', ',')
        try:
            data = csv_file.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                csv_file.seek(0)
                data = csv_file.read().decode('latin-1')
            except Exception:
                raise ValidationError('Unable to decode file. Please upload a valid CSV file.')

        reader = csv.DictReader(io.StringIO(data), delimiter=delimiter)
        rows = list(reader)

        if not rows:
            raise ValidationError('CSV file is empty.')

        headers = set(rows[0].keys())
        missing_required = [f for f in self.required_fields if f not in headers]
        if missing_required:
            raise ValidationError(
                f'Missing required columns: {", ".join(missing_required)}'
            )

        self._rows_data = rows
        return csv_file

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
        return {k: row.get(k, '').strip() for k in self.field_names}

    def _validate_row(self, mapped_data, row_number):
        """Validate a mapped row. Override for custom validation."""
        for field in self.required_fields:
            if not mapped_data.get(field):
                raise ValidationError(f'Row {row_number}: "{field}" is required.')

    def _create_instance(self, mapped_data):
        """Create a model instance from mapped data. Override in subclass."""
        return self.model(**mapped_data)
