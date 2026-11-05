from django.urls import include, path
from rest_framework.routers import DefaultRouter

from accounts.views import (
    ActivityAdminView,
    HomeLocateView,
    HomeSearchView,
    HomeView,
    MeView,
    MyActivityView,
    MyAlertsView,
    MySubscriptionsView,
    MySubscriptionView,
    ObtainTokenView,
    PasswordResetConfirmView,
    PasswordResetView,
    PasswordView,
    PlacesView,
    PlaceView,
    RegisterView,
    RevokeTokenView,
    SocialAccountsView,
    SocialAccountView,
    SocialCallbackView,
    SocialCompleteView,
    SocialProvidersView,
    SocialStartView,
    UserAdminViewSet,
    VerifyContactView,
)
from alerts.views import AlertViewSet, confirm, subscribe, subscribe_me, unsubscribe
from geodata.views import (
    EnvironmentalDataViewSet,
    FloodEventViewSet,
    FocusRegionViewSet,
    GeospatialFeatureViewSet,
    KmdWarningViewSet,
    RegionViewSet,
)
from riskengine.views import (
    RiskForecastViewSet,
    RiskGridViewSet,
    RiskOutlookViewSet,
    RiskScoreViewSet,
    admin_analytics,
    situation_briefing,
)

from . import views

router = DefaultRouter()
router.register("regions", RegionViewSet, basename="region")
router.register("focus-regions", FocusRegionViewSet, basename="focus-region")
router.register("geospatial-features", GeospatialFeatureViewSet, basename="geospatial-feature")
router.register("environmental-data", EnvironmentalDataViewSet, basename="environmental-data")
router.register("risk-scores", RiskScoreViewSet, basename="risk-score")
router.register("risk-outlook", RiskOutlookViewSet, basename="risk-outlook")
router.register("risk-grid", RiskGridViewSet, basename="risk-grid")
router.register("risk-forecast", RiskForecastViewSet, basename="risk-forecast")
router.register("alerts", AlertViewSet, basename="alert")
router.register("admin/users", UserAdminViewSet, basename="admin-user")
router.register("flood-events", FloodEventViewSet, basename="flood-event")
router.register("kmd-warnings", KmdWarningViewSet, basename="kmd-warning")

urlpatterns = [
    path("health/", views.health_check, name="health-check"),
    path("auth/token/", ObtainTokenView.as_view(), name="auth-token"),
    path("auth/logout/", RevokeTokenView.as_view(), name="auth-logout"),
    path("auth/register/", RegisterView.as_view(), name="auth-register"),
    path("auth/me/", MeView.as_view(), name="auth-me"),
    path("auth/password/", PasswordView.as_view(), name="auth-password"),
    path("auth/me/activity/", MyActivityView.as_view(), name="auth-my-activity"),
    path("auth/me/home/", HomeView.as_view(), name="auth-my-home"),
    path("auth/me/home/search/", HomeSearchView.as_view(), name="auth-my-home-search"),
    path("auth/me/home/locate/", HomeLocateView.as_view(), name="auth-my-home-locate"),
    path("auth/me/places/", PlacesView.as_view(), name="auth-my-places"),
    path("auth/me/places/<int:place_id>/", PlaceView.as_view(), name="auth-my-place"),
    path("auth/verify/", VerifyContactView.as_view(), name="auth-verify"),
    path("auth/me/alerts/", MyAlertsView.as_view(), name="auth-my-alerts"),
    path("auth/me/subscriptions/", MySubscriptionsView.as_view(), name="auth-my-subscriptions"),
    path(
        "auth/me/subscriptions/<int:pk>/",
        MySubscriptionView.as_view(),
        name="auth-my-subscription",
    ),
    path("admin/activity/", ActivityAdminView.as_view(), name="admin-activity"),
    path("auth/password-reset/", PasswordResetView.as_view(), name="auth-password-reset"),
    path(
        "auth/password-reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="auth-password-reset-confirm",
    ),
    path("auth/social/providers/", SocialProvidersView.as_view(), name="social-providers"),
    path("auth/social/accounts/", SocialAccountsView.as_view(), name="social-accounts"),
    path("auth/social/accounts/<int:pk>/", SocialAccountView.as_view(), name="social-account"),
    path("auth/social/complete/", SocialCompleteView.as_view(), name="social-complete"),
    path("auth/social/<str:provider>/start/", SocialStartView.as_view(), name="social-start"),
    path(
        "auth/social/<str:provider>/callback/",
        SocialCallbackView.as_view(),
        name="social-callback",
    ),
    path("admin/analytics/", admin_analytics, name="admin-analytics"),
    path("briefing/", situation_briefing, name="briefing"),
    # Before the router, whose alerts/<pk>/ would otherwise take these names.
    path("alerts/subscribe/", subscribe, name="alerts-subscribe"),
    path("alerts/subscribe/me/", subscribe_me, name="alerts-subscribe-me"),
    path("alerts/confirm/", confirm, name="alerts-confirm"),
    path("alerts/unsubscribe/<str:token>/", unsubscribe, name="alerts-unsubscribe"),
    path("", include(router.urls)),
]
