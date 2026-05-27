# Parse Trigger Policy

Parse is an explicit user action. It is never triggered automatically.

After a crawl completes, pending fetches accumulate. The source row shows an
info pill such as "N documents haven't been parsed." The user clicks `Parse`
to drain that pending work.

This is intentional: LLM-backed parsing is the primary cost driver.
Auto-triggering would bill the user on every crawl, including weekly
auto-recrawls that may or may not have meaningful changes.

Hash-diff deduplication ensures unchanged content does not generate new parse
work even when parsing is manually triggered, so the user's parse click only
pays for actually changed pages.
