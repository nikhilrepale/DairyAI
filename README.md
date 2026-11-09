# DairyAI V2 — Marathi-first dairy management (pilot build)

DairyAI V2 builds on the owner's existing FastAPI, SQLAlchemy, HTML, CSS and vanilla JavaScript project. **It is a working pilot**, not a certified financial accounting platform. Review a complete settlement against a dairy's manually prepared register before relying on it for payouts.

## Product experience

| Screen | Everyday task |
| --- | --- |
| **Home / मुख्यपृष्ठ** | Milk collected today, term totals, pending corrections, shortcuts |
| **Milk / दूध नोंद** | Compact farmer–litres–rate–amount table; editable approved entries before final billing |
| **AI Scan / AI स्कॅन** | Auto-identify milk, cattle-feed sales or a FAT-SNF rate chart with Gemini Vision; review/correct and approve |
| **Cattle Feed / पशुखाद्य** | Separate **Stock**, **Sales** and **History** tabs; bulk manual inward, optional selling-price changes, scanned farmer sales |
| **Farmers / शेतकरी** | Customer details, balances, recent transactions and sharing |
| **Billing / १० दिवसांचा हिशोब** | Term selection, draft bills, feed/advance/interest/anamat deductions, immutable finalized snapshots, printable bills and WhatsApp click-to-chat |
| **Rate Chart / दर पत्रक** | Actual FAT/SNF matrix, photo import to editable draft, manually editable grid, effective-date versions |
| **Advances / अॅडव्हान्स** | Customer advances with installment; anamat ledger and payout |
| **Reports / अहवाल** | Milk procurement, sales/dispatch, feed margin, expenses, estimated operating surplus |
| **Settings / सेटिंग्ज** | Dairy-wide versioned interest and anamat policies, AI status |

All authoritative rate, bill, inventory and interest calculations execute on the Python backend using `decimal.Decimal`; AI is used only for document classification/extraction and the optional Marathi assistant. **AI never posts money automatically.** An owner/manager must approve entries. An approved milk entry can be corrected with version checking and audited delta postings before bill finalization; finalized entries cannot be changed directly.

Multi-dairy login/JWT membership remains intact, including OWNER/MANAGER/OPERATOR roles. The application can be installed to an Android home screen as a PWA via HTTPS. Native Android packaging is not required for the initial pilot.

## Project layout

```text
DairyAI_Ready/
├── backend/
│   ├── app/
│   │   ├── main.py, models.py, schemas.py, security.py, database.py
│   │   ├── routes/
│   │   │   ├── auth.py, feed_inventory.py, rate_charts.py
│   │   │   └── experience.py             # simpler owner-facing API
│   │   └── services/
│   │       ├── ocr.py, extraction.py, image_preprocessing.py
│   │       ├── document_classifier.py, rate_charts.py
│   │       ├── feed_inventory.py, settlements.py, ledger.py
│   │       ├── image_storage.py, audit.py, messaging.py, assistant.py
│   ├── migrations/                     # initial Alembic revision
│   ├── scripts/copy_sqlite_to_postgres.py
│   ├── tests/test_pilot_workflows.py
│   ├── requirements.txt, alembic.ini, .env.example
├── frontend/
│   ├── index.html, app.js, styles.css
│   ├── manifest.json, service-worker.js, icons/
│   └── prototype.html                   # retained from original project
├── render.yaml
└── docs/DEPLOY_FREE.md
```

## Run locally (Windows)

Use Python 3.11 (the tested deployment target) and a Windows terminal. From the project root:

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Edit `backend/.env`: paste the generated random value into `JWT_SECRET_KEY` and set `GEMINI_API_KEY` for Gemini. Gemini usage may incur fees outside applicable free quotas. The default configuration uses Gemini first and falls back to local Ollama (`AI_FALLBACK_PROVIDERS=ollama`) for scan classification and extraction. On Windows, install Ollama, then open a terminal and run:

```powershell
ollama pull qwen2.5vl:3b
```

Start the Ollama desktop app and keep it running so the backend can reach `http://localhost:11434`; set `OLLAMA_BASE_URL` and `OLLAMA_MODEL` in `backend/.env` to match your local setup. Check `/api/ocr/health` to see whether both configured providers are available. If Ollama is not installed or running, scans continue to use Gemini and only fall back when a provider fails. Keep `DATABASE_URL=sqlite:///./dairyai.db` for local development. Then:

