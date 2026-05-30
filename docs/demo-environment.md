# Demo environment (pinegrafdemo.com)

Parallel deployment of Pinegraf with isolated database, secrets, and Cloud Run
service. Same codebase, different git branch.

## Branch model

- `main` -> pinegraf.com (production)
- `demo` -> pinegrafdemo.com (demo)
- Merge `main` into `demo` to roll prod-ready changes into demo.
- Cherry-pick or PR demo-only experiments into `demo` without touching main.

## Manual one-time setup

Database (separate Cloud SQL instance):

```bash
gcloud sql instances create pinegraf-demo-db \
  --project=pinegraf-prod \
  --database-version=POSTGRES_16 \
  --region=us-east4 \
  --tier=db-f1-micro

gcloud sql databases create pinegraf --instance=pinegraf-demo-db --project=pinegraf-prod
gcloud sql users create pinegraf_app --instance=pinegraf-demo-db \
  --project=pinegraf-prod \
  --password=GENERATE_STRONG_PASSWORD
```

Secrets (all prefixed `DEMO_*`):

```bash
echo -n "postgresql+psycopg://pinegraf_app:PASSWORD@DEMO_DB_IP:5432/pinegraf?sslmode=require" \
  | gcloud secrets create DEMO_DATABASE_URL \
  --project=pinegraf-prod --data-file=-

echo -n "PASSWORD" | gcloud secrets create DEMO_DB_PASSWORD \
  --project=pinegraf-prod --data-file=-

echo -n "your-openai-key" | gcloud secrets create DEMO_OPENAI_API_KEY \
  --project=pinegraf-prod --data-file=-

echo -n "your-pdl-key" | gcloud secrets create DEMO_PDL_API_KEY \
  --project=pinegraf-prod --data-file=-

echo -n "$(openssl rand -hex 32)" | gcloud secrets create DEMO_ADMIN_SESSION_SECRET \
  --project=pinegraf-prod --data-file=-

echo -n "demo-admin-password" | gcloud secrets create DEMO_PINEGRAF_ADMIN_PASSWORD \
  --project=pinegraf-prod --data-file=-

echo -n "demoviewer:somePassword" | gcloud secrets create DEMO_BASIC_AUTH_CREDENTIALS \
  --project=pinegraf-prod --data-file=-
```

Grant the Cloud Run runtime service account access to the demo secrets:

```bash
PROJECT_NUMBER="$(gcloud projects describe pinegraf-prod --format='value(projectNumber)')"
SERVICE_ACCOUNT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

for secret in \
  DEMO_DATABASE_URL \
  DEMO_DB_PASSWORD \
  DEMO_OPENAI_API_KEY \
  DEMO_PDL_API_KEY \
  DEMO_ADMIN_SESSION_SECRET \
  DEMO_PINEGRAF_ADMIN_PASSWORD \
  DEMO_BASIC_AUTH_CREDENTIALS
do
  gcloud secrets add-iam-policy-binding "$secret" \
    --project=pinegraf-prod \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/secretmanager.secretAccessor"
done
```

Create the `demo` branch after the main plumbing commit is pushed:

```bash
git checkout main
git pull
git checkout -b demo
git push -u origin demo
```

Cloud Build trigger on `demo` branch:

Configure in Cloud Console -> Cloud Build -> Triggers. Source: same repo as
prod. Event: push to branch `demo`. Build config: `cloudbuild.yaml`.

Use these substitutions:

```text
_SERVICE = pinegrafdemo
_IMAGE_REPO = us-east4-docker.pkg.dev/pinegraf-prod/pinegraf/app
_ENVIRONMENT = demo
_MIN_INSTANCES = 0
_MAX_INSTANCES = 2
_SECRET_DATABASE_URL = DEMO_DATABASE_URL
_SECRET_OPENAI = DEMO_OPENAI_API_KEY
_SECRET_PDL = DEMO_PDL_API_KEY
_SECRET_SESSION = DEMO_ADMIN_SESSION_SECRET
_SECRET_ADMIN_PASSWORD = DEMO_PINEGRAF_ADMIN_PASSWORD
_SECRET_DB_PASSWORD = DEMO_DB_PASSWORD
_SECRET_BASIC_AUTH = DEMO_BASIC_AUTH_CREDENTIALS
```

Domain mapping (after first Cloud Build succeeds and `pinegrafdemo` service exists):

```bash
gcloud beta run domain-mappings create \
  --service=pinegrafdemo \
  --domain=pinegrafdemo.com \
  --region=us-east4 \
  --project=pinegraf-prod
```

Configure DNS at the domain registrar using Google's verification records from
the domain mapping command.

## Operational notes

- Demo Cloud SQL instance can be paused when not needed:

  ```bash
  gcloud sql instances patch pinegraf-demo-db \
    --project=pinegraf-prod \
    --activation-policy=NEVER
  ```

  Resume by setting `--activation-policy=ALWAYS`.

- Demo runs cost roughly $10-30/month idle, more if actively used.
- Demo's PDL credit pool is separate from prod's. Configure a spending limit in
  the PDL dashboard on the demo key.
- Basic auth credentials live in `DEMO_BASIC_AUTH_CREDENTIALS` as
  `username:password`. To rotate, add a new secret version and deploy a new
  Cloud Run revision.

## Reset demo data

The boot seed only runs when the demo database has no rows in `sources`. For a
clean reseed after fixture changes, drop and recreate the demo database:

```bash
gcloud sql databases delete pinegraf \
  --project=pinegraf-prod \
  --instance=pinegraf-demo-db

gcloud sql databases create pinegraf \
  --project=pinegraf-prod \
  --instance=pinegraf-demo-db

DEMO_DATABASE_URL="$(gcloud secrets versions access latest \
  --project=pinegraf-prod \
  --secret=DEMO_DATABASE_URL)"
DATABASE_URL="$DEMO_DATABASE_URL" .venv/bin/alembic upgrade head
```

Then deploy the `demo` branch again. The next `pinegrafdemo` boot will import
fixture files from `data/demo_fixtures/`.
