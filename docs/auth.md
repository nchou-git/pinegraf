# Authentication

Admin endpoints require the Pinegraf session cookie. The cookie is issued by
`POST /admin/login` with a JSON body:

```json
{"username":"pinegraf","password":"..."}
```

Basic auth is not supported. The `WWW-Authenticate` header is intentionally not
sent, so browsers do not show the native password popup.

For scripts and integration checks, log in once, save the cookie jar, and reuse
it:

```bash
BASE_URL="https://pinegraf-ghuqxhu2ua-uk.a.run.app"
COOKIE_JAR=/tmp/pinegraf-admin-cookies.txt
curl -fsS -c "$COOKIE_JAR" -H 'content-type: application/json' \
  -d "{\"username\":\"pinegraf\",\"password\":\"${PINEGRAF_ADMIN_PASSWORD}\"}" \
  "$BASE_URL/admin/login"
curl -fsS -b "$COOKIE_JAR" "$BASE_URL/admin/audit"
```

`ADMIN_SESSION_SECRET` and `PINEGRAF_ADMIN_PASSWORD` live in Google Secret
Manager. Rotate them with:

```bash
printf '%s' "$NEW_PINEGRAF_ADMIN_PASSWORD" \
  | gcloud secrets versions add PINEGRAF_ADMIN_PASSWORD \
      --project=pinegraf-prod --data-file=-
printf '%s' "$NEW_ADMIN_SESSION_SECRET" \
  | gcloud secrets versions add ADMIN_SESSION_SECRET \
      --project=pinegraf-prod --data-file=-
gcloud run services update pinegraf \
  --project=pinegraf-prod --region=us-east4 \
  --update-secrets=PINEGRAF_ADMIN_PASSWORD=PINEGRAF_ADMIN_PASSWORD:latest,ADMIN_SESSION_SECRET=ADMIN_SESSION_SECRET:latest
```
