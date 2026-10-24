from django.contrib import admin

from .models import SkynetProfile


@admin.register(SkynetProfile)
class SkynetProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "updated_at")
    search_fields = ("user__username",)
