import csv

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import Job, StudentResult
from .runner import start_job
from .seamless import parse_rows

CSV_FIELDS = ["name", "linkedin_url", "college", "status", "valid_email", "email_type", "confidence",
              "match_check", "seamless_name", "seamless_company", "seamless_title",
              "guessed_emails", "message", "request_id"]


def index(request):
    jobs = Job.objects.all()[:25]
    return render(request, "finder/index.html", {
        "jobs": jobs, "server_key": bool(settings.SEAMLESS_API_KEY)})


@require_POST
def create_job(request):
    api_key = request.POST.get("api_key", "").strip() or settings.SEAMLESS_API_KEY
    if not api_key:
        messages.error(request, "Enter a Seamless API key, or set SEAMLESS_API_KEY on the server.")
        return redirect("finder:index")

    text = request.POST.get("paste", "")
    f = request.FILES.get("file")
    if f:
        text = f.read().decode("utf-8-sig", errors="replace") + "\n" + text
    college = request.POST.get("college", "").strip()
    people = parse_rows(text, college)
    if not people:
        messages.error(request, "No rows with a LinkedIn profile URL were found.")
        return redirect("finder:index")

    job = Job.objects.create(name=request.POST.get("name", "").strip() or (f.name if f else ""),
                             default_college=college, total=len(people))
    StudentResult.objects.bulk_create([
        StudentResult(job=job, row_no=i + 1, **p) for i, p in enumerate(people)])
    start_job(job.pk, api_key)        # the key is passed to the worker only; it is not saved
    return redirect("finder:job", pk=job.pk)


def job_detail(request, pk):
    job = get_object_or_404(Job, pk=pk)
    return render(request, "finder/job.html", {"job": job, "server_key": bool(settings.SEAMLESS_API_KEY)})


def job_status(request, pk):
    job = get_object_or_404(Job, pk=pk)
    rows = list(job.results.values("id", *CSV_FIELDS))
    return JsonResponse({
        "state": job.state, "total": job.total, "log": job.log,
        "processed": sum(1 for r in rows if r["status"] not in ("pending", "submitted")),
        "found": sum(1 for r in rows if r["valid_email"]),
        "matched": sum(1 for r in rows if r["match_check"] == "linkedin_match"),
        "not_found": sum(1 for r in rows if r["status"] == "not found"),
        "rows": rows,
    })


def job_download(request, pk):
    job = get_object_or_404(Job, pk=pk)
    only_valid = request.GET.get("valid") == "1"
    qs = job.results.all()
    if only_valid:
        qs = qs.exclude(valid_email="")
    resp = HttpResponse(content_type="text/csv")
    suffix = "_valid" if only_valid else ""
    resp["Content-Disposition"] = f'attachment; filename="student_emails_job{job.pk}{suffix}.csv"'
    w = csv.writer(resp)
    w.writerow(CSV_FIELDS)
    for r in qs.values_list(*CSV_FIELDS):
        w.writerow(r)
    return resp


@require_POST
def job_retry(request, pk):
    """Run again the rows that were not found, errored or timed out (for example with a new college name)."""
    job = get_object_or_404(Job, pk=pk)
    api_key = request.POST.get("api_key", "").strip() or settings.SEAMLESS_API_KEY
    if not api_key:
        messages.error(request, "Enter a Seamless API key to retry.")
        return redirect("finder:job", pk=pk)
    if job.state == "running":
        messages.error(request, "This job is still running.")
        return redirect("finder:job", pk=pk)
    qs = job.results.filter(valid_email="").exclude(match_check="linkedin_match")
    college = request.POST.get("college", "").strip()
    if college:
        qs.update(college=college)
    n = qs.update(status="pending", message="", match_check="", guessed_emails="", request_id="")
    if not n:
        messages.info(request, "Nothing to retry.")
        return redirect("finder:job", pk=pk)
    Job.objects.filter(pk=pk).update(state="queued", finished_at=None)
    start_job(job.pk, api_key)
    messages.success(request, f"Retrying {n} students.")
    return redirect("finder:job", pk=pk)


@require_POST
def job_delete(request, pk):
    get_object_or_404(Job, pk=pk).delete()
    messages.success(request, "Job deleted.")
    return redirect("finder:index")
