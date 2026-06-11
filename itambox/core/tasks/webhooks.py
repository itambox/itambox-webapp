import hmac
import hashlib
import json
import logging
import requests
from django_q.tasks import async_task

logger = logging.getLogger(__name__)


def send_webhook_task(url, method, headers, secret, event_action, event_model_app_label,
                      event_model_name, event_object_id, event_timestamp_iso, event_data,
                      attempt=0, retry_count=3, retry_backoff=60):
    """Dispatch a webhook event. Retries on 5xx and connection errors; 4xx are final."""
    try:
        if 'hooks.slack.com' in url:
            payload = {'text': f"Event: {event_action} on {event_model_name} (ID: {event_object_id})"}
            response = requests.post(url, json=payload, timeout=10)
        elif 'webhook.office.com' in url or 'outlook.office.com/webhook' in url:
            payload = {
                '@type': 'MessageCard',
                '@context': 'https://schema.org/extensions',
                'summary': f"Event: {event_action} on {event_model_name} (ID: {event_object_id})",
                'themeColor': '0076D7',
                'title': 'ITAMbox Notification',
                'text': f"Event: {event_action} on {event_model_name} (ID: {event_object_id})",
            }
            response = requests.post(url, json=payload, timeout=10)
        else:
            payload = {
                'event': event_action,
                'model': f"{event_model_app_label}.{event_model_name}",
                'object_id': event_object_id,
                'timestamp': event_timestamp_iso,
                'data': event_data,
            }
            body = json.dumps(payload, default=str)
            req_headers = dict(headers)
            if secret:
                sig = hmac.new(
                    secret.encode('utf-8'),
                    body.encode('utf-8'),
                    hashlib.sha256,
                ).hexdigest()
                req_headers['X-Hub-Signature-256'] = f'sha256={sig}'
            req_headers.setdefault('Content-Type', 'application/json')
            response = requests.request(method=method, url=url, headers=req_headers, data=body, timeout=10)

        if 400 <= response.status_code < 500:
            logger.warning("Webhook %s returned %s — not retrying (4xx is final)", url, response.status_code)
            return
        response.raise_for_status()
        logger.info("Webhook sent to %s — status %s", url, response.status_code)

    except requests.RequestException as exc:
        if attempt < retry_count:
            logger.warning(
                "Webhook %s failed (attempt %d/%d): %s — retrying immediately",
                url, attempt + 1, retry_count, exc,
            )
            # Retry is immediate; retry_backoff is stored for future use with a scheduler.
            async_task(
                'core.tasks.send_webhook_task',
                url=url, method=method, headers=headers, secret=secret,
                event_action=event_action, event_model_app_label=event_model_app_label,
                event_model_name=event_model_name, event_object_id=event_object_id,
                event_timestamp_iso=event_timestamp_iso, event_data=event_data,
                attempt=attempt + 1, retry_count=retry_count, retry_backoff=retry_backoff,
            )
        else:
            logger.error("Webhook %s: all %d attempts failed: %s", url, retry_count, exc)
