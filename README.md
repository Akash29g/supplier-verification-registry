# Supplier Verification Registry

Know who you're paying before the money leaves.

A serverless app that checks a supplier's GSTIN against live Indian government
records, scores the result, flags fraud rings across your supplier list, and
keeps re-checking after the first verification — instead of leaving you to
read a raw government API response once and forget about it.

Built solo for **WeMakeDevs × AWS "First Commit"** (Bharat Builds Tour), Sept
17–20, 2026.

**Live app:** https://main.dqyhm0mdxlkym.amplifyapp.com

**Demo video:** https://youtu.be/K25UTbknLR4?si=FWL9xkVDJwoGKKmW

---

## The problem

India has ~63 million registered SMEs, and every one of them onboards new
suppliers by hand. The only way to verify a GSTIN today is to open the
government's GST portal, type it in manually, solve a captcha, and read a
page of raw fields — and that's *if* you remember to do it at all. Nothing
checks whether two "different" suppliers you're paying actually share a bank
account. Nothing tells you if a supplier that passed a check six months ago
is still registered today. This app does all three, automatically, for
whatever it costs to run a Lambda function.

---

## What it actually does

- **Live verification** — enter a GSTIN, get a real answer from
  gstincheck.co.in (falling back to data.gov.in's Company Master Data) in
  under a second, not 45 seconds and a captcha.
- **A visible multi-agent pipeline** — a verification agent does the lookup,
  a risk agent scores it. Every step either agent takes is logged and
  replayed live in the UI, not just claimed in a README.
- **Fraud-ring detection** — the risk agent doesn't just look at one
  supplier. It re-runs across everything you've verified, so if two
  supplier entries share a bank account or address, both get flagged —
  visualized as a live, force-directed graph on `/fraud-network`.
- **An AI-generated plain-language summary** on every report, via Amazon
  Bedrock (Nova Lite) — with a template fallback if the model call fails, so
  a verification never errors out because of the AI step.
- **It keeps watching.** An EventBridge schedule re-verifies every live
  supplier nightly, updates trust scores, and automatically revokes any
  public badge (see below) that's no longer earned.
- **Public trust badges** — a Verified supplier can get a shareable,
  no-login-required badge link that's kept honest automatically by the
  nightly re-check.
- **Bulk verification** — paste or upload a list of GSTINs and check them
  all in one run, with a live progress bar and CSV export.
- **PDF export** of any report, via the browser's native print-to-PDF.
- **Real accounts** — signup/login/email confirmation via Amazon Cognito,
  every protected endpoint validated server-side.

---

## Architecture

```
┌─────────────┐      ┌──────────────────┐      ┌─────────────────────────┐
│  Frontend    │      │   API Gateway    │      │     AWS Lambda           │
│  (single     │─────▶│   (REST, CORS)   │─────▶│  Python 3.13, one        │
│  page app,   │      │                  │      │  function per endpoint  │
│  hash-routed)│      └──────────────────┘      └───────────┬─────────────┘
└──────┬───────┘                                             │
       │                                          ┌──────────┼──────────┐
       │ hosted on                                ▼          ▼          ▼
       ▼                                   ┌───────────┐ ┌────────┐ ┌─────────┐
┌─────────────┐                            │ DynamoDB  │ │Cognito │ │ Bedrock │
│ AWS Amplify │                            │ (5 tables,│ │ (auth) │ │(Nova    │
│  Hosting    │                            │  pay-per- │ │        │ │ Lite,   │
└─────────────┘                            │  request) │ │        │ │cross-   │
                                            └───────────┘ └────────┘ │account  │
                                                                     │ role)   │
                                                                     └─────────┘
                        ┌──────────────────┐
                        │   EventBridge     │───▶ nightly re-check Lambda
                        │  (cron, 02:00 IST)│      (re-verify + revoke badges)
                        └──────────────────┘
```

**AWS services used, deployed and live (not local-only):**

| Service | What it's doing here |
|---|---|
| **AWS Lambda** (Python 3.13) | One function per endpoint — auth, verify, reads, badges, demo seed, nightly re-check. |
| **Amazon API Gateway** | REST API in front of every Lambda, CORS-enabled for the frontend. |
| **Amazon DynamoDB** | 5 pay-per-request tables: Suppliers, VerificationLog, Users, Stats, Badges. Zero idle cost. |
| **Amazon Cognito** | User pool for signup / login / email confirmation. Every protected endpoint validates the bearer token directly against Cognito. |
| **Amazon Bedrock** (Nova Lite) | Generates the plain-language report summary. Invoked via a cross-account IAM role (see *What I learned* below) with a template fallback on failure. |
| **Amazon EventBridge** | Nightly scheduled rule that re-verifies every live supplier and syncs badge state. |
| **AWS Amplify Hosting** | Serves and rebuilds the static frontend on every push to `main`. |
| **AWS SAM** | Infrastructure as code for everything above (`infra/template.yaml`). |

**Also included, for the Build It track:** `agents/strands_wrapper.py` exposes
the verification pipeline as **Strands Agents SDK** tools — runs locally, no
AWS account required. Deliberately kept small; the deployed stack above is
the actual build priority.

---

## The two agents

Every verification runs two agents in sequence, and their steps stream live
in the UI trace:

1. **Verification agent** (`agents/verification_agent.py`) — runs an
   offline GSTIN format + checksum validation first, so a malformed number
   never reaches an external API. Then looks the GSTIN up against
   gstincheck.co.in, falling back to data.gov.in's Company Master Data.
2. **Risk agent** (`agents/risk_agent.py`) — starts every supplier at a
   100-point trust score.
   - *Own findings* look at that one supplier: registration status, how
     recently it registered, mismatches between what was declared and what's
     registered.
   - *Ring findings* re-run across **every** supplier in the account, so
     adding a new supplier can retroactively flag an older one the moment
     they turn out to share a bank account or address — this is what feeds
     `/fraud-network`.

## Verdicts

| Verdict | Meaning |
|---|---|
| **Verified** | No material findings — safe to pay. |
| **Needs review** | Minor findings (e.g. very recently registered). |
| **High risk** | Material findings — most often a shared bank account or address with another supplier in the account. |
| **Rejected** | The GSTIN itself is malformed or fails the checksum. Never reaches the government API. |

---

## Cost

Every part of this stack scales to zero and is billed pay-per-request:
Lambda, DynamoDB on-demand, API Gateway, and Bedrock's per-token pricing for
a small model. Back-of-envelope cost per verification (Lambda execution +
DynamoDB writes + one Nova Lite call) comes out to **under ₹0.01**, and
nothing is provisioned or billed while idle.

---

## What I didn't build

Being upfront about scope, since a hackathon judge should be able to trust
the README:

- **No Step Functions orchestration.** Bulk verification calls the existing
  `/suppliers/verify` endpoint once per GSTIN sequentially from the browser,
  not a state-machine fan-out. It works and is honest about being simple.
- **No SNS/SES notifications.** The nightly re-check updates data and badges
  silently; it doesn't email or text anyone when a supplier's status
  changes.
- **No WhatsApp integration.**
- **The two seed data sets are intentionally not live lookups on every
  load** — 5 real companies are cached from an earlier live fetch
  (`data_origin: live_api`), and 5–10 fraud cases are hand-crafted and
  explicitly labelled `crafted_test_case` everywhere they appear in the UI.
  They exist to reliably exercise the risk agent's fraud-ring logic in a
  demo without depending on a third-party API succeeding on the first try —
  this is a standard hackathon technique for proving a detection system
  actually detects something, not an attempt to hide synthetic data as real.
- **Bedrock runs in a second AWS account**, invoked via a cross-account
  IAM role, because Bedrock model access was blocked on the primary AWS
  account for reasons outside my control at the time of building. This is a
  standard, supported AWS pattern (`sts:AssumeRole` + a scoped inline
  policy), not a workaround of anything the hackathon rules restrict — see
  *What I learned* below.

---

## What I learned

- **GSTIN structure and checksum validation** — the actual mod-36 checksum
  math, not just regex-shaped validation.
- **Cross-account IAM roles for Bedrock access** — when the model wasn't
  approved on my primary account, the fix wasn't to move the whole project;
  it was a scoped `AssumeRole` policy so only the Bedrock call crosses
  accounts, and everything else (Lambda, DynamoDB, Cognito) stays where it
  is.
- **Cognito token validation from Lambda** without a full Amplify Auth SDK
  on the frontend — a plain bearer token checked with `cognito-idp:GetUser`
  per request.
- **Cross-record fraud detection** as a re-derived view, not stored state —
  ring findings are recomputed from every row on each write rather than
  cached, which is what lets one new supplier retroactively flag an
  unrelated older one the moment a shared bank account appears.
- **EventBridge scheduled Lambdas** for a "keeps running after the demo"
  product, instead of everything being triggered by a user action.

---

## Setup

### Prerequisites
- AWS account with SAM CLI configured
- Python 3.13
- An API key from [gstincheck.co.in](https://gstincheck.co.in) (free tier)
- Optionally, a [data.gov.in](https://data.gov.in) API key for the fallback
  source

### Backend

```bash
cd infra
sam validate --lint
sam build
sam deploy --guided
```

You'll be prompted for the stack parameters — the two API keys above and,
if you're using cross-account Bedrock access, the ARN of the IAM role in the
account that has Bedrock model access:

```bash
sam deploy --parameter-overrides \
  GstincheckApiKey=<your key> \
  DataGovInApiKey=<your key> \
  BedrockRoleArn=arn:aws:iam::<account-id>:role/SupplierRegistryBedrockInvoke
```

Note the `ApiUrl` output — you'll need it for the frontend.

### Frontend

The frontend is a single static file (`frontend/index.html`), deployed via
AWS Amplify Hosting connected to this repo (see `amplify.yml`). Update the
`API_BASE` constant near the top of the `<script>` block to your deployed
`ApiUrl`, then push — Amplify rebuilds automatically.

### Tests

```bash
pip install -r requirements-dev.txt
pytest
```

### Environment variables (local dev only)

Copy `.env.example` to `.env` and fill in your keys. In deployment, these
are passed as SAM parameters instead (see above), not read from `.env`.

---

## API reference

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/auth/signup` | — | Create an account |
| POST | `/auth/confirm` | — | Confirm signup with the emailed code |
| POST | `/auth/login` | — | Log in, returns an access token |
| POST | `/suppliers/verify` | Bearer | Run a live verification + risk check on one GSTIN |
| GET | `/suppliers` | Bearer | List this account's suppliers with current verdicts |
| GET | `/history` | Bearer | List this account's verification runs, newest first |
| GET | `/reports/{log_id}` | Bearer | Full saved report for one verification run |
| GET | `/fraud-network` | Bearer | Nodes + edges of suppliers linked by a shared bank account or address |
| POST | `/demo/seed` | Bearer | Load 5 real companies + hand-crafted fraud cases into this account |
| POST | `/badges` | Bearer | Issue a public badge for a Verified supplier |
| GET | `/badges/{badge_id}` | — | Public badge page data |
| GET | `/stats` | — | Global, anonymous count of verifications run |

Full request/response shapes and the trust-score methodology are documented
in-app at `/docs`.

---

## Judging criteria cross-reference

| Criterion | Where it's addressed |
|---|---|
| **Idea & Impact** | Real, personally-felt problem (supplier/vendor GSTIN fraud). Live counter of real checks performed. Ongoing monitoring via the nightly re-check, not a one-time script. |
| **Built on AWS** | Lambda, API Gateway, DynamoDB, Cognito, Bedrock, EventBridge, Amplify Hosting, SAM — all deployed and live, not local-only. |
| **Learning** | GSTIN checksum math, cross-account Bedrock access via IAM roles, Cognito token validation, recomputed cross-record fraud detection, EventBridge scheduling — see *What I learned* above. |
| **Execution** | Multi-page app, persistent state, live real data, end-to-end tested, graceful failure handling (a flaky external API never breaks a check — it falls back or logs the failure honestly). |
| **Best UI** | A single restrained design system (editorial serif headings, mono for data, one accent color) applied consistently across every page, including the ones added under time pressure late in the build. |
| **Demo video** | See the video linked at the top of this README. |

---

## Repo structure

```
agents/       verification_agent, risk_agent, summary_agent, strands_wrapper
lambda/       one handler per API endpoint, common.py, store.py
infra/        template.yaml (AWS SAM)
frontend/     single-page app (index.html) — hash-routed, no build step
data/         seed_legit.json (cached live data), seed_fraud.json (crafted cases)
tests/        pytest suite for the agents and handlers
```
