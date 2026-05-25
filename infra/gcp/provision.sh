#!/usr/bin/env bash
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
: "${PROJECT_NUMBER:?Set PROJECT_NUMBER}"
: "${BILLING_ACCOUNT:?Set BILLING_ACCOUNT}"

REGION="${REGION:-us-east4}"
INSTANCE_NAME="${INSTANCE_NAME:-pinegraf-db}"
DATABASE_NAME="${DATABASE_NAME:-pinegraf}"
DB_USER="${DB_USER:-pinegraf_app}"
CONNECTION_NAME="${PROJECT_ID}:${REGION}:${INSTANCE_NAME}"
SERVICE_ACCOUNT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
DEV_IP="$(curl -fsS https://api.ipify.org || true)"
AUTHORIZED_NETWORKS="0.0.0.0/0"
if [[ -n "${DEV_IP}" ]]; then
  AUTHORIZED_NETWORKS="${DEV_IP}/32,${AUTHORIZED_NETWORKS}"
fi

gcloud config set project "${PROJECT_ID}"

gcloud sql instances create "${INSTANCE_NAME}" \
  --database-version=POSTGRES_16 \
  --edition=ENTERPRISE \
  --tier=db-f1-micro \
  --region="${REGION}" \
  --storage-type=SSD \
  --storage-size=10GB \
  --storage-auto-increase \
  --backup-start-time=07:00 \
  --backup \
  --enable-point-in-time-recovery \
  --authorized-networks="${AUTHORIZED_NETWORKS}" \
  --database-flags=cloudsql.iam_authentication=on

gcloud sql instances describe "${INSTANCE_NAME}" --format="value(state)"

gcloud sql databases create "${DATABASE_NAME}" --instance="${INSTANCE_NAME}"

PASSWORD="$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-32)"

gcloud sql users create "${DB_USER}" \
  --instance="${INSTANCE_NAME}" \
  --password="${PASSWORD}"

echo -n "${PASSWORD}" | gcloud secrets create DB_PASSWORD \
  --data-file=- \
  --replication-policy=automatic

INSTANCE_IP="$(gcloud sql instances describe "${INSTANCE_NAME}" --format="value(ipAddresses[0].ipAddress)")"

PGPASSWORD="$(gcloud secrets versions access latest --secret=DB_PASSWORD)" \
  psql "host=${INSTANCE_IP} port=5432 user=${DB_USER} dbname=${DATABASE_NAME} sslmode=require" \
  -v ON_ERROR_STOP=1 \
  -c "CREATE EXTENSION IF NOT EXISTS vector;" \
  -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;" \
  -c "\\dx"

DATABASE_URL="postgresql+psycopg://${DB_USER}:${PASSWORD}@${INSTANCE_IP}:5432/${DATABASE_NAME}?sslmode=require"

echo -n "${DATABASE_URL}" | gcloud secrets create DATABASE_URL \
  --data-file=- \
  --replication-policy=automatic

gcloud artifacts repositories create pinegraf \
  --repository-format=docker \
  --location="${REGION}" \
  --description="Pinegraf container images"

gcloud auth configure-docker "${REGION}-docker.pkg.dev"

while IFS='=' read -r key value; do
  [[ -z "${key}" || "${key}" =~ ^# ]] && continue
  echo -n "${value}" | gcloud secrets create "${key}" \
    --data-file=- --replication-policy=automatic 2>/dev/null \
    || echo -n "${value}" | gcloud secrets versions add "${key}" --data-file=-
done < "${HOME}/pinegraf/.gcp-secrets-staging"

rm "${HOME}/pinegraf/.gcp-secrets-staging"

for secret in OPENAI_API_KEY PINEGRAF_ADMIN_PASSWORD DATABASE_URL DB_PASSWORD; do
  gcloud secrets add-iam-policy-binding "${secret}" \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/secretmanager.secretAccessor"
done

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/cloudsql.client"

gcloud builds submit --config cloudbuild.yaml .

gcloud run deploy pinegraf \
  --image="${REGION}-docker.pkg.dev/${PROJECT_ID}/pinegraf/app:latest" \
  --region="${REGION}" \
  --platform=managed \
  --allow-unauthenticated \
  --memory=1Gi \
  --cpu=1 \
  --min-instances=0 \
  --max-instances=4 \
  --concurrency=80 \
  --timeout=300 \
  --set-secrets="OPENAI_API_KEY=OPENAI_API_KEY:latest,PINEGRAF_ADMIN_PASSWORD=PINEGRAF_ADMIN_PASSWORD:latest,DATABASE_URL=DATABASE_URL:latest,DB_PASSWORD=DB_PASSWORD:latest"

gcloud billing budgets create \
  --billing-account="${BILLING_ACCOUNT}" \
  --display-name="Pinegraf demo budget" \
  --budget-amount=25USD \
  --threshold-rule=percent=0.5 \
  --threshold-rule=percent=0.9 \
  --threshold-rule=percent=1.0 \
  --filter-projects="projects/${PROJECT_NUMBER}"
