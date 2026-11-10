from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import ActivityLog, User


class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("FloodScout", {"fields": ("role", "phone", "organisation", "county")}),
    )
    list_display = ("username", "email", "role", "is_staff", "last_login")


admin.site.register(User, UserAdmin)


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ("at", "user", "action", "ip")
    list_filter = ("action",)
    search_fields = ("user__username",)
    readonly_fields = ("at", "user", "action", "detail", "ip")
