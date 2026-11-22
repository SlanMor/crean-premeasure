from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from jobs import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.home, name="home"),
    path("projects/<int:pk>/", views.project, name="project"),
    path("projects/<int:pk>/setup/", views.setup, name="setup"),
    path("projects/<int:pk>/setup/from/<int:pm>/", views.fill_setup, name="fill_setup"),
    path("projects/<int:pk>/callidus/", views.add_starter, name="add_starter"),
    path("projects/<int:pk>/new/", views.new_premeasure, name="new_premeasure"),
    path("premeasure/<int:pk>/", views.premeasure, name="premeasure"),
    path("premeasure/<int:pk>/write/", views.write, name="write"),
    path("premeasure/<int:pk>/retry/", views.retry, name="retry"),
    path("premeasure/<int:pk>/status/", views.status, name="status"),
    path("premeasure/<int:pk>/preview.png", views.preview, name="preview"),
    path("premeasure/<int:pk>/drawing.pdf", views.drawing, name="drawing"),
    path("premeasure/<int:pk>/download/", views.download, name="download"),
]

admin.site.site_header = "Crean pre-measure"
admin.site.site_title = "Crean pre-measure"
