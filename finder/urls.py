from django.urls import path

from . import views

app_name = "finder"

urlpatterns = [
    path("", views.index, name="index"),
    path("template.xlsx", views.template_xlsx, name="template_xlsx"),
    path("template.csv", views.template_csv, name="template_csv"),
    path("jobs/new/", views.create_job, name="create"),
    path("jobs/<int:pk>/review/", views.review, name="review"),
    path("jobs/<int:pk>/", views.job_detail, name="job"),
    path("jobs/<int:pk>/rows/", views.job_rows, name="rows"),
    path("jobs/<int:pk>/status/", views.job_status, name="status"),
    path("jobs/<int:pk>/download/", views.job_download, name="download"),
    path("jobs/<int:pk>/retry/", views.job_retry, name="retry"),
    path("jobs/<int:pk>/delete/", views.job_delete, name="delete"),
]
