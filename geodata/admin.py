from django.contrib import admin

from .models import EnvironmentalData, GeospatialFeature, Region

admin.site.register(Region)
admin.site.register(GeospatialFeature)
admin.site.register(EnvironmentalData)
