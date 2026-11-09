# Free deployment of DairyAI for a five-dairy pilot

**Recommended:** one **Render Free Python Web Service** (serves both the HTML/JS and the FastAPI APIs) + one **Neon Free PostgreSQL project** + a private S3-compatible object bucket when storing real register images.

This can cost **₹0 for light, experimental use within free quotas**. It is **not reliable 24/7 production hosting** and must not be the only place that stores financial records.

## 1. Create your PostgreSQL database (Neon)

1. Create an account at **https://console.neon.tech/**.
2. Choose **New Project** → name it `dairyai-pilot` → choose a region as close as feasible to your Render service (Singapore if offered).
3. Choose Neon's **Free** plan. In November 2026, official free-plan allowances include **1 GB Postgres storage/project**, **100 CU-hours/project/month** and **5 GB public network transfer/project/month**. Your usage and other plan terms may change; verify them in the Neon console before relying on them.
4. From the project's **Connect** pane, copy the Postgres connection string into a safe password manager. It starts with `postgresql://...` and should include `sslmode=require` (or provider equivalent). Do not paste it into frontend code or commit it to Git.
5. You do **not** need to install or maintain PostgreSQL on your own laptop or pay for a VPS. Neon hosts the database. Create a new database for testing and another/branch for production when moving beyond a pilot.

**Important:** Do not use Render's own Free PostgreSQL database for this pilot; it expires after **30 days** and isn't appropriate for months of records.

## 2. Prepare GitHub repository

Create a **private** GitHub repository and push the extracted `DairyAI_Ready` project folder **contents** with `render.yaml` at the top level, and `backend/` and `frontend/` as sibling folders. The `render.yaml` has no `rootDir`, on purpose: FastAPI must be able to read the sibling frontend files at runtime.

Never commit `.env`, a SQLite database, `.venv`, credentials or customer scan images. The project `.gitignore` excludes them.

## 3. Deploy the API and UI together on Render Free

1. Sign up at **https://dashboard.render.com/** and connect GitHub.
2. Choose **New → Blueprint** (preferred because `render.yaml` is included), select the private repository, and deploy the `dairyai-pilot` service. Alternatively create a **Web Service** manually.
3. Set plan **Free** and region **Singapore** (the region nearest Maharashtra among Render regions, if offered).
4. Use these settings if deploying manually:
   - **Root directory:** *(blank; project root)*
   - **Build command:** `pip install -r backend/requirements.txt`
   - **Start command:** `cd backend && alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`
   - **Health check:** `/api/health`
5. Under **Environment**, configure the following **secrets on Render**, not in your code:

| Key | Value |
| --- | --- |
| `DATABASE_URL` | The Neon Postgres connection URL (private) |
| `JWT_SECRET_KEY` | A cryptographically random secret ≥32 chars (the Blueprint can generate it) |
| `AI_PROVIDER` | `gemini` |
| `GEMINI_API_KEY` | Your own Gemini API key, **if you want live AI extraction** |
| `GEMINI_MODEL` | A currently supported image-capable model your account can access; sample default `gemini-2.5-flash` must be verified |
| `WHATSAPP_MODE` | `click_to_chat` for free manual sharing |
| `SCAN_IMAGE_STORAGE` | `database` for the very first tiny demo, **`s3` recommended** before real scans |

6. Deploy. On startup Alembic creates PostgreSQL tables; the server serves the new frontend at your Render URL. Check `https://<service>.onrender.com/api/health` and then the app homepage, create a dairy account and try both desktop and Android.
7. On Android Chrome, open the HTTPS Render URL → browser menu → **Add to Home screen / Install app**. A DairyAI icon should appear. Depending on browser/device this may be a full PWA or a shortcut.

### What happens when idle?

A Render Free web service **spins down after 15 minutes** without a request. Restarting can take **approximately one minute**. The local Render filesystem resets on restarts/redeploys. Keep all lasting data in Neon and uploaded images in private object storage. The Free service can be suspended when it reaches usage quotas; it is not guaranteed to be continuously available.

## 4. Keep register images OUT of your PostgreSQL quota

A few hundred original register images can exhaust a small Postgres Free plan much faster than typed records.

DairyAI supports two storage modes:

- `SCAN_IMAGE_STORAGE=database`: saves images in PostgreSQL binary columns; **easy demo** but counts against 1 GB DB storage and consumes DB transfer.
- `SCAN_IMAGE_STORAGE=s3`: saves original images in a **private S3-compatible bucket**; Postgres retains only the private object key. Recommended for real pilot scans.

Neon currently advertises **5 GB Object Storage per Free project**; alternatively a private Cloudflare R2 bucket may be usable. Check current region, eligibility, pricing, credential type and provider instructions in the chosen storage dashboard.

For an S3-compatible bucket, add these Render environment variables (provided by your storage vendor):

```dotenv
SCAN_IMAGE_STORAGE=s3
S3_BUCKET=your-private-bucket-name
S3_ENDPOINT_URL=https://your-s3-compatible-endpoint
S3_ACCESS_KEY_ID=your-secret-access-key-id
S3_SECRET_ACCESS_KEY=your-secret-access-key
S3_REGION=your-provider-region
```

Verify a scanned photo can be retrieved via its **authenticated DairyAI API**, that strangers cannot open the object URL directly, and that upload failures do not leave false approved records. Never use a public bucket for farmer records.

## 5. Backups and real-use precautions

**Free does not mean safe for months of financial records**. Before real customer use:

- Take and verify **regular PostgreSQL database exports**, e.g. `pg_dump` to an encrypted backup destination; rehearse `pg_restore` to a test project. Free-tier time travel is limited and not a substitute for backups.
- Enable provider monitoring/quota alerts where offered.
- Back up object storage separately. Verify you can match a scan image to the relevant row after restoring data.
- Use a sufficiently strong `JWT_SECRET_KEY`, never share API keys, and restrict team membership to actual staff.
- Have the dairy owner manually reconcile at least **two entire 10-day billing cycles** against existing handwritten books before paying farmers using app numbers.
- Consider a paid web service once continuous availability, backups, support or more frequent scanning become important.

## 6. Existing SQLite data → PostgreSQL

If you already have genuine data, you must migrate it; a new PostgreSQL database starts empty. The uploaded files **did not include a SQLite database**.

1. Copy your original SQLite file to a safe offline backup.
2. On a clean PostgreSQL destination, run `alembic upgrade head` using the Neon URL.
3. The repository includes `backend/scripts/copy_sqlite_to_postgres.py`. Start with the **dry-run** check and only use `--execute` after validation. The helper accepts a fully compatible current SQLite schema only; it intentionally rejects incomplete/older schemas.
4. Compare tables, customer balances, stock quantities and bill totals on staging before relying on the migrated records.

## Useful official links

- Render free tier: https://render.com/docs/free
- Render deploy: https://render.com/docs/your-first-deploy
- Neon Free limits: https://neon.com/blog/neon-free-plan-1-gb-per-project
- Neon dashboard: https://console.neon.tech/
- Render dashboard: https://dashboard.render.com/
- PWA installation: https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Installing

**Bottom line:** five low-volume dairy accounts can be evaluated using Render + Neon, but **no free service guarantees ample uptime**. If daily customer payments depend on DairyAI, budget for reliable hosting, backups and operational support.
