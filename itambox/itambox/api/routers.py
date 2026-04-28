from rest_framework.routers import DefaultRouter


class ITAMBoxRouter(DefaultRouter):
    """
    Extend DRF's built-in DefaultRouter to:
    1. Support bulk operations
    2. Alphabetically order endpoints under the root view
    3. Provide contextual view names for breadcrumb trails
    """
    class APIRootView(DefaultRouter.APIRootView):
        _module_name = None

        def get_view_name(self):
            if self._module_name:
                return self._module_name
            return 'API Root'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.routes[0].mapping.update({
            'put': 'bulk_update',
            'patch': 'bulk_partial_update',
            'delete': 'bulk_destroy',
        })

    def get_api_root_view(self, api_urls=None):
        api_root_dict = {}
        list_name = self.routes[0].name
        for prefix, viewset, basename in sorted(self.registry, key=lambda x: x[0]):
            api_root_dict[prefix] = list_name.format(basename=basename)

        root_view_cls = self.APIRootView
        if self.registry:
            viewset = self.registry[0][1]
            model = viewset.queryset.model
            meta = model._meta
            module_name = meta.app_config.verbose_name if meta.app_config else meta.app_label
            root_view_cls = type(
                'AppRootView',
                (self.APIRootView,),
                {'_module_name': module_name},
            )

        return root_view_cls.as_view(api_root_dict=api_root_dict)
