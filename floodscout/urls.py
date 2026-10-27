from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from .admin_login import limited_login

urlpatterns = [
    # Before admin.site.urls, so its login/ is this one.
    path(f"{settings.ADMIN_PATH}login/", limited_login, name="admin-login"),
    path(settings.ADMIN_PATH, admin.site.urls),
    path("api/", include("api.urls")),
]
