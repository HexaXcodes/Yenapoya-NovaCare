# NovaCare — Rural NCD Screening Backend

A FastAPI backend for community-level **non-communicable disease (NCD) early
detection** in rural India. ASHA workers screen patients offline (IDRS
questionnaire + on-device voice and rPPG feature extraction), a composite risk
tier is computed, patients get plain-language localised reports and weekly
geo-localised dietary advice, and PHC doctors get a village-level risk heatmap
and outreach-campaign tools.

> **Privacy by design:** no raw audio is ever stored (only extracted feature
> vectors), patient identity is kept as an ABHA ID or a salted phone **hash**,
> and phone numbers never appear in logs (masked to the last 4 digits).

---

## Architecture

```
Frontends (React)        ASHA portal · Patient portal · Doctor portal
        │
On-device ML             Voice + rPPG inference (features only leave device)
        │
Backend (this repo)      FastAPI — layered: routers → services → repositories
        │
Data layer               SQLite (offline-first, on-device) ⇄ MongoDB (cloud)
        │
External                 i18n/TTS · ABHA/NHA bridge · FCM/SMS gateway
```

**Layering**

- `routers/` — HTTP surface, auth dependencies, request/response envelopes.
- `services/` — business logic. `risk_engine` and `diet_engine` are **pure &
  synchronous** so they are trivially unit-testable.
- `repositories/` — all MongoDB (Motor async) access.
- `models/` — document builders / serialisers + SQLAlchemy models for SQLite.
- `schemas/` — Pydantic v2 request/response models.
- `core/` — security (JWT/bcrypt/OTP/phone-hash), i18n, deps, response envelope.

Every endpoint returns a uniform envelope:

```json
{ "success": true, "data": { ... }, "error": null }
```

---

## Quick start

Requires **Python 3.11+**. (A running MongoDB is needed for the data
endpoints; the pure engines and the test suite run with no external services.)

```bash
# 1. install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. (optional) configure — defaults work out of the box for local dev
cp .env.example .env

# 3. run
uvicorn app.main:app --reload

# 4. open the interactive docs
#    http://localhost:8000/docs
```

By default `APP_ENV=dev`, which enables developer conveniences:
- `/auth/patient/otp` returns the OTP in the response (`dev_otp`) instead of
  only sending SMS.
- `/auth/asha/login` accepts any 4+ digit PIN and assigns a dev village.
- `SMS_PROVIDER`, `FCM_PROVIDER`, and `TTS_PROVIDER` default to `mock`
  (SMS/push are logged with masked numbers; TTS returns a valid silent WAV).

Set `APP_ENV=prod` and real provider credentials for production.

---

## Tests

The risk and diet engines have full unit coverage (47 tests):

```bash
pytest
```

Covered: composite-score formula and rounding, GREEN/AMBER/RED tier
boundaries, the prediabetes-trajectory heuristic and its labels, weekly advice
rotation and wrap-around, region/profile/income-band fallbacks, language
fallback to English, and the supplement/branded-product **safety guardrail**
(including a sweep asserting the entire shipped knowledge base is clean).

---

## Risk model

```
composite = idrs_score * 0.6  +  voice_score * 100 * 0.25  +  (15 if rppg_hrv_flag else 0)

GREEN  composite < 40       → routine follow-up in 6 months
AMBER  40 ≤ composite < 65  → glucometer / BP check this visit
RED    composite ≥ 65       → immediate referral + PHC doctor alert
```

A separate **prediabetes trajectory** (0–100, labelled `stable` / `rising` /
`high_risk`) is derived from waist circumference, dietary score, occupation
transition, and family history.

---

## Internationalisation

Supported languages: **hi, kn, ta, te, bn, mr, en**. Pass `?lang=` to any
endpoint that returns patient-facing text. Translations live in
`data/translations/{lang}.json`; missing keys fall back to English.

---

## Configuration

