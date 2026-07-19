# GESH Financial — Privacy Policy (DRAFT)
> **DRAFT — NOT YET LEGALLY REVIEWED.** Have a qualified attorney review and finalize before publishing. Placeholders in **[BRACKETS]** need your real values. This skeleton exists so counsel redlines a draft (cheaper) instead of drafting from scratch.

**Effective date:** 2026-07-19
**Who we are:** GESH Financial ("GESH", "we", "us"), operated by [LEGAL ENTITY / SOLE PROPRIETOR NAME], [MICHIGAN, USA]. Contact: [PRIVACY@YOURDOMAIN].

## 1. Scope
This policy explains what personal information we collect when you use the GESH website and application at pay.geshfinance.com, why we collect it, who we share it with, and your rights.

## 2. Information we collect
- **Account data:** email address; authentication data (password stored only as a salted hash — we never store your plaintext password).
- **Financial inputs you enter:** the holdings, quantities, and watchlist symbols you add to "My Book." These are **positions you type in**, not a connection to your brokerage.
- **Usage data:** feature usage/metering events (e.g., which analyses you ran), timestamps, and basic technical logs.
- **Payment data:** processed by **Lemon Squeezy** (our merchant of record). We do **not** store your full card details; we receive subscription status and limited billing metadata from Lemon Squeezy.
- **Communications:** emails you send us and email-preference choices.

## 3. How we use your information
- To provide the research/analytics features you request.
- To operate accounts, authentication, and subscriptions.
- To send you service and (if opted in) signal-change/digest emails.
- To meter usage and enforce plan limits.
- To secure the service and comply with law.

## 4. AI processing — important disclosure
When you run an analysis, the relevant inputs (including symbols and, for "My Book," your entered holdings) may be sent to **third-party AI providers — Anthropic (Claude) and Google (Gemini)** — to generate the research output. We send the minimum needed for the feature. These providers process the data under their own terms; per their current API terms they do not use API inputs to train their models, but we do not control their systems. If you do not want your inputs processed by third-party AI, do not use the AI analysis features.

## 5. Who we share with (processors / sub-processors)
- **Lemon Squeezy** — payments/subscriptions (merchant of record).
- **Anthropic, Google** — AI analysis (see §4).
- **Resend** — transactional/alert email delivery.
- **Market/data providers** — we send them queries (e.g., ticker symbols), not your identity, to fetch market data.
- We do **not sell** your personal information.
- We may disclose information if required by law or to protect our rights.

## 6. Data retention
We keep account and financial-input data while your account is active and for [RETENTION PERIOD, e.g., 12 months] after closure, unless a longer period is required by law. Usage logs are kept for [PERIOD]. You can request earlier deletion (§8).

## 7. Security
Passwords are hashed (PBKDF2). Sessions use signed tokens. We restrict access and take reasonable technical measures to protect your data. No system is perfectly secure; we cannot guarantee absolute security.

## 8. Your rights
Depending on where you live (e.g., California/CCPA-CPRA, or the EU/UK/GDPR), you may have rights to **access, export, correct, or delete** your personal information, and to opt out of certain processing. To exercise these, email [PRIVACY@YOURDOMAIN] or use the in-app **Export my data** / **Delete my account** controls. We will respond within the legally required timeframe. We will not discriminate against you for exercising these rights.

- **Access/Export:** you can export the positions and analyses tied to your account.
- **Deletion:** you can delete your account and associated financial inputs.
- **Email preferences:** unsubscribe links in every marketing email; service emails are required while your account is active.

## 9. Cookies & analytics
We use [strictly-necessary cookies for login sessions] and [ANALYTICS, IF ANY]. [Cookie banner / preferences description if you add analytics.]

## 10. Children
GESH is not directed to anyone under 18 and we do not knowingly collect data from minors.

## 11. International users
[Decide: either "GESH is intended for US users only; do not use if you are in the EU/UK," OR add a full GDPR section. Geo-blocking the EU is the simplest path for a solo operator unless you want EU customers.]

## 12. Changes
We may update this policy; the effective date reflects the latest version and we will notify you of material changes.

## 13. Contact
[PRIVACY@YOURDOMAIN] · [MAILING ADDRESS if required by applicable law].

---
### Engineering to-do to back this policy (Claude can implement)
- **In-app "Export my data"** endpoint/button (primitives exist: `export_portfolio.py`).
- **In-app "Delete my account"** flow (primitives exist: `clear_positions`, account deactivation) — hard-delete or anonymize per counsel.
- **Footer links** to /privacy, /terms, /disclaimer on every page.
- **AI-disclosure line** near the "My Book" analyze button ("Your inputs are processed by third-party AI — see Privacy Policy").
