from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from urllib.parse import quote
from graphene_django.views import GraphQLView
from rest_framework import exceptions

from itambox.api.authentication import TokenAuthentication
from itambox.middleware import TenantMiddleware, CurrentUserMiddleware


@method_decorator(csrf_exempt, name='dispatch')
class PrivateGraphQLView(GraphQLView):
    @property
    def graphiql(self):
        return settings.DEBUG

    @graphiql.setter
    def graphiql(self, value):
        pass

    def dispatch(self, request, *args, **kwargs):
        if request.method == 'GET':
            if not request.user.is_authenticated:
                from django.shortcuts import resolve_url
                return redirect(f"{resolve_url(settings.LOGIN_URL)}?next={quote(request.get_full_path())}")
        
        elif request.method == 'POST':
            if not request.user.is_authenticated:
                try:
                    auth_result = TokenAuthentication().authenticate(request)
                    if auth_result is not None:
                        user, token = auth_result
                        request.user = user
                        request.auth = token
                        # Re-run current user middleware to bind context user
                        CurrentUserMiddleware().process_request(request)
                        # Re-run tenant middleware to set tenant context
                        TenantMiddleware().process_request(request)
                    else:
                        return HttpResponse('Unauthorized', status=401)
                except exceptions.AuthenticationFailed as e:
                    return JsonResponse({'errors': [{'message': str(e)}]}, status=401)
                except Exception as e:
                    return JsonResponse({'errors': [{'message': 'Authentication failed'}]}, status=401)
                    
        return super().dispatch(request, *args, **kwargs)
