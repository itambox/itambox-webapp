from django.test import RequestFactory, SimpleTestCase

from assets.views.asset_views import AssetCloneView


class AssetCloneFormContextTests(SimpleTestCase):
    def test_clone_form_receives_request_for_specification_authorization(self):
        request = RequestFactory().get("/assets/assets/1/clone/")
        view = AssetCloneView()
        view.setup(request, pk=1)
        view.object = None

        kwargs = view.get_form_kwargs()

        self.assertIs(kwargs.get("request"), request)
