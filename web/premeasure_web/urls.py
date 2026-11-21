from django.contrib import admin
from django.urls import path

from jobs import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("admin/", admin.site.urls),
    path("", views.home, name="home"),
]

admin.site.site_header = "Crean pre-measure"
admin.site.site_title = "Crean pre-measure"
