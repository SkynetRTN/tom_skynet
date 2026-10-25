from django.urls import path

from .views import SkynetTokenView

app_name = "tom_skynet"

urlpatterns = [
    path("token/", SkynetTokenView.as_view(), name="token"),
]
