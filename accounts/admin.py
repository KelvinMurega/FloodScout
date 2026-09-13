from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (("FloodScout", {"fields": ("role",)}),)
    list_display = ("username", "email", "role", "is_staff")


admin.site.register(User, UserAdmin)
