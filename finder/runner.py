"""Background job runner. Uses a thread so no extra services are needed.
For heavy production use, move run_job() into Celery or Django-Q."""

import threading
import time

from django.db import close_old_connections
from django.utils import timezone

from .models import Job, StudentResult
from .seamless import BATCH_SIZE, SeamlessClient, guessed, match_check, pick_email


def start_job(job_id, api_key):
    threading.Thread(target=run_job, args=(job_id, api_key), daemon=True).start()


def run_job(job_id, api_key):
    close_old_connections()
    job = Job.objects.get(pk=job_id)
    Job.objects.filter(pk=job_id).update(state="running")
    client = SeamlessClient(api_key, log=job.add_log)
    try:
        rows = list(job.results.filter(status="pending"))
        job.add_log(f"Started. {len(rows)} students to check.")
        for start in range(0, len(rows), BATCH_SIZE):
            batch = rows[start:start + BATCH_SIZE]
            job.add_log(f"Batch {start // BATCH_SIZE + 1}: sending {len(batch)} students to Seamless.")
            ids = client.submit([{"name": r.name, "linkedin_url": r.linkedin_url, "college": r.college}
                                 for r in batch])
            for r, rid in zip(batch, ids):
                r.request_id = rid or ""
                r.status = "submitted"
                r.save(update_fields=["request_id", "status"])

            results = client.poll(ids)
            for r, rid in zip(batch, ids):
                save_result(r, results.get(rid, {}))
            time.sleep(1.1)        # stay under 60 requests per minute

        job.refresh_from_db()
        job.add_log(f"Finished. {job.found}/{job.total} valid emails.")
        Job.objects.filter(pk=job_id).update(state="done", finished_at=timezone.now())
    except Exception as e:  # noqa: BLE001
        job.add_log(f"STOPPED: {e}")
        job.results.filter(status__in=["pending", "submitted"]).update(status="not run")
        Job.objects.filter(pk=job_id).update(state="error", finished_at=timezone.now())
    finally:
        close_old_connections()


def save_result(row, item):
    c = item.get("contact") or {}
    email, etype, conf = pick_email(c)
    mc = match_check(row.name, row.linkedin_url, c)
    if mc == "wrong_person":
        email, etype, conf = "", "", ""     # never return an email for a different person
    row.status = item.get("status") or "no response"
    row.message = (item.get("message") or "")[:500]
    row.valid_email = email
    row.email_type = etype
    row.confidence = conf
    row.match_check = mc
    row.seamless_name = (c.get("nameOriginal") or "")[:200]
    row.seamless_company = (c.get("company") or "")[:200]
    row.seamless_title = (c.get("title") or "")[:200]
    row.guessed_emails = guessed(c)
    row.save()
