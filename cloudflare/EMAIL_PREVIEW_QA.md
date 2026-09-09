# Email preview restoration — 2026-09-09

Restores the existing `tender_radar/email_digest.py` visual structure in Cloudflare: CONCOST logo/header, three summary counts, relevance cards, issuer/deadline/amount, new notices, previously notified open notices, today's construction news and legal updates, and source links.

Preview and scheduled delivery share the same renderer. Only provider-confirmed snapshots with item identities count as notification history. Failed deliveries and legacy snapshots without identities are not guessed to be sent. Existing notices are separate from fresh-item counts; past news is excluded. New snapshots retain item identities without adding a table or dependency. Same-day retries reuse the immutable snapshot, including when fresh rows have subsequently disappeared.

Checks:

- 37 Node tests passed: template content, category counts, deduplication/variants, escaped hostile content, empty state, provider-confirmed history, failed/legacy history, no duplicate daily delivery, partial retry snapshot preservation, existing collection and schedule tests.
- Local fixture actual flow: administrator settings → 오늘 요약 미리보기 opens the restored iframe with sample new/old/news/law records. No collection or mail is sent by the fixture.
- Desktop and 375px viewport screenshots inspected. Original email-table layout and brand retained. Impeccable's inherited font/header warnings intentionally retained to preserve the requested existing design; the new empty-state accent border was removed.
- Cloudflare deployment build passed. Deployed version: `55cfe777-b9eb-48bc-8330-de61fe30c7f0`.
- Live administrator session was expired during final verification; authenticated live preview requires user sign-in. No credentials were retrieved or reset to bypass this check.

Unchanged boundaries: actual mail was not sent; weekday 09:00/10:00 schedules, API keys, addressbook and collection logic are unchanged. No-fresh-content still prevents an empty digest. Upstream/date-collection failures are not solved by this template repair. Render delivery history was not imported; only verifiable Cloudflare history populates existing notifications.
