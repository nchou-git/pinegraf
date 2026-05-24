# GCP Migration

Pinegraf now runs on Google Cloud Platform:

- Cloud Run service: `pinegraf`
- Service URL: `https://pinegraf-ghuqxhu2ua-uk.a.run.app`
- Region: `us-east4`
- Cloud SQL instance: `pinegraf-db`
- Cloud SQL connection name: `pinegraf-prod:us-east4:pinegraf-db`
- Artifact Registry image: `us-east4-docker.pkg.dev/pinegraf-prod/pinegraf/app:latest`
- Cloud Run service account: `119418200766-compute@developer.gserviceaccount.com`

## Commands Run

These are the commands actually run during the migration, in order. Secret
values were never printed or committed.

```bash
which gcloud
gcloud --version
gcloud config list --format='text(core.project,core.account)'
which psql
which openssl && openssl version
gcloud auth list --filter=status:ACTIVE --format='value(account)'

gcloud config set project pinegraf-prod

# First attempt failed because db-f1-micro is invalid on Enterprise Plus.
gcloud sql instances create pinegraf-db \
  --database-version=POSTGRES_16 \
  --tier=db-f1-micro \
  --region=us-east4 \
  --storage-type=SSD \
  --storage-size=10GB \
  --storage-auto-increase \
  --backup-start-time=07:00 \
  --backup \
  --enable-point-in-time-recovery \
  --database-flags=cloudsql.iam_authentication=on

# Corrected command. The CLI returned INTERNAL_ERROR, but the operation later
# completed and the instance became RUNNABLE.
gcloud sql instances create pinegraf-db \
  --database-version=POSTGRES_16 \
  --edition=ENTERPRISE \
  --tier=db-f1-micro \
  --region=us-east4 \
  --storage-type=SSD \
  --storage-size=10GB \
  --storage-auto-increase \
  --backup-start-time=07:00 \
  --backup \
  --enable-point-in-time-recovery \
  --database-flags=cloudsql.iam_authentication=on

gcloud sql instances describe pinegraf-db --format='value(state,connectionName)'
gcloud secrets describe DB_PASSWORD --format='value(name)'
gcloud secrets describe DATABASE_URL --format='value(name)'

gcloud sql databases create pinegraf --instance=pinegraf-db

PASSWORD="$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-32)"
gcloud sql users create pinegraf_app \
  --instance=pinegraf-db \
  --password="$PASSWORD"

echo -n "$PASSWORD" | gcloud secrets create DB_PASSWORD \
  --data-file=- \
  --replication-policy=automatic

curl -L \
  https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.13.0/cloud-sql-proxy.linux.amd64 \
  -o ~/cloud-sql-proxy
chmod +x ~/cloud-sql-proxy

~/cloud-sql-proxy pinegraf-prod:us-east4:pinegraf-db --port 5433 \
  > /tmp/pinegraf-cloud-sql-proxy.log 2>&1 &
PROXY_PID="$!"
sleep 3

PGPASSWORD="$(gcloud secrets versions access latest --secret=DB_PASSWORD)" \
  psql "host=127.0.0.1 port=5433 user=pinegraf_app dbname=pinegraf" \
  -v ON_ERROR_STOP=1 \
  -c "CREATE EXTENSION IF NOT EXISTS vector;" \
  -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;" \
  -c "\\dx"

kill "$PROXY_PID"

CONNECTION_NAME="pinegraf-prod:us-east4:pinegraf-db"
PASSWORD="$(gcloud secrets versions access latest --secret=DB_PASSWORD)"
DATABASE_URL="postgresql+psycopg://pinegraf_app:${PASSWORD}@/pinegraf?host=/cloudsql/${CONNECTION_NAME}"
echo -n "$DATABASE_URL" | gcloud secrets create DATABASE_URL \
  --data-file=- \
  --replication-policy=automatic

gcloud artifacts repositories create pinegraf \
  --repository-format=docker \
  --location=us-east4 \
  --description="Pinegraf container images"

gcloud auth configure-docker us-east4-docker.pkg.dev --quiet

while IFS='=' read -r key value; do
  [[ -z "$key" || "$key" =~ ^# ]] && continue
  echo -n "$value" | gcloud secrets create "$key" \
    --data-file=- --replication-policy=automatic 2>/dev/null \
    || echo -n "$value" | gcloud secrets versions add "$key" --data-file=-
done < ~/pinegraf/.gcp-secrets-staging
rm ~/pinegraf/.gcp-secrets-staging

for secret in OPENAI_API_KEY PINEGRAF_ADMIN_PASSWORD SITE_AUTH_USER SITE_AUTH_PASSWORD DATABASE_URL DB_PASSWORD; do
  gcloud secrets add-iam-policy-binding "$secret" \
    --member="serviceAccount:119418200766-compute@developer.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
done

gcloud projects add-iam-policy-binding pinegraf-prod \
  --member="serviceAccount:119418200766-compute@developer.gserviceaccount.com" \
  --role="roles/cloudsql.client"

# First build failed because COMMIT_SHA is empty for gcloud builds submit.
gcloud builds submit --config cloudbuild.yaml .

# cloudbuild.yaml was fixed to use BUILD_ID, then the build succeeded.
gcloud builds submit --config cloudbuild.yaml .

gcloud run deploy pinegraf \
  --image=us-east4-docker.pkg.dev/pinegraf-prod/pinegraf/app:latest \
  --region=us-east4 \
  --platform=managed \
  --allow-unauthenticated \
  --add-cloudsql-instances=pinegraf-prod:us-east4:pinegraf-db \
  --memory=1Gi \
  --cpu=1 \
  --min-instances=0 \
  --max-instances=4 \
  --concurrency=80 \
  --timeout=300 \
  --set-env-vars="USE_MOCK_FETCH=false,USE_MOCK_EXTRACT=false,USE_MOCK_QUERY=false" \
  --set-secrets="OPENAI_API_KEY=OPENAI_API_KEY:latest,PINEGRAF_ADMIN_PASSWORD=PINEGRAF_ADMIN_PASSWORD:latest,SITE_AUTH_USER=SITE_AUTH_USER:latest,SITE_AUTH_PASSWORD=SITE_AUTH_PASSWORD:latest,DATABASE_URL=DATABASE_URL:latest"

SERVICE_URL="$(gcloud run services describe pinegraf --region=us-east4 --format='value(status.url)')"
curl -fsS "$SERVICE_URL/health"
curl -i "$SERVICE_URL/"
gcloud run services logs read pinegraf --region=us-east4 --limit=100

ADMIN_PASSWORD="$(gcloud secrets versions access latest --secret=PINEGRAF_ADMIN_PASSWORD)"
curl -fsS -u "admin:${ADMIN_PASSWORD}" "$SERVICE_URL/admin/stats"

gcloud billing budgets create \
  --billing-account=01D07B-2696F0-52D834 \
  --display-name="Pinegraf demo budget" \
  --budget-amount=25USD \
  --threshold-rule=percent=0.5 \
  --threshold-rule=percent=0.9 \
  --threshold-rule=percent=1.0 \
  --filter-projects=projects/119418200766

fly apps destroy pinegraf --yes
```

