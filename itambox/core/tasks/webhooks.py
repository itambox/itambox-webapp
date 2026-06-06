import hmac
import hashlib
import json
import logging
import requests

logger = logging.getLogger(__name__)

def send_webhook_task(url, method, headers, secret, event_action, event_model_app_label, event_model_name, event_object_id, event_timestamp_iso, event_data):
    """
    Asynchronously dispatches webhook events to external endpoints like Slack, Microsoft Teams,
    or custom HTTP webhook URLs. Raises exceptions to trigger django-q retries if dispatch fails.
    """
    # 1. Dispatch to Slack
    if 'hooks.slack.com' in url:
        payload = {
            'text': f"Event: {event_action} on {event_model_name} (ID: {event_object_id})"
        }
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info("Slack notification sent successfully — status %s", response.status_code)
            return
        except requests.RequestException as e:
            logger.error("Slack notification failed: %s", e)
            raise

    # 2. Dispatch to Microsoft Teams
    if 'webhook.office.com' in url or 'outlook.office.com/webhook' in url:
        payload = {
            '@type': 'MessageCard',
            '@context': 'https://schema.org/extensions',
            'summary': f"Event: {event_action} on {event_model_name} (ID: {event_object_id})",
            'themeColor': '0076D7',
            'title': 'ITAMbox Notification',
            'text': f"Event: {event_action} on {event_model_name} (ID: {event_object_id})",
        }
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info("Teams notification sent successfully — status %s", response.status_code)
            return
        except requests.RequestException as e:
            logger.error("Teams notification failed: %s", e)
            raise

    # 3. Custom Webhook Endpoint
    payload = {
        'event': event_action,
        'model': f"{event_model_app_label}.{event_model_name}",
        'object_id': event_object_id,
        'timestamp': event_timestamp_iso,
        'data': event_data,
    }

    body = json.dumps(payload, default=str)

    if secret:
        signature = hmac.new(
            secret.encode('utf-8'),
            body.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        headers['X-Hub-Signature-256'] = f'sha256={signature}'

    headers.setdefault('Content-Type', 'application/json')

    try:
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            data=body,
            timeout=10,
        )
        response.raise_for_status()
        logger.info("Webhook sent to %s — status %s", url, response.status_code)
    except requests.RequestException as e:
        logger.error("Webhook to %s failed: %s", url, e)
        raise
