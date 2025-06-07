from rest_framework.routers import DefaultRouter
from . import views

app_name = 'extras_api'

router = DefaultRouter()
router.register(r'tags', views.TagViewSet)

urlpatterns = router.urls
