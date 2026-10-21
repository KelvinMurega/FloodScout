from django.contrib import admin

from .models import Alert, AlertRun, Delivery, Subscriber


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = (
        "headline",
        "level",
        "status",
        "basis",
        "onset",
        "expires",
        "version",
        "issued_at",
    )
    list_filter = ("status", "level", "basis")
    search_fields = ("headline", "region__name", "region__county_name")
    readonly_fields = ("identifier", "issued_at", "updated_at")


@admin.register(Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    list_display = ("__str__", "min_level", "confirmed", "created_at")
    list_filter = ("confirmed", "min_level")
    # The code and token are not shown: they would let an administrator act
    # as the subscriber.
    exclude = ("confirm_code", "token")
    filter_horizontal = ("regions",)


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("alert", "channel", "level", "kind", "outcome", "sent_at")
    list_filter = ("channel", "outcome", "kind")


@admin.register(AlertRun)
class AlertRunAdmin(admin.ModelAdmin):
    list_display = ("started_at", "scores_for", "issued", "upgraded", "ended", "messages_sent")