```powershell
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000** and create a new dairy owner account through the existing registration screen. Use a **real, verified FAT-SNF rate chart** before approving milk entries or generating bills; the app deliberately does not seed fictional production rates.

To run tests:

```powershell
python -m pip install pytest
python -m pytest -q tests
```

For local PostgreSQL, replace `DATABASE_URL` with a PostgreSQL 14+ connection string (e.g. from Neon), then run `python -m alembic upgrade head` again against the empty target database.

## Free pilot hosting

See [step-by-step Neon + Render instructions](docs/DEPLOY_FREE.md). The `render.yaml` in this repository deploys **one Render Web Service serving frontend and API**. The PostgreSQL database is separate on Neon; no static site or second host needed.

Do **not** run the live app with a SQLite file or local image folder on Render Free because its filesystem is ephemeral. For ongoing scans, configure a **private S3-compatible object store** and `SCAN_IMAGE_STORAGE=s3`; without it, original images can be stored as PostgreSQL blobs only for a short/small pilot and will consume your free DB quota quickly. Never make register photos public.

## Data migration from an existing SQLite database

The owner did not upload a current SQLite database, so this ZIP contains **no real data**. Your existing database is not modified by this project package.

1. **Back up** your original `dairyai.db`; verify backup by opening it read-only.
2. Create a **new empty** Neon database and apply `alembic upgrade head` to create current tables.
3. If the source SQLite database already has the complete **current multi-tenant schema**, use the guarded copy helper:

```bash
cd backend
DATABASE_URL='postgresql://...?...' python -m scripts.copy_sqlite_to_postgres --sqlite ./dairyai.db
# review counts, back up source and destination, then explicitly:
DATABASE_URL='postgresql://...?...' python -m scripts.copy_sqlite_to_postgres --sqlite ./dairyai.db --execute
```

4. The helper **refuses** to copy a missing/older schema or into a nonempty PostgreSQL database. An older database requires a separately reviewed migration. Do not just overwrite it or blindly use `create_all`.
5. Reconcile customer counts, inventory, ledger, advances and existing bills on a staging URL before switching over.

## Critical pilot acceptance checklist

- [ ] Owner can register/log in and a second dairy cannot access the first dairy's records.
- [ ] Import the correct printed FAT/SNF chart and manually validate **several** rates against the dairy's manual calculations.
- [ ] Scan a real milk register; correct low-confidence cells and verify server-calculated rates.
- [ ] Scan farmer feed purchases; ensure **sale amount = stock selling rate × quantity**, stock decreases once and a customer ledger debit is posted once.
- [ ] Test an advance with both interest policy and 10-day installment against the owner's actual practice.
- [ ] Test anamat contribution, customer-held balance and Diwali payout.
- [ ] Reconcile an entire 10-day bill by hand before approving live payments.
- [ ] Correct an approved milk entry; ensure it is blocked after final bill settlement.
- [ ] Configure nightly database backups and a tested restore process; do not rely on a free-host plan as your only copy.
- [ ] Verify real AI extraction quality across **30–50 manually transcribed real register images**. These tests are not included because live provider credentials/images were not supplied.

## Limits and next steps

- **Availability:** Render Free sleeps after 15 minutes idle, can take around one minute to start, and has no production SLA. Move to paid hosting once records/payments become business-critical.
- **Scalability:** modular-monolith backend, shared tenant-keyed PostgreSQL and a small DB pool. Suitable to **test** with five low-usage dairy owners; throughput and memory usage for OCR need load testing. Large scans should eventually run as queued jobs with worker processes.
- **Financials:** this is a pilot accounting implementation, not independently reconciled production accounting. Confirm how advances, partial repayments, carried balances, interest and payout confirmations actually operate at the pilot dairy.
- **AI/privacy:** any real customer data sent to a third-party cloud model is subject to that provider's terms. Review data retention, confidentiality and billing before uploading production records.
- **WhatsApp:** free click-to-chat creates an outgoing message draft; the user must press Send. Automated WhatsApp Cloud API messaging requires approved templates/appropriate service setup and may incur fees.
- **Login:** the browser stores a signed short-lived token in `sessionStorage`. For a larger deployment consider additional hardened session management, login rate limiting, monitoring, and a security assessment.
- **Original photos:** S3-compatible storage uses a private bucket and per-dairy object names. The image download API requires authentication and a dairy-scoped query.

## Existing source and acknowledgments

This code reuses and builds on the owner's uploaded DairyAI modules. The older frontend prototype is retained for reference. Only safe `.env.example` placeholders are included; actual credentials, databases and logs are excluded from the distributable ZIP.


## V3: running term register and bank payment export

- Home now shows a farmer × date table for the chosen term, including daily litres, earned amount and live net payable. On mobile, use the day chips. Tap a day to review and use the pencil icon to correct records.
- The correction API recalculates rate and milk amount, posts a ledger adjustment and refreshes Home totals. Finalized bills remain immutable.
- Farmer creation supports optional bank details; Farmer Account has a bank pencil icon. Dairy Settings has an Owner-only debit bank account. Bank data is Fernet-encrypted.
- Finalize a billing term, then use Billing → Bank Excel. It validates all positive-payable farmers and generates a generic XLSX, **not** a bank-certified format or actual payment.
- AI Scan has **Take Photo** and **Upload Photo** buttons.
- **Important:** Set `BANK_DATA_KEY` before entering bank details, then run `python -m alembic upgrade head`. Never expose keys to Git or change a used encryption key without migration.
- See `docs/BANK_PAYMENTS_AND_TERM_DASHBOARD.md` for details, deployment precautions, and the generic bank-file limitation.

## ICICI payment exports

The Billing > Bank Excel workflow now generates the owner-provided ICICI `PAB_VENDOR` 19-column structure. Select the payment date and each farmer’s payment mode. See `docs/ICICI_PAB_EXPORT.md`. Never include the confidential original workbook in Git.
