from django.contrib import admin

from .models import Job, StudentResult


class ResultInline(admin.TabularInline):
    model = StudentResult
    extra = 0
    fields = ("row_no", "name", "college", "status", "valid_email", "match_check", "guessed_emails")
    readonly_fields = fields


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "state", "total", "created_at")
    inlines = [ResultInline]


@admin.register(StudentResult)
class StudentResultAdmin(admin.ModelAdmin):
    list_display = ("name", "college", "status", "valid_email", "match_check", "job")
    list_filter = ("status", "match_check", "job")
    search_fields = ("name", "linkedin_url", "valid_email")
