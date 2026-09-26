from django.urls import include, path
from rest_framework.routers import DefaultRouter

from geodata.views import (
    EnvironmentalDataViewSet,
    FocusRegionViewSet,
    GeospatialFeatureViewSet,
    RegionViewSet,
)
from riskengine.views import RiskScoreViewSet

from . import views

router = DefaultRouter()
router.register("regions", RegionViewSet, basename="region")
router.register("focus-regions", FocusRegionViewSet, basename="focus-region")
router.register("geospatial-features", GeospatialFeatureViewSet, basename="geospatial-feature")
router.register("environmental-data", EnvironmentalDataViewSet, basename="environmental-data")
router.register("risk-scores", RiskScoreViewSet, basename="risk-score")

urlpatterns = [
    path("health/", views.health_check, name="health-check"),
    path("", include(router.urls)),
]
