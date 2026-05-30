# Demo fixtures

The demo seed loader reads local HTML files from this directory using
`manifest.yaml`. Add each fixture file and manifest entry on the `demo` branch,
then commit and push to trigger the demo deployment.

Fetch a page from your WSL machine:

```bash
curl -L -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36" \
  "https://example.com/page" \
  -o data/demo_fixtures/example-page.html
```

Add a manifest entry:

```yaml
  - filename: example-page.html
    original_url: https://example.com/page
    source: external
    display_name: Example page
```

Use `source: tuck` for `tuck.dartmouth.edu` pages. Use `source: external` for
everything else. The seed only runs when `PINEGRAF_DEMO_MODE=true` and the demo
database has no source rows.
