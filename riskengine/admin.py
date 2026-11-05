from django.contrib import admin, messages
from django.http import HttpResponseRedirect

from .models import ReportedFlood, RiskOutlook, RiskScore
from .scoring import NoRegionsError, score_regions

RESCORE_ACTION = "rescore_all_regions"


@admin.action(description="Rescore all regions for today")
def rescore_all_regions(modeladmin, request, queryset):
    """Score every region again, as `manage.py score_regions` does.

    The administration site's route to the "generate risk scores" use case
    (Chapter 4, Section 4.3.1). It runs the same function as the command and
    the API's `rescore/` endpoint. The rows ticked in the list are ignored:
    every region is scored, for today.
    """
    if not request.user.is_administrator():
        modeladmin.message_user(
            request, "Only a System Administrator can rescore.", messages.ERROR
        )
        return
    try:
        result = score_regions()
    except NoRegionsError as exc:
        modeladmin.message_user(request, str(exc), messages.ERROR)
        return
    modeladmin.message_user(
        request,
        f"Scored {result.regions} regions for {result.date:%d %B %Y} with "
        f"{result.model_version}: {result.created} new, {result.updated} updated.",
        messages.SUCCESS,
    )


@admin.register(RiskScore)
class RiskScoreAdmin(admin.ModelAdmin):
    list_display = ("region", "prediction_date", "risk_category", "predicted_value", "model_version")
    list_filter = ("risk_category", "model_version", "region__level")
    search_fields = ("region__name", "region__county_name")
    date_hierarchy = "prediction_date"
    list_select_related = ("region",)
    actions = [rescore_all_regions]

    def changelist_view(self, request, extra_context=None):
        # Django runs an action only on ticked rows, and refuses when none
        # is ticked. This one scores every region whatever is ticked, and
        # must also work before any score exists, when there is no row to
        # tick. So it is run here, ahead of that check.
        if request.method == "POST" and request.POST.get("action") == RESCORE_ACTION:
            if not self.has_change_permission(request):
                return super().changelist_view(request, extra_context)
            rescore_all_regions(self, request, RiskScore.objects.none())
            return HttpResponseRedirect(request.get_full_path())
        return super().changelist_view(request, extra_context)


admin.site.register(RiskOutlook)


@admin.register(ReportedFlood)
class ReportedFloodAdmin(admin.ModelAdmin):
    """The counties matched in ReliefWeb flood reports. A wrong match (a
    county named for another reason) can be deleted here; the next fetch
    adds it back only if the report is still in its window, so delete it
    again or narrow the report text rule in geodata/services/reliefweb.py."""

    list_display = ("published", "county", "title", "source")
    list_filter = ("county",)
    search_fields = ("title", "county__name")
    date_hierarchy = "published"
