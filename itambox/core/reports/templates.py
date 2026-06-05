import os
from django.conf import settings

def get_polished_system_html_template():
    """
    Returns the highly-polished, HTML no-code template.
    Includes print stylesheets and inline visual elements.
    """
    path = os.path.join(settings.BASE_DIR, 'templates', 'core', 'reports', 'polished_report.html')
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()
