import csv

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import Job, StudentResult
from .runner import start_job
from .seamless import (TEMPLATE_HEADERS, build_template_xlsx, college_unclear, looks_like_college,
                       parse_rows, rows_from_text, rows_from_upload)

CSV_FIELDS = ["name", "linkedin_url", "college", "status", "valid_email", "email_type", "confidence",
              "match_check", "seamless_name", "seamless_company", "seamless_title",
              "guessed_emails", "message", "request_id"]
TABLE_LIMIT = 500


def _key_missing(request, target):
    if settings.SEAMLESS_API_KEY:
        return None
    messages.error(request, "SEAMLESS_API_KEY is missing. Add it to the .env file and restart the server.")
    return redirect(*target)


def index(request):
    jobs = Job.objects.annotate(
        found_n=Count("results", filter=~Q(results__valid_email="")))[:25]
    return render(request, "finder/index.html", {
        "jobs": jobs, "server_key": bool(settings.SEAMLESS_API_KEY)})


# ---------------------------------------------------------------- upload -> review -> start

@require_POST
def create_job(request):
    f = request.FILES.get("file")
    rows = rows_from_text(request.POST.get("paste", ""))
    if f:
        try:
            rows = rows_from_upload(f.name, f.read()) + rows
        except Exception as e:  # noqa: BLE001
            messages.error(request, f"Could not read {f.name}: {e}")
            return redirect("finder:index")
    college = request.POST.get("college", "").strip()
    people = parse_rows(rows, college)
    if not people:
        messages.error(request, "No rows with a LinkedIn profile URL (linkedin.com/in/...) were found "
                                "in the file or the pasted text.")
        return redirect("finder:index")

    swapped = sum(1 for p in people if looks_like_college(p["name"]) and not looks_like_college(p["college"]))
    if swapped > len(people) / 2:
        messages.error(request, "The Student Name column holds college names, so the columns look swapped. "
                                "Download the template, put the data in the right columns and upload again.")
        return redirect("finder:index")

    job = Job.objects.create(name=request.POST.get("name", "").strip() or (f.name if f else ""),
                             default_college=college, total=len(people), state="draft")
    StudentResult.objects.bulk_create(
        [StudentResult(job=job, row_no=i + 1, **p) for i, p in enumerate(people)], batch_size=1000)
    return redirect("finder:review", pk=job.pk)


def review(request, pk):
    job = get_object_or_404(Job, pk=pk)
    if job.state != "draft":
        return redirect("finder:job", pk=pk)

    groups = list(job.results.values("college").annotate(n=Count("id")).order_by("-n", "college"))
    for i, g in enumerate(groups):
        g["idx"] = i
        g["unclear"] = college_unclear(g["college"])

    if request.method == "POST":
        if (resp := _key_missing(request, ("finder:review", pk))):
            return resp
        keep_total = 0
        ids = {g["idx"]: list(job.results.filter(college=g["college"]).values_list("id", flat=True))
               for g in groups}      # fixed before any rename, so renames cannot mix groups
        for g in groups:
            rows = StudentResult.objects.filter(id__in=ids[g["idx"]])
            if request.POST.get(f"use_{g['idx']}") != "on":
                rows.delete()
                continue
            new_name = request.POST.get(f"name_{g['idx']}", "").strip()
            if new_name and new_name != g["college"]:
                rows.update(college=new_name[:200])
            keep_total += g["n"]
        if not keep_total:
            messages.error(request, "Select at least one college.")
            return redirect("finder:review", pk=pk)
        Job.objects.filter(pk=pk).update(total=keep_total, state="queued")
        start_job(job.pk, settings.SEAMLESS_API_KEY)
        messages.success(request, f"Started {keep_total} students.")
        return redirect("finder:job", pk=pk)

    return render(request, "finder/review.html", {
        "job": job, "groups": groups, "unclear_n": sum(g["n"] for g in groups if g["unclear"]),
        "server_key": bool(settings.SEAMLESS_API_KEY)})


