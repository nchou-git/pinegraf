#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-pinegraf-prod}"
REGION="${REGION:-us-east4}"
SERVICE="${SERVICE:-pinegraf}"

IMAGE="$(gcloud run services describe "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format='value(spec.template.spec.containers[0].image)')"
SERVICE_ACCOUNT="$(gcloud run services describe "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format='value(spec.template.spec.serviceAccountName)')"

if [[ -z "${SERVICE_ACCOUNT}" ]]; then
  PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
  SERVICE_ACCOUNT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
fi

if ! gcloud projects get-iam-policy "${PROJECT_ID}" \
  --flatten="bindings[].members" \
  --filter="bindings.role=roles/run.developer AND bindings.members=serviceAccount:${SERVICE_ACCOUNT}" \
  --format="value(bindings.members)" | grep -Fx "serviceAccount:${SERVICE_ACCOUNT}" >/dev/null; then
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/run.developer" \
    --quiet >/dev/null
fi

upsert_job() {
  local job="$1"
  local mode="$2"
  local args=(
    "--project=${PROJECT_ID}"
    "--region=${REGION}"
    "--image=${IMAGE}"
    "--memory=1Gi"
    "--cpu=1"
    "--task-timeout=3600s"
    "--service-account=${SERVICE_ACCOUNT}"
    "--command=python"
    "--args=-m,backend.jobs.run"
    "--set-env-vars=PINEGRAF_MODE=${mode}"
    "--set-secrets=DATABASE_URL=DATABASE_URL:latest"
  )

  if gcloud run jobs describe "${job}" --project="${PROJECT_ID}" --region="${REGION}" >/dev/null 2>&1; then
    gcloud run jobs update "${job}" "${args[@]}"
  else
    gcloud run jobs create "${job}" "${args[@]}"
  fi
}

upsert_job pinegraf-crawl crawl
upsert_job pinegraf-parse parse