## Verification

Cloud Run smoke checks passed:

```bash
curl -fsS https://pinegraf-ghuqxhu2ua-uk.a.run.app/health
# {"ok":true}

curl -i https://pinegraf-ghuqxhu2ua-uk.a.run.app/
# HTTP 401 with WWW-Authenticate: Basic realm="Pinegraf"
```

Admin stats after startup seeding:

```json
{
  "sources": 2,
  "source_runs": 0,
  "fetches": 0,
  "documents": 0,
  "document_fetches": 0,
  "chunks": 0,
  "extractor_runs": 0,
  "claims_raw": 0,
  "entities": 0,
  "entity_aliases": 0,
  "entity_mentions": 0,
  "claims": 0,
  "claim_evidence": 0,
  "claim_conflicts": 0,
  "human_signals": 0,
  "entity_summary": 0,
  "entity_neighborhood": 0
}
```

Cloud Run logs showed Alembic ran `0001_foundation` and Uvicorn started on port
8080.

## Operations

View logs:

```bash
gcloud run services logs read pinegraf --region=us-east4
```

Redeploy:

```bash
gcloud builds submit --config cloudbuild.yaml .
gcloud run services update pinegraf \
  --region=us-east4 \
  --image=us-east4-docker.pkg.dev/pinegraf-prod/pinegraf/app:latest
```

Update a secret:

```bash
echo -n "new-value" | gcloud secrets versions add NAME --data-file=-
```

Connect to Cloud SQL locally:

```bash
~/cloud-sql-proxy pinegraf-prod:us-east4:pinegraf-db --port 5433
PGPASSWORD="$(gcloud secrets versions access latest --secret=DB_PASSWORD)" \
  psql "host=127.0.0.1 port=5433 user=pinegraf_app dbname=pinegraf"
```

## Cost

Expected cost is roughly `$12-15/month` after free trial credits, dominated by
the single-zone `db-f1-micro` Cloud SQL instance and small Cloud Run/Artifact
Registry usage. A `$25` budget alert named `Pinegraf demo budget` was created.

## Fly

Fly app `pinegraf` was destroyed successfully:

```text
Destroyed app pinegraf
```

No Fly resources are intentionally left behind.
