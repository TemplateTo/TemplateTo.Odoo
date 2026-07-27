{
    "name": "TemplateTo Reports",
    "version": "19.0.1.0.0",
    "category": "Technical/Reporting",
    "summary": "Replace wkhtmltopdf with modern Chrome-based PDF rendering",
    "description": """
        This module replaces Odoo's default wkhtmltopdf PDF engine with
        TemplateTo's Chromium-based rendering API. Get modern CSS support
        (flexbox, grid), reliable headers/footers, and no more blank PDFs.

        Features:
        - Drop-in replacement: existing QWeb templates work unchanged
        - Sync rendering for interactive Print button
        - Async batch generation for 20+ records (month-end invoices, bulk emails)
        - Background processing with progress tracking
        - Automatic fallback to wkhtmltopdf on API failure

        EXTERNAL SERVICE: This module sends report HTML to the TemplateTo
        API (https://api.templateto.com) for PDF conversion. A TemplateTo
        account, active paid plan, and API key are required. The addon is
        free and open source, but TemplateTo service usage is billed
        separately. No Odoo data beyond the rendered report HTML is
        transmitted.
    """,
    "author": "TemplateTo",
    "website": "https://templateto.com",
    "support": "david@templateto.com",
    "license": "LGPL-3",
    "depends": ["base", "mail", "account"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "data/ir_cron.xml",
        "data/ir_actions_server.xml",
        "views/res_config_settings.xml",
        "views/templateto_batch_job_views.xml",
        "wizard/batch_generate_wizard.xml",
    ],
    "external_dependencies": {
        "python": ["requests"],
    },
    "images": [
        "static/description/banner.png",
        "static/description/screenshot_settings.png",
        "static/description/screenshot_invoice.png",
        "static/description/screenshot_invoice_with_pdf.png",
        "static/description/screenshot_batch.png",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