All settings come from environment variables (or `.env`). See
[`.env.example`](.env.example) for the full annotated list — JWT secret/expiry,
OTP, MongoDB URI/DB, SQLite URL, CORS origins, SMS/FCM/TTS providers, the
missed-follow-up threshold (`MISSED_FOLLOWUP_DAYS`, default 21), and the diet KB
and translations paths.

---

## API reference

All paths are relative to the server root. Auth is via `Authorization: Bearer
<jwt>`; the role required is shown per group.

### Auth
| Method | Path | Body |
| --- | --- | --- |
| POST | `/auth/asha/login` | `{asha_id, pin}` → JWT |
| POST | `/auth/patient/otp` | `{phone}` |
| POST | `/auth/patient/verify` | `{phone, otp}` → JWT |
| POST | `/auth/doctor/login` | `{email, password}` → JWT |

### Patients *(ASHA / Doctor)*
| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/patients/` | Create patient (ABHA **or** phone required) |
| GET | `/patients/{id}` | Fetch profile |
| PUT | `/patients/{id}` | Update profile |
| GET | `/patients/{id}/history` | All screening sessions |

### Screening sessions *(ASHA)*
| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/sessions/` | Create session (links patient + ASHA) |
| POST | `/sessions/{id}/idrs` | Submit IDRS answers |
| POST | `/sessions/{id}/voice` | Upload voice **features** (never raw audio) |
| POST | `/sessions/{id}/rppg` | Upload rPPG features (HRV, HR, RR) |
| GET | `/sessions/{id}/result` | Computed tier + action card |
| GET | `/sessions/{id}/report` | Plain-language report (`?lang=`) |

### Risk
| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/risk/compute` | `{idrs_score, voice_features, rppg_features}` → tier + breakdown |

### Diet
| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/diet/advice/{patient_id}` | Weekly rotating advice (`?lang=&region=`) |

### Follow-up
| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/followup/due` | ASHA's due visits for today |
| POST | `/followup/complete` | Mark a visit done |
| GET | `/followup/missed` | Patients overdue >21 days (sends SMS) |

### Heatmap *(Doctor)*
| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/heatmap/villages` | GeoJSON risk aggregates (`?district=&tier=&age_min=&age_max=&condition=&from_date=`) |
| GET | `/heatmap/trends` | Per-village time-series |

### Campaigns *(Doctor)*
| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/campaigns/` | Create outreach campaign |
| GET | `/campaigns/` | List campaigns for district |
| PUT | `/campaigns/{id}` | Update campaign |
| GET | `/campaigns/{id}/coverage` | Villages + ASHA assignments |

### Sync *(ASHA)*
| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/sync/push` | Push device SQLite queue → upsert by `local_id` |
| GET | `/sync/pull/{asha_id}` | Pull server-side updates since last sync |

Conflict resolution: **server wins** for doctor edits, **device wins** for ASHA
field data.

### Notifications *(internal — ASHA / Doctor)*
| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/notify/sms` | Send SMS via gateway |
| POST | `/notify/push` | Send FCM push |

### I18N / TTS
| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/i18n/languages` | Supported languages |
| POST | `/tts/generate` | `{text, lang}` → base64 audio |

### Health
| Method | Path |
| --- | --- |
| GET | `/health` |

---

## Scheduled jobs

An APScheduler cron (Asia/Kolkata, daily at 09:00) runs the missed-follow-up
sweep: it finds non-GREEN patients with no session in `MISSED_FOLLOWUP_DAYS`
days and sends a localised re-engagement SMS, logging each to the
`notifications` collection. The same logic is exposed on demand via
`GET /followup/missed`.

---

## Project layout

```
app/
  main.py            config.py        database.py        scheduler.py
  core/      security.py  i18n.py  deps.py  responses.py
  routers/   auth patients sessions risk diet followup heatmap
             campaigns sync notifications i18n
  services/  risk_engine diet_engine sync_service
             notification_service tts_service
  repositories/  patient_repo session_repo misc_repo
  models/    patient session campaign sqlite_models
  schemas/   auth patient session misc common
data/
  diet_advice.json
  translations/  hi kn ta te bn mr en .json
tests/
  test_risk_engine.py   test_diet_engine.py
```
