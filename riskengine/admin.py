from django.contrib import admin

from .models import RiskOutlook, RiskScore

admin.site.register(RiskScore)
admin.site.register(RiskOutlook)
