# User-authorized one-off recovery send

User requested sending September 8 materials once on September 9 because the previous day's digest was missed. No recurring carryover policy was requested.

- Source date fixed to 2026-09-08; authorization expires after 2026-09-09 KST.
- Existing authenticated admin route, same-origin POST, saved recipient list, durable day/recipient uniqueness, existing queue and Resend idempotency keys.
- Recovery queue lease: five minutes from the immutable snapshot; normal weekday 10:00 window remains unchanged.
- Content: 1 notice, 1 construction news item, 7 legal/regulatory items. Previously provider-confirmed items excluded; expired/cancelled notices excluded.
- API credential parameters are removed from outgoing HTML and plain-text links. Law API links use the public law information page; the tested public law link returned HTTP 200 and the expected title.
- 39 regression tests passed, including recovery date boundaries, normal time gate, duplicate prevention and credential-free links.
- Worker version: `5eeceb97-c0c1-49da-9a9f-7f0d5d679dc7`.

## Verified outcome

After one browser form submission, Chrome blocked the result-page display (`ERR_BLOCKED_BY_CLIENT`). The request itself succeeded: the public server status API showed delivery day 2026-09-09 `sent`, 7 recipient records `sent`, 7 non-empty provider receipt IDs, and no day error. No second form submission was made.

This confirms provider acceptance, not inbox placement. Recipient addresses, secret values and raw provider IDs are intentionally omitted from this record.
