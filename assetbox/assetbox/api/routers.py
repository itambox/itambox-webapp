from rest_framework.routers import DefaultRouter

class AssetBoxRouter(DefaultRouter):
    """
        Custom router for the AssetBox API.
        (Currently just inherits DefaultRouter, can add NetBox customizations later)
    """
    def get_api_root_view(self, api_urls=None):
        # Get the original view function
        root_view_func = super().get_api_root_view(api_urls=api_urls)

        # Define a wrapper that modifies the response context
        def view_wrapper(request, *args, **kwargs):
            # print(f"--- Entering view_wrapper for {request.resolver_match.namespace} ---") # DEBUG
            response = root_view_func(request, *args, **kwargs)
            # Ensure the context used by the template has the correct name
            if hasattr(response, 'renderer_context') and response.renderer_context is not None:
                # Extract app label from namespace for a better default name
                app_label = request.resolver_match.namespace.replace('-api', '').title()
                new_name = f"{app_label} API"
                # print(f"  Setting renderer_context['name'] to: {new_name}") # DEBUG
                response.renderer_context['name'] = new_name
                # print(f"  Current response context keys: {response.renderer_context.keys()}") # DEBUG
            # else:
                # print("  Response has no renderer_context or it is None") # DEBUG
            return response

        # Return the wrapped view function
        return view_wrapper 