# ---------------------------------------------------------------- job page + data

def job_detail(request, pk):
    job = get_object_or_404(Job, pk=pk)
    if job.state == "draft":
        return redirect("finder:review", pk=pk)
    return render(request, "finder/job.html", {"job": job, "server_key": bool(settings.SEAMLESS_API_KEY)})


def job_status(request, pk):
    job = get_object_or_404(Job, pk=pk)
    qs = job.results.all()
    agg = qs.aggregate(
        processed=Count("id", filter=~Q(status__in=["pending", "submitted"])),
        found=Count("id", filter=~Q(valid_email="")),
        matched=Count("id", filter=Q(match_check="linkedin_match")),
        not_found=Count("id", filter=Q(status="not found")),
    )
    by_college = list(qs.values("college").annotate(
        total=Count("id"),
        processed=Count("id", filter=~Q(status__in=["pending", "submitted"])),
        found=Count("id", filter=~Q(valid_email="")),
    ).order_by("-total"))
    log_lines = job.log.splitlines()
    return JsonResponse({"state": job.state, "total": job.total, "log": "\n".join(log_lines[-300:]),
                         "by_college": by_college, **agg})


def job_rows(request, pk):
    """Filtered rows for the table. f = all|valid|guess|nf|check, q = search, c = college."""
    job = get_object_or_404(Job, pk=pk)
    qs = job.results.all()
    f, q, c = request.GET.get("f", "all"), request.GET.get("q", "").strip(), request.GET.get("c", "")
    if c:
        qs = qs.filter(college=c)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(valid_email__icontains=q) | Q(linkedin_url__icontains=q))
    if f == "valid":
        qs = qs.exclude(valid_email="")
    elif f == "guess":
        qs = qs.filter(valid_email="").exclude(guessed_emails="")
    elif f == "nf":
        qs = qs.filter(status="not found")
    elif f == "check":
        qs = qs.exclude(match_check="").exclude(match_check="linkedin_match")
    total = qs.count()
    return JsonResponse({"total": total, "limit": TABLE_LIMIT,
                         "rows": list(qs.values("id", "row_no", *CSV_FIELDS)[:TABLE_LIMIT])})


def job_download(request, pk):
    job = get_object_or_404(Job, pk=pk)
    qs = job.results.all()
    if request.GET.get("valid") == "1":
        qs = qs.exclude(valid_email="")
    if request.GET.get("c"):
        qs = qs.filter(college=request.GET["c"])
    resp = HttpResponse(content_type="text/csv")
    suffix = "_valid" if request.GET.get("valid") == "1" else ""
    resp["Content-Disposition"] = f'attachment; filename="student_emails_job{job.pk}{suffix}.csv"'
    resp.write("﻿")   # lets Excel open UTF-8 names correctly
    w = csv.writer(resp)
    w.writerow(CSV_FIELDS)
    for r in qs.values_list(*CSV_FIELDS).iterator():
        w.writerow(r)
    return resp


@require_POST
def job_retry(request, pk):
    """Run again the rows with no valid email that did not match the profile (optionally with a new college)."""
    job = get_object_or_404(Job, pk=pk)
    if (resp := _key_missing(request, ("finder:job", pk))):
        return resp
    if job.state in ("running", "queued"):
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
    start_job(job.pk, settings.SEAMLESS_API_KEY)
    messages.success(request, f"Retrying {n} students.")
    return redirect("finder:job", pk=pk)


@require_POST
def job_delete(request, pk):
    get_object_or_404(Job, pk=pk).delete()
    messages.success(request, "Job deleted.")
    return redirect("finder:index")


# ---------------------------------------------------------------- templates

def template_xlsx(request):
    resp = HttpResponse(build_template_xlsx(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = 'attachment; filename="student_upload_template.xlsx"'
    return resp


def template_csv(request):
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="student_upload_template.csv"'
    csv.writer(resp).writerow(TEMPLATE_HEADERS)
    return resp
