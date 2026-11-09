# DairyAI V3 — Term dashboard and bank payouts

## The Home dashboard
- Shows **all approved milk entries** aggregated by farmer and collection day across the selected date range.
- Each day-cell opens morning/evening entries and a small pencil icon opens the audited correction form.
- The row end shows total litres, gross milk earnings and **running net payable**. The latter subtracts feed, scheduled advance principal + interest, anamat and earlier negative carry-forward.
- The live net payable is a **draft estimate**, not a bank-ready payment, until a bill is finalized. All corrections are recalculated on the FastAPI backend; no financial rates are calculated in JavaScript.
- When viewing a finalized billing period, frozen snapshots determine the net payable. Corrections to milk entries inside finalized periods are rejected by the backend.
- Select dates at the top. Mobile displays compact farmer cards with horizontally scrollable day chips.

## Setting up secure bank data
- The farmer creation dialog has optional collapsed bank fields. Existing farmers' bank accounts can be updated via the pencil icon in Farmer Account.
- **Settings → Dairy Debit Account** allows the dairy owner to configure their debit account.
- Store `BANK_DATA_KEY` as a secret (Fernet key), generated **once** with:

  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

- A changed/lost key renders stored bank account numbers unreadable. Back up the key securely; never put it in Git.
- Full account numbers are encrypted at rest and never returned to the browser after saving; only masked numbers are shown. Role checks prevent a different dairy from reading them.
- For a new dairy owner, configure the debit account once; then configure each farmer's account number, confirmed account number, IFSC and holder name. Leading zeros are preserved.

## Generating the ICICI bank payment Excel
1. Complete and **finalize** the term's customer bills.
2. Open **Billing** for that term and choose **Bank Excel**.
3. The preview shows each missing account. Use the pencil icon to correct beneficiary details. All positive payout farmers must be configured; the export never silently drops someone.
4. Download the XLSX. The download event is audited with a count, amount, and SHA-256 checksum. **No bank payment is initiated or marked paid** by downloading.
5. The export now matches the **19-column ICICI PAB_VENDOR sample layout** the owner supplied. See `docs/ICICI_PAB_EXPORT.md` for exact mapping. Choose the payment date separately from the billing term; select NEFT or FT in each farmer’s bank details. The sample has both modes, including ICICI beneficiaries under NEFT. **Bank acceptance has not been independently verified.**
6. Exporting the same term again shows a duplicate-payment warning, but the application cannot know if a transfer has actually been made. Check the bank's acknowledgement or transaction report first.

## Image capture
- **AI Scan → Take Photo** opens the camera on supported smartphones, while **Upload Photo** opens the device file picker.
- Both send a JPEG/PNG image to the configured vision extraction service for review; account and rate-chart changes still require approval.
- The existing PWA remains installable. Camera permissions/device behavior depend on the browser; HTTPS is required outside localhost.

## Database upgrade

Back up the database first. In `backend`:

```sh
python -m alembic upgrade head
```

The new migration adds encrypted bank-data columns for dairies/customers and an audit table for downloaded bank payment batches. Set `DATABASE_URL` to Neon PostgreSQL if deploying there, with TLS (`sslmode=require`). Retain the old database until migration and financial reconciliation have been verified.

## Pilot and production cautions
- Never put real bank details in a public repository or screenshots.
- Keep the Neon database private. Store JWT and BANK_DATA_KEY in secret environment variables.
- Export is currently **XLSX preparation, not bank API payment initiation**. There is no reconciliation or paid-status workflow yet.
- Test with a fictional account first. Have the dairy owner and bank reconcile an entire 10-day term before using the result for real payments.
- Any free hosting sleeping or quota exhaustion makes the system temporarily unavailable. Budget for paid backups, object storage, and uptime before financial production use.
