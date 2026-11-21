from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect


def health(request):
    """Used by deploy checks: confirms the app and database respond."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


def home(request):
    if request.user.is_authenticated:
        return redirect("admin:index")
    return HttpResponse("Crean pre-measure", content_type="text/plain")
