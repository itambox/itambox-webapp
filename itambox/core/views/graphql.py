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
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from graphql.validation import specified_rules
        from graphene.validation import depth_limit_validator
        
        rules = list(specified_rules)
        # Limit query depth (H4)
        rules.append(depth_limit_validator(max_depth=10))
        
        # Disable introspection in production (H5)
        if not settings.DEBUG:
            from graphql.validation import NoSchemaIntrospectionCustomRule
            rules.append(NoSchemaIntrospectionCustomRule)
            
        self.validation_rules = rules

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
            print("DEBUG: request.user =", request.user)
            print("DEBUG: is_authenticated =", request.user.is_authenticated)
            print("DEBUG: HTTP_AUTHORIZATION =", request.META.get('HTTP_AUTHORIZATION'))
            if not request.user.is_authenticated:
                try:
                    auth_result = TokenAuthentication().authenticate(request)
                    print("DEBUG: auth_result =", auth_result)
                    if auth_result is not None:
                        user, token = auth_result
                        request.user = user
                        request.auth = token
                        # Re-run current user middleware to bind context user
                        CurrentUserMiddleware().process_request(request)
                        # Re-run tenant middleware to set tenant context
                        TenantMiddleware().process_request(request)
                    else:
                        print("DEBUG: TokenAuthentication returned None")
                        return HttpResponse('Unauthorized', status=401)
                except exceptions.AuthenticationFailed as e:
                    print("DEBUG: AuthenticationFailed exception =", e)
                    return JsonResponse({'errors': [{'message': str(e)}]}, status=401)
                except Exception as e:
                    print("DEBUG: Exception in authentication =", e)
                    return JsonResponse({'errors': [{'message': 'Authentication failed'}]}, status=401)

            # Apply DRF rate limiting (H6)
            from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
            throttles = [AnonRateThrottle(), UserRateThrottle()]
            for throttle in throttles:
                if not throttle.allow_request(request, self):
                    return JsonResponse({'errors': [{'message': 'Request was throttled.'}]}, status=429)
                    
        return super().dispatch(request, *args, **kwargs)
