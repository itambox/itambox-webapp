from django.test import TestCase, RequestFactory
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.urls import reverse
from unittest.mock import patch, MagicMock
from core.models import ReportTemplate, ScheduledReport, ReportGenerationArchive, NotificationChannel
from django_q.models import Schedule

User = get_user_model()

class ScheduledReportingAndAlertsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='reportuser', password='password123', is_superuser=True)
        # Create a report template
        self.template = ReportTemplate.objects.create(
            name='Asset Inventory Test Report',
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=['asset_tag', 'name'],
            include_summary_cards=True
        )

    def test_scheduled_report_validation_cron(self):
        """Test cron expression validation in ScheduledReport."""
        # 1. Valid cron
        report = ScheduledReport(
            name='Test Report 1',
            report=self.template,
            frequency='cron',
            cron_expression='0 8 * * 1-5'
        )
        report.full_clean()  # should not raise
        
        # 2. Invalid cron
        report_invalid = ScheduledReport(
            name='Test Report 2',
            report=self.template,
            frequency='cron',
            cron_expression='invalid_cron'
        )
        with self.assertRaises(ValidationError):
            report_invalid.full_clean()

    def test_scheduled_report_validation_recipients(self):
        """Test email recipients validation."""
        # Invalid email list
        report = ScheduledReport(
            name='Test Report 3',
            report=self.template,
            recipients='invalid_email, another_invalid'
        )
        with self.assertRaises(ValidationError):
            report.full_clean()

        # Valid email list
        report_valid = ScheduledReport(
            name='Test Report 4',
            report=self.template,
            recipients='test@example.com, user2@domain.co.uk'
        )
        report_valid.full_clean()

    def test_schedule_creation_in_view(self):
        """Test that Schedule is created or updated in ScheduledReport form_valid views."""
        self.client.force_login(self.user)
        url = reverse('scheduledreport_add')
        data = {
            'name': 'Active Weekly Report',
            'report': self.template.pk,
            'frequency': 'weekly',
            'format': 'html',
            'start_time': '09:30:00',
            'save_to_archive': True,
            'is_active': True,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        
        # Retrieve created ScheduledReport
        sched = ScheduledReport.objects.get(name='Active Weekly Report')
        self.assertIsNotNone(sched.schedule)
        self.assertEqual(sched.schedule.schedule_type, Schedule.WEEKLY)
        self.assertEqual(sched.schedule.cron, '')
        
        # Verify next run is set and matches the configured time of day
        self.assertEqual(sched.schedule.next_run.time().hour, 9)
        self.assertEqual(sched.schedule.next_run.time().minute, 30)

    @patch('django.core.mail.EmailMessage')
    @patch('requests.post')
    def test_generate_report_task_success(self, mock_post, mock_email_message):
        """Test general execution of report generation task, local archiving and dispatches."""
        # Setup channel
        channel_webhook = NotificationChannel.objects.create(
            name='Test Webhook Channel',
            channel_type=NotificationChannel.TYPE_WEBHOOK,
            enabled=True,
            config={'url': 'http://example.com/webhook'}
        )
        
        # Create a scheduled report with webhook and archive active
        sched = ScheduledReport.objects.create(
            name='Full Task Test Schedule',
            report=self.template,
            frequency='once',
            format='html',
            recipients='',
            save_to_archive=True
        )
        sched.channels.add(channel_webhook)

        # Mock the requests POST response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # Execute task
        from core.tasks import generate_scheduled_report_task
        success = generate_scheduled_report_task(sched.pk)
        
        self.assertTrue(success)
        
        # Check archive entry was created
        sched.refresh_from_db()
        self.assertEqual(sched.last_status, 'success')
        self.assertIsNotNone(sched.last_run)
        
        archive = ReportGenerationArchive.objects.filter(scheduled_report=sched).first()
        self.assertIsNotNone(archive)
        self.assertEqual(archive.status, 'success')
        self.assertIsNotNone(archive.file)
        self.assertEqual(archive.file.mime_type, 'text/html')
        
        # Verify webhook was called
        mock_post.assert_called_once()
