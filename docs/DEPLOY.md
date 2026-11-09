# Deploying MandatePay

MandatePay is one long-lived Python process with a SQLite database and a local
uploads directory. Any host that can run a container (or a Python web service)
with a persistent disk works.

It is **not** a good fit for Vercel, Netlify, Cloudflare Workers or GitHub Pages:
those platforms either cannot run FastAPI continuously or lose the local
database and uploaded files on every request/restart. Supabase is optional, not
required: the app currently uses `sqlite3`, so you would have to replace the
store layer with Postgres to use it.

**Recommended:** Render or Railway with the included `Dockerfile`. Both provide
HTTPS, a public URL and persistent volumes.

---

## Before you deploy

You need:

- the public GitHub repo;
- an account on the hosting platform;
- a username and password for the public demo (HTTP Basic auth is built in);
- optionally, PayPal sandbox credentials.

No PayPal credentials are required. The default deployment runs the deterministic
mock adapter and the rules planner/verifier, so judges can complete the whole demo
offline.

> **Never deploy with live PayPal.** Keep `PAYPAL_ENV=sandbox` and `ALLOW_LIVE`
> unset/false. The app also refuses live mode without `ALLOW_LIVE=1` and real
> credentials, but the safe default is to keep the mock adapter on.

---

## Option 1 — Render (recommended, blueprint included)

The repository contains [`render.yaml`](../render.yaml), which builds the
`Dockerfile`, sets `DATA_DIR=/data`, enables demo mode and turns on Basic auth.

1. Sign in to <https://render.com> with GitHub.
2. Choose **New → Blueprint**.
3. Select `nattypatty-beep/paypalhack`.
4. Render reads `render.yaml`. When prompted, enter a strong
   `BASIC_AUTH_PASSWORD`. The username defaults to `judge`.
5. Click **Apply** and wait for the image build.
6. Open the generated `https://mandatepay-*.onrender.com` URL. Your browser asks
   for the username and password.
7. Click **Run full demo**.

You can also use the one-click blueprint link:

<https://render.com/deploy?repo=https://github.com/nattypatty-beep/paypalhack>

### Free plan caveat

Render's free plan does not support persistent disks. The app works, but the
SQLite database and uploads are lost whenever the service sleeps, restarts or
redeploys. That is acceptable for a demo because **Reset demo** recreates
everything.

To keep state:

1. Change the service to a paid instance type.
2. Uncomment the `disks` block in `render.yaml` (or add a disk in the dashboard)
   and mount it at `/data`.
3. Redeploy.

### Add PayPal sandbox credentials

In the Render dashboard → **Environment**:

| Key | Value |
| --- | --- |
| `PAYPAL_CLIENT_ID` | sandbox client ID |
| `PAYPAL_CLIENT_SECRET` | sandbox secret |
| `PAYPAL_FORCE_MOCK` | `false` |
| `PAYPAL_ENV` | `sandbox` |
| `PAYPAL_WEBHOOK_ID` | webhook ID from the PayPal dashboard |
| `ALLOW_LIVE` | `false` |

Redeploy after changing variables. `/api/health` and the header switch from
**PayPal mock** to **PayPal sandbox** only when the credentials are valid.

Webhook URL:

```text
https://YOUR-SERVICE.onrender.com/api/webhooks/paypal
```

See [PAYPAL.md](PAYPAL.md) for the sandbox app, Payouts permissions and webhook
events.

---

## Option 2 — Railway

1. Create a project and choose **Deploy from GitHub repo**.
2. Railway detects the `Dockerfile`.
3. Under **Variables**, add:

   ```text
   DATA_DIR=/data
   DEMO_MODE=true
   PAYPAL_FORCE_MOCK=true
   ALLOW_LIVE=false
   BASIC_AUTH_USER=judge
   BASIC_AUTH_PASSWORD=<strong password>
   ```

4. Add a **Volume** mounted at `/data`.
5. Under **Networking**, generate a public domain.
6. Open the URL and approve the browser's Basic auth prompt.

Railway keeps the volume across deploys, so the demo state survives.

---

## Option 3 — Fly.io

```bash
fly launch --no-deploy
fly volumes create mandatepay_data --size 1
fly secrets set BASIC_AUTH_USER=judge BASIC_AUTH_PASSWORD='<strong password>'
fly deploy
fly open
```

Make sure `fly.toml` contains the volume mount and `DATA_DIR`:

```toml
[[mounts]]
  source = "mandatepay_data"
  destination = "/data"

[env]
  DATA_DIR = "/data"
  DEMO_MODE = "true"
  PAYPAL_FORCE_MOCK = "true"
  ALLOW_LIVE = "false"
```

Fly gives you HTTPS automatically.

---

## Option 4 — Any VPS with Docker

```bash
docker build -t mandatepay .
docker run -d --name mandatepay --restart unless-stopped \
  -p 127.0.0.1:8000:8000 \
  -v mandatepay_data:/data \
  -e BASIC_AUTH_USER=judge \
  -e BASIC_AUTH_PASSWORD='<strong password>' \
  -e DEMO_MODE=true \
  -e PAYPAL_FORCE_MOCK=true \
  -e ALLOW_LIVE=false \
  mandatepay
```

Put a TLS reverse proxy in front. Example `Caddyfile`:

```caddyfile
demo.example.com {
    reverse_proxy 127.0.0.1:8000 {
        flush_interval -1
    }
}
```

`flush_interval -1` disables proxy buffering so Server-Sent Events (`/api/stream`)
arrive immediately. Caddy obtains and renews HTTPS certificates automatically.

Run one container only. SQLite and the uploads directory are not designed for
multiple replicas sharing a volume.

---

## Local Docker test

If Docker is installed:

