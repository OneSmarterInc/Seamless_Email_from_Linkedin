# Student Email Finder (Django + Seamless.AI)

Finds student emails from their name, LinkedIn profile URL and college, using your Seamless.AI API key.

## Run it

```
python -m venv venv
venv\Scripts\activate          # Windows   (Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env         # Mac/Linux: cp .env.example .env
# open .env and put your key after SEAMLESS_API_KEY=
python manage.py migrate
python manage.py runserver
```

Open http://127.0.0.1:8000

Optional: `python manage.py createsuperuser`, then use http://127.0.0.1:8000/admin to browse all results.

## Use it

1. Click **Download Excel template** on the home page. It uses the master-list layout: `S.No. | College | Student Name | LinkedIn Profile | Source File`. Keep the header row.
2. Upload the filled file (or your master list as it is). Other sheets such as College Summary are ignored.
3. On the **Review** page, tick the colleges to run and fix college names. Rows whose College cell holds a person's name or a link are unticked until you type a college. Nothing runs until you click Start.
4. or paste rows: `name, linkedin_url, college`. A row with only a URL works. `sample_students.csv` is an example.
2. Set a default college for rows with no college. Seamless needs it: without it, it answers "Company name is required".
The job page shows progress, a live log, counts, and a results table with filters, search, a college filter, and a per-college breakdown with CSV downloads.
4. Download **valid emails** only, or **all rows**.
5. Use **Retry** to run the rows with no valid email again, for example with a different college name.

## What the result columns mean

| Column | Meaning |
|---|---|
| valid_email | Email Seamless marks valid AND the profile matches. Safe to use. |
| match_check | `linkedin_match` = Seamless returned the same LinkedIn profile. `name_match_only` = names agree only. `wrong_person` = email is dropped. |
| guessed_emails | Patterns Seamless guessed and marked invalid. Do not send to these. |
| status / message | Seamless result: done, not found, error, timeout. |

## Settings (.env file)

All settings live in `.env` in the project folder. Restart the server after you change it.

| Variable | Default |
|---|---|
| SEAMLESS_API_KEY | required |
| SEAMLESS_BASE_URL | https://api.seamless.ai/api/client/v1 |
| SEAMLESS_AUTH_HEADER | Token |
| DJANGO_SECRET_KEY | dev key — set your own for any shared server |
| DJANGO_DEBUG | 1 |
| DJANGO_ALLOWED_HOSTS | 127.0.0.1,localhost |

If Seamless returns 401 or 404 on the first run, check the base URL and auth header in your Seamless API dashboard and set the two variables above.

## Notes

- Cost: 1 Seamless credit per student when an email is found. Seamless returns credits when it finds nothing.
- The API key is read from `.env`. It is not shown in the UI or saved in the database. `.env` is in `.gitignore`, so it stays on your machine.
- Jobs run in a background thread. If you stop the server during a job, use Retry to finish it. For large volumes, move `finder/runner.py: run_job` into Celery.
- Rate limit: 60 requests per minute. The app waits and retries on limit errors.

## Code map

- `finder/seamless.py` — Seamless client, CSV parsing, match checks, email selection
- `finder/runner.py` — background job: submit batches, poll, save results
- `finder/views.py`, `finder/urls.py` — pages, JSON status, CSV download, retry, delete
- `finder/models.py` — Job and StudentResult
- `finder/templates/finder/` — UI
