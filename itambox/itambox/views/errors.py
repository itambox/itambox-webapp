from django.shortcuts import render


class ErrorViewMixin:

    def render_error(self, request, status_code, message=None, exception=None):
        template_name = f'errors/{status_code}.html'
        context = {
            'status_code': status_code,
            'message': message,
            'exception': exception,
        }
        return render(request, template_name, context, status=status_code)


def handler404(request, exception=None):
    return render(request, 'errors/404.html', {'exception': exception}, status=404)


def handler500(request):
    return render(request, 'errors/500.html', status=500)


def handler403(request, exception=None):
    return render(request, 'errors/403.html', {'exception': exception}, status=403)
