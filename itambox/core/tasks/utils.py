import logging

logger = logging.getLogger(__name__)

def reverse_job_detail(job_id):
    """
    Helper to resolve job detail URL safely.
    """
    try:
        from django.urls import reverse
        return reverse('job_detail', kwargs={'pk': job_id})
    except Exception:
        return f"/jobs/{job_id}/"
