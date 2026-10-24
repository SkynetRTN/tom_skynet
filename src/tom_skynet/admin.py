from django.contrib import admin

from .models import SkynetProfile, SkynetRecordState


@admin.register(SkynetProfile)
class SkynetProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "updated_at")
    search_fields = ("user__username",)


@admin.register(SkynetRecordState)
class SkynetRecordStateAdmin(admin.ModelAdmin):
    list_display = ("record", "products_final", "updated_at")
    list_filter = ("products_final",)
