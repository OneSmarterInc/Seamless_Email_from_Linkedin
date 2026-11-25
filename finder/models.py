from django.db import models


class Job(models.Model):
    STATES = [("queued", "Queued"), ("running", "Running"), ("done", "Done"), ("error", "Error")]

    name = models.CharField(max_length=200, blank=True)
    default_college = models.CharField(max_length=200, blank=True)
    state = models.CharField(max_length=10, choices=STATES, default="queued")
    total = models.PositiveIntegerField(default=0)
    log = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name or f"Job {self.pk}"

    @property
    def processed(self):
        return self.results.exclude(status="pending").count()

    @property
    def found(self):
        return self.results.exclude(valid_email="").count()

    def add_log(self, msg):
        from django.utils import timezone
        line = timezone.localtime().strftime("%H:%M:%S ") + msg
        Job.objects.filter(pk=self.pk).update(log=models.functions.Concat(
            models.F("log"), models.Value(line + "\n"), output_field=models.TextField()))


class StudentResult(models.Model):
    job = models.ForeignKey(Job, related_name="results", on_delete=models.CASCADE)
    row_no = models.PositiveIntegerField()
    name = models.CharField(max_length=200)
    linkedin_url = models.URLField(max_length=500)
    college = models.CharField(max_length=200, blank=True)

    status = models.CharField(max_length=30, default="pending")
    message = models.CharField(max_length=500, blank=True)
    valid_email = models.CharField(max_length=254, blank=True)
    email_type = models.CharField(max_length=30, blank=True)
    confidence = models.CharField(max_length=10, blank=True)
    match_check = models.CharField(max_length=30, blank=True)
    seamless_name = models.CharField(max_length=200, blank=True)
    seamless_company = models.CharField(max_length=200, blank=True)
    seamless_title = models.CharField(max_length=200, blank=True)
    guessed_emails = models.TextField(blank=True)
    request_id = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["row_no"]

    def __str__(self):
        return f"{self.name} ({self.status})"
