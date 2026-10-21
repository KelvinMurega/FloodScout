from django.urls import include, path
from rest_framework.routers import DefaultRouter

from accounts.views import ObtainTokenView, RevokeTokenView
from geodata.views import (
    EnvironmentalDataViewSet,
    FloodEventViewSet,
    FocusRegionViewSet,
    GeospatialFeatureViewSet,
    RegionViewSet,
)
from riskengine.views import RiskGridViewSet, RiskOutlookViewSet, RiskScoreViewSet, admin_analytics

from . import views

router = DefaultRouter()
router.register("regions", RegionViewSet, basename="region")
router.register("focus-regions", FocusRegionViewSet, basename="focus-region")
router.register("geospatial-features", GeospatialFeatureViewSet, basename="geospatial-feature")
router.register("environmental-data", EnvironmentalDataViewSet, basename="environmental-data")
router.register("risk-scores", RiskScoreViewSet, basename="risk-score")
router.register("risk-outlook", RiskOutlookViewSet, basename="risk-outlook")
router.register("risk-grid", RiskGridViewSet, basename="risk-grid")
router.register("flood-events", FloodEventViewSet, basename="flood-event")

urlpatterns = [
    path("health/", views.health_check, name="health-check"),
    path("auth/token/", ObtainTokenView.as_view(), name="auth-token"),
    path("auth/logout/", RevokeTokenView.as_view(), name="auth-logout"),
    path("admin/analytics/", admin_analytics, name="admin-analytics"),
    path("", include(router.urls)),
]
