# TemplateTo Reports — Odoo Module

Replace Odoo's default **wkhtmltopdf** PDF engine with **TemplateTo's Chromium-based rendering API**.

## What it does

- **Drop-in replacement**: existing QWeb report templates work unchanged
- **Modern CSS**: full support for flexbox, grid, media queries, web fonts
- **Reliable output**: no more blank PDFs, broken headers/footers, or timeout crashes
- **Sync rendering**: Print button works instantly (single records or small batches)
- **Async batch generation**: background processing for 20+ records with progress tracking
- **Automatic fallback**: falls back to wkhtmltopdf if the API is unreachable

## Requirements

- Odoo 18.0 (Community or Enterprise)
- A TemplateTo account and API key ([sign up](https://app.templateto.com))

## Installation

1. Clone or copy this module into your Odoo addons path
2. Update the Apps List: **Settings → Technical → Update Apps List**
3. Search for "TemplateTo" and install

## Configuration

1. Go to **Settings → TemplateTo**
2. Enable "Use TemplateTo for PDF Rendering"
3. Enter your API key
4. Click **Test Connection** to verify
5. Save

### Settings

| Setting | Default | Description |
|---------|---------|-------------|
| Enable TemplateTo | Off | Global toggle for the PDF engine |
| API Key | — | Your TemplateTo API key |
| Fallback to wkhtmltopdf | On | Auto-fallback on API failure |
| Batch threshold | 20 | Records above this trigger background generation |
| API Endpoint | `https://api.templateto.com` | Override only if directed by support |

## How it works

When enabled, the module overrides `ir.actions.report._render_qweb_pdf()`:

1. Odoo renders the QWeb template to HTML (as it normally does)
2. The HTML is base64-encoded and sent to the TemplateTo API
3. TemplateTo renders the HTML using Chromium and returns PDF bytes
4. The PDF is returned to Odoo as if wkhtmltopdf had generated it

For batch operations (20+ records), the module uses TemplateTo's async API with background polling via `ir.cron`.

## External service disclosure

This module sends **rendered report HTML only** to the TemplateTo API for PDF conversion. No Odoo credentials, database names, user data, or metadata are transmitted. The module is disabled by default and requires explicit opt-in.

## License

LGPL-3 — see [LICENSE](LICENSE).
