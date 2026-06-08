import json
from unittest.mock import patch, MagicMock
from django.test import TransactionTestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError

from core.models import Job, Notification, AlertRule, AlertLog, NotificationChannel
from core.tasks import import_csv_task, evaluate_alert_rules_task
from assets.models import Asset, StatusLabel, AssetRole, Manufacturer, AssetType
from subscriptions.models import Subscription

User = get_user_model()

class TasksTestCase(TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="task_user", password="password")
        self.tenant = None  # test with no tenant or default tenant

    @patch('itambox.views.generic.ObjectImportView.get_form_class')
    def test_import_csv_task_success(self, mock_get_form_class):
        # Mocking ObjectImportView and the Form
        mock_form = MagicMock()
        mock_form.import_data.return_value = (5, [])  # 5 success, 0 errors
        mock_get_form_class.return_value = lambda: mock_form

        job = Job.objects.create(name="Import Job", status=Job.STATUS_PENDING)

        import_csv_task(
            job_id=job.pk,
            rows_data=[{"name": "Mfr1"}, {"name": "Mfr2"}],
            app_label="assets",
            model_name="manufacturer",
            user_id=self.user.pk
        )

        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_COMPLETED)
        self.assertEqual(job.result['imported'], 5)
        
        # Verify notification was created
        notification = Notification.objects.filter(user=self.user, level=Notification.LEVEL_SUCCESS).first()
        self.assertIsNotNone(notification)
        self.assertIn("Successfully imported 5 record(s)", notification.message)

    @patch('itambox.views.generic.ObjectImportView.get_form_class')
    def test_import_csv_task_failed(self, mock_get_form_class):
        mock_form = MagicMock()
        mock_form.import_data.return_value = (0, ["Row 1: invalid name", "Row 2: missing field"])
        mock_get_form_class.return_value = lambda: mock_form

        job = Job.objects.create(name="Import Job Failed", status=Job.STATUS_PENDING)

        import_csv_task(
            job_id=job.pk,
            rows_data=[{"name": ""}],
            app_label="assets",
            model_name="manufacturer",
            user_id=self.user.pk
        )

        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_FAILED)
        
        # Verify failure notification
        notification = Notification.objects.filter(user=self.user, level=Notification.LEVEL_DANGER).first()
        self.assertIsNotNone(notification)
        self.assertIn("Failed to import CSV/YAML data", notification.message)

    def test_evaluate_alert_rules_task_low_stock(self):
        # Create an AlertRule for low stock
        rule = AlertRule.objects.create(
            name="Low Stock Alert Rule",
            alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=5,
            is_active=True
        )

        # Create accessory under threshold
        mfr = Manufacturer.objects.create(name="HP", slug="hp")
        from inventory.models import Accessory, AccessoryStock
        from organization.models import Location, Site
        
        accessory = Accessory.objects.create(
            name="HP Mouse",
            slug="hp-mouse",
            manufacturer=mfr,
            min_qty=3  # specific threshold
        )
        
        site = Site.objects.create(name="Stock Site", slug="stock-site")
        location = Location.objects.create(name="Stock Room", slug="stock-room", site=site)
        
        # Stock has 2 mouse units (below 3 threshold)
        AccessoryStock.objects.create(accessory=accessory, location=location, qty=2)

        # Run evaluation
        evaluate_alert_rules_task()

        # Assert AlertLog created
        alert_log = AlertLog.objects.filter(rule=rule).first()
        self.assertIsNotNone(alert_log)
        self.assertEqual(alert_log.status, AlertLog.STATUS_ACTIVE)
        self.assertIn("HP Mouse", alert_log.subject)
