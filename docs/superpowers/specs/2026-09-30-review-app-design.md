# Review app: hierarchical navigation and error marking

Date: 2026-09-30 · Status: approved in conversation

## Purpose

Browse the hierarchy live and mark classification errors on any element.
Marks feed overrides, quality measurement per level and method, and
targeted re-assessment.

## Decisions

- Local web app `handdown serve` (stdlib `http.server`, no new dependency),
  bound to 127.0.0.1, run on the host (shared workspace and database).
- Levels: **image** (pictogram) → **style group** → **depiction** →
  **object/symbol** → **meaning**, plus composite parts.

## Pages (each shows a sample of what is inside)

| Route | Shows |
|---|---|
| `/` | search; most established meanings with 6-image samples |
| `/meaning/<id>` | objects drawing the meaning (share of sources), 8 images each; applied-to list |
| `/object/<id>` | depictions (view, features) with representative images; used-to-mean |
| `/depiction/<id>` | style groups with 6 images each; meanings |
| `/group/<id>` | all images of the style group (paged) |
| `/image/<id>` | image at 16/24/48/96 px, metadata, source, composite parts, breadcrumbs up the hierarchy |
| `/svg/<sha>.svg` | normalized (sanitized) SVG |

## Error marks

Every element has a ⚑ button → form: which levels are wrong
(style group / depiction / object / meaning / composite), an optional
correct value and a note. `POST /feedback` stores a row in `feedback`
(target kind, id, levels, correct value, note, snapshot of the current
classification, created_at, status open). `handdown feedback` summarises
open marks per level and per method (rules / vision / ai).

## Security

- Listens on 127.0.0.1 only; requests with a foreign `Host` are refused
  (DNS rebinding), and POSTs need an `Origin` of the app itself (CSRF from
  other sites).
- All text is HTML-escaped; SVGs are the sanitized normalized files and are
  shown through `<img>` (no script execution); responses carry a CSP.

## Testing

- Pages render with samples on a fixture catalog; names with markup are
  escaped.
- Feedback POST stores the row with a snapshot; foreign Origin/Host → 403.
- `feedback` summary counts.
