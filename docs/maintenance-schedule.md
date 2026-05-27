# Maintenance Schedule

`pinegraf-maintenance` reconciles source pipeline invariants. It should run every
six hours:

```bash
gcloud scheduler jobs create http pinegraf-maintenance-every-6h \
  --project=pinegraf-prod \
  --location=us-east4 \
  --schedule="0 */6 * * *" \
  --uri="https://us-east4-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/pinegraf-prod/jobs/pinegraf-maintenance:run" \
  --http-method=POST \
  --oauth-service-account-email="$(gcloud run services describe pinegraf --project=pinegraf-prod --region=us-east4 --format='value(spec.template.spec.serviceAccountName)')"
```

The job fails stale runs, deletes dangling `DocumentFetch` rows, audits orphan
documents and broken unchanged-body chains, and corrects source counter drift.
It never auto-runs parse.