```bash
docker build -t mandatepay .
docker run --rm -p 8000:8000 \
  -e BASIC_AUTH_USER=judge \
  -e BASIC_AUTH_PASSWORD=local-demo \
  -v mandatepay_data:/data \
  mandatepay
```

Open <http://127.0.0.1:8000> and log in with `judge` / `local-demo`.

Without Docker, use the normal Python quickstart in the
[README](../README.md#quickstart).

---

## Environment variables for a public demo

| Variable | Recommended | Why |
| --- | --- | --- |
| `DEMO_MODE` | `true` | Enables reset, sample packages and tamper test |
| `DATA_DIR` | `/data` | Persistent volume for SQLite and uploads |
| `BASIC_AUTH_USER` | `judge` | Public-demo username |
| `BASIC_AUTH_PASSWORD` | strong secret | Public-demo password |
| `PAYPAL_FORCE_MOCK` | `true` | Guarantees no accidental sandbox/live calls |
| `PAYPAL_ENV` | `sandbox` | Only relevant if you add credentials |
| `ALLOW_LIVE` | `false` | Blocks live money even with live credentials |
| `MAX_UPLOAD_MB` | `8` | Keeps uploads bounded |
| `APPROVAL_TTL_MINUTES` | `30` | Approval decision window |
| `APPROVAL_TOKEN_TTL_MINUTES` | `20` | Approved-token use window |

If you set `PAYPAL_FORCE_MOCK=false` and provide sandbox credentials, the app uses
the real sandbox Orders/Payouts APIs. The mock remains the default.

---

## How Basic auth works

- Auth turns on only when **both** `BASIC_AUTH_USER` and `BASIC_AUTH_PASSWORD`
  are set. If only one is set, startup logs a warning and auth stays off.
- Every path is protected, including `/`, `/docs`, `/static` and `/api/*`.
- Browsers show a native login prompt and then send the header on subresource and
  SSE requests automatically.
- `curl` example:

  ```bash
  curl -u judge:'<password>' https://YOUR-SITE/api/state
  ```

- Two endpoints stay public:
  - `/api/health` — platform health checks;
  - `/api/webhooks/paypal` — PayPal cannot send a browser login. The webhook
    signature is verified inside the service, and unverified events are audited
    as errors rather than trusted. In mock mode an event is trusted only when a
    dummy `PAYPAL_WEBHOOK_ID` is deliberately configured; without one, the event
    is recorded for the audit trail but never changes payment state.
- This is a single shared login, not a user system. It is enough to keep a demo
  private; use your platform's SSO or a reverse proxy for real multi-user access.

---

## Data and persistence

The app writes two things under `DATA_DIR`:

```text
/data/mandatepay.sqlite3     SQLite database (money state + audit chain)
/data/uploads/               uploaded deliverables
```

A persistent volume mounted at `/data` keeps both across restarts. The audit
chain is inside the database, so it survives too.

Backup is a file copy:

```bash
docker cp mandatepay:/data/mandatepay.sqlite3 ./backup.sqlite3
docker cp mandatepay:/data/uploads ./backup-uploads
```

`POST /api/demo/reset` deliberately wipes all demo state. Keep `DEMO_MODE=true`
for judges; set it to `false` if you want a longer-lived instance without reset
and tamper endpoints.

---

## SSE, cold starts and timeouts

- The UI uses `/api/stream` for live updates. If a proxy buffers responses, the
  page still works but updates appear only after a refresh. Disable buffering
  (`X-Accel-Buffering: no` is already sent, and Caddy needs `flush_interval -1`).
- Render's free tier sleeps after inactivity. The first request can take 30–60
  seconds while the container starts.
- Use the platform's `$PORT` value. The Docker command already does:
  `uvicorn ... --port ${PORT:-8000}`.
- A single Uvicorn worker is intentional. Multiple workers would each open the
  same SQLite file and split the in-process SSE bus.

---

## Security checklist

- [ ] Strong `BASIC_AUTH_PASSWORD`; do not commit `.env`.
- [ ] `ALLOW_LIVE` unset/false.
- [ ] `PAYPAL_ENV=sandbox` if credentials are configured.
- [ ] `PAYPAL_FORCE_MOCK=true` unless you are actively testing the sandbox.
- [ ] HTTPS only (automatic on Render/Railway/Fly/Caddy).
- [ ] `PAYPAL_WEBHOOK_ID` set if the webhook endpoint is public; unverified events
      are recorded but never applied.
- [ ] Only demo files uploaded; the app has no malware scanner.
- [ ] One instance, persistent volume, regular database backup.
- [ ] `DEMO_MODE=false` for a long-lived deployment that should not expose reset
      or tamper controls.
- [ ] Read [SECURITY.md](../SECURITY.md) before exposing real credentials.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Browser keeps asking for a password | Credentials do not match the env vars | Check `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` and redeploy |
| `401` on every page but `/api/health` is `200` | Auth is working as designed | Log in with the Basic auth prompt |
| Auth is not enforced | Only one env var is set | Set both; check startup logs for the warning |
| Demo resets after a restart | No persistent disk | Mount a volume at `/data` and set `DATA_DIR=/data` |
| Header says **PayPal mock** | Credentials missing, incomplete, or forced mock | Set both credentials and `PAYPAL_FORCE_MOCK=false`, then redeploy |
| Webhook is `unverified` | Wrong `PAYPAL_WEBHOOK_ID` or environment | Copy the ID from the matching sandbox webhook |
| Live updates are stuck | Proxy buffering SSE | Disable buffering / use `flush_interval -1` |
| Build runs out of memory | Host too small | Use a larger instance or the Python buildpack instead |
| Container exits immediately | `$PORT` not respected | Use the included `Dockerfile` start command |
