from django.test import TestCase, override_settings
from django.core.exceptions import ValidationError
from extras.models import Tag, ConfigContext

class CustomValidatorTests(TestCase):
    @override_settings(CUSTOM_VALIDATORS={
        'extras.Tag': [
            {
                'field': 'name',
                'regex': r'^TAG-[A-Z]{3}-\d{4}$',
                'error_message': "Tag name must match TAG-XXX-1234 convention."
            }
        ]
    })
    def test_regex_validation_failure(self):
        tag = Tag(name="InvalidTag", slug="invalid-tag")
        with self.assertRaises(ValidationError) as ctx:
            tag.save()
        
        self.assertIn('name', ctx.exception.error_dict)
        self.assertEqual(
            ctx.exception.error_dict['name'][0].message,
            "Tag name must match TAG-XXX-1234 convention."
        )

    @override_settings(CUSTOM_VALIDATORS={
        'extras.Tag': [
            {
                'field': 'name',
                'regex': r'^TAG-[A-Z]{3}-\d{4}$',
                'error_message': "Tag name must match TAG-XXX-1234 convention."
            }
        ]
    })
    def test_regex_validation_success(self):
        tag = Tag(name="TAG-NYC-1234", slug="tag-nyc-1234")
        try:
            tag.save()
        except ValidationError:
            self.fail("ValidationError raised unexpectedly for valid name pattern")

    @override_settings(CUSTOM_VALIDATORS={
        'extras.ConfigContext': [
            {
                'field': 'weight',
                'min_value': 10,
                'max_value': 200,
            }
        ]
    })
    def test_numerical_bounds_min_failure(self):
        config = ConfigContext(name="Test Config", data={"foo": "bar"}, weight=5)
        with self.assertRaises(ValidationError) as ctx:
            config.save()
        
        self.assertIn('weight', ctx.exception.error_dict)
        self.assertEqual(
            ctx.exception.error_dict['weight'][0].message,
            "Must be at least 10."
        )

    @override_settings(CUSTOM_VALIDATORS={
        'extras.ConfigContext': [
            {
                'field': 'weight',
                'min_value': 10,
                'max_value': 200,
            }
        ]
    })
    def test_numerical_bounds_max_failure(self):
        config = ConfigContext(name="Test Config", data={"foo": "bar"}, weight=250)
        with self.assertRaises(ValidationError) as ctx:
            config.save()
        
        self.assertIn('weight', ctx.exception.error_dict)
        self.assertEqual(
            ctx.exception.error_dict['weight'][0].message,
            "Cannot exceed 200."
        )

    @override_settings(CUSTOM_VALIDATORS={
        'extras.ConfigContext': [
            {
                'field': 'weight',
                'min_value': 10,
                'max_value': 200,
            }
        ]
    })
    def test_numerical_bounds_success(self):
        config = ConfigContext(name="Test Config", data={"foo": "bar"}, weight=100)
        try:
            config.save()
        except ValidationError:
            self.fail("ValidationError raised unexpectedly for valid bounds")

    @override_settings(CUSTOM_VALIDATORS={
        'extras.Tag': [
            {
                'field': 'name',
                'regex': r'^TAG-[A-Z]{3}-\d{4}$',
                'error_message': "Tag name must match TAG-XXX-1234 convention."
            },
            {
                'field': 'description',
                'regex': r'^[a-zA-Z\s]+$',
                'error_message': "Description must only contain letters and spaces."
            }
        ]
    })
    def test_multiple_fields_failures(self):
        tag = Tag(name="InvalidTag", slug="invalid-tag", description="12345")
        with self.assertRaises(ValidationError) as ctx:
            tag.save()
        
        self.assertIn('name', ctx.exception.error_dict)
        self.assertIn('description', ctx.exception.error_dict)
        
        self.assertEqual(
            ctx.exception.error_dict['name'][0].message,
            "Tag name must match TAG-XXX-1234 convention."
        )
        self.assertEqual(
            ctx.exception.error_dict['description'][0].message,
            "Description must only contain letters and spaces."
        )
