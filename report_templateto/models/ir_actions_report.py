import base64
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from odoo import models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 60
MAX_PARALLEL = 6  # Matches TemplateTo rate limit (6 tokens)


class IrActionsReport(models.Model):
    _inherit = "ir.actions.report"

    # ------------------------------------------------------------------
    # Public override
    # ------------------------------------------------------------------

    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        """Override to route PDF rendering through TemplateTo API.

        When TemplateTo is enabled (toggle ON *and* API key configured),
        QWeb-rendered HTML is sent to the TemplateTo sync endpoint for
        PDF conversion.  On failure the behaviour depends on the fallback
        setting: if enabled, we fall back silently to wkhtmltopdf and log
        a warning; if disabled, we raise a ``UserError``.
        """
        if not self._templateto_enabled():
            return super()._render_qweb_pdf(
                report_ref, res_ids=res_ids, data=data
            )

        try:
            return self._render_via_templateto(report_ref, res_ids, data)
        except Exception as exc:
            if self._templateto_fallback_enabled():
                _logger.warning(
                    "TemplateTo API failed, falling back to wkhtmltopdf: %s",
                    exc,
                )
                return super()._render_qweb_pdf(
                    report_ref, res_ids=res_ids, data=data
                )
            raise UserError(
                "TemplateTo PDF rendering failed: %s\n\n"
                "Enable fallback in Settings \u2192 Technical \u2192 TemplateTo, "
                "or check your API key." % str(exc)
            ) from exc

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    def _templateto_enabled(self):
        """Return ``True`` when the module is enabled **and** an API key
        is configured.  Both conditions must be met — if the key is
        missing we silently fall through to wkhtmltopdf.
        """
        ICP = self.env["ir.config_parameter"].sudo()
        enabled = ICP.get_param("templateto.enabled", "False")
        api_key = ICP.get_param("templateto.api_key", "")
        return enabled == "True" and bool(api_key)

    def _templateto_fallback_enabled(self):
        """Return ``True`` when wkhtmltopdf fallback is turned on."""
        ICP = self.env["ir.config_parameter"].sudo()
        return ICP.get_param("templateto.fallback_enabled", "True") == "True"

    def _templateto_api_config(self):
        """Return ``(api_endpoint, api_key)`` from system parameters."""
        ICP = self.env["ir.config_parameter"].sudo()
        return (
            ICP.get_param(
                "templateto.api_endpoint", "https://api.templateto.com"
            ),
            ICP.get_param("templateto.api_key", ""),
        )

    # ------------------------------------------------------------------
    # Sync rendering
    # ------------------------------------------------------------------

    def _render_via_templateto(self, report_ref, res_ids, data):
        """Render QWeb HTML, send to TemplateTo sync API, return PDF.

        For a single document (or when Odoo already merges records into
        one HTML blob) we make one API call.  For multiple records we
        render each one individually and send them in parallel (up to
        ``MAX_PARALLEL`` concurrent requests), then merge the resulting
        PDFs.
        """
        if res_ids and len(res_ids) > 1:
            return self._render_via_templateto_parallel(
                report_ref, res_ids, data
            )

        # --- single document path ---
        html_bytes = self._get_html_bytes(report_ref, res_ids, data)
        b64_html = base64.b64encode(html_bytes).decode("ascii")
        api_endpoint, api_key = self._templateto_api_config()

        pdf_bytes = self._templateto_render_single(
            api_endpoint, api_key, b64_html
        )
        return pdf_bytes, "pdf"

    def _render_via_templateto_parallel(self, report_ref, res_ids, data):
        """Render multiple records in parallel and merge into one PDF."""
        api_endpoint, api_key = self._templateto_api_config()

        # Render each record's HTML individually
        futures = {}
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
            for res_id in res_ids:
                html_bytes = self._get_html_bytes(
                    report_ref, [res_id], data
                )
                b64_html = base64.b64encode(html_bytes).decode("ascii")
                future = pool.submit(
                    self._templateto_render_single,
                    api_endpoint,
                    api_key,
                    b64_html,
                )
                futures[future] = res_id

        # Collect results in original order
        results = {}
        for future in as_completed(futures):
            res_id = futures[future]
            results[res_id] = future.result()  # propagates exceptions

        # Merge PDFs in the order of res_ids
        pdf_pages = [results[rid] for rid in res_ids]
        merged = self._merge_pdfs(pdf_pages)
        return merged, "pdf"

    # ------------------------------------------------------------------
    # HTML extraction helper
    # ------------------------------------------------------------------

    def _get_html_bytes(self, report_ref, res_ids, data):
        """Call ``_render_qweb_html`` and return raw ``bytes``."""
        html_result = self._render_qweb_html(report_ref, res_ids, data=data)
        # _render_qweb_html may return (html, 'html') tuple or just html
        html_data = (
            html_result[0] if isinstance(html_result, tuple) else html_result
        )
        if isinstance(html_data, str):
            html_data = html_data.encode("utf-8")
        return html_data

    # ------------------------------------------------------------------
    # API call (thread-safe, no ORM usage)
    # ------------------------------------------------------------------

    @staticmethod
    def _templateto_render_single(api_endpoint, api_key, b64_html):
        """POST base64-encoded HTML to the TemplateTo sync endpoint.

        This is a plain ``requests`` call with no ORM access, making it
        safe to run from a ``ThreadPoolExecutor`` worker thread.

        Returns raw PDF ``bytes`` on success; raises on any error.
        """
        resp = requests.post(
            f"{api_endpoint}/render/pdf/fromhtml",
            json={"base64HtmlString": b64_html},
            headers={
                "X-API-KEY": api_key,
                "Content-Type": "application/json",
            },
            timeout=TIMEOUT_SECONDS,
        )

        if resp.status_code in (401, 403):
            raise UserError(
                "TemplateTo authentication failed (HTTP %d). "
                "Please check your API key in Settings \u2192 Technical "
                "\u2192 TemplateTo." % resp.status_code
            )

        if resp.status_code != 200:
            raise Exception(
                "TemplateTo API returned HTTP %d: %s"
                % (resp.status_code, resp.text[:500])
            )

        if not resp.content:
            raise Exception("TemplateTo API returned an empty PDF response.")

        return resp.content

    # ------------------------------------------------------------------
    # PDF merging
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_pdfs(pdf_list):
        """Merge a list of raw PDF byte strings into a single PDF.

        Uses PyPDF2 if available (common in Odoo environments), falls
        back to simple concatenation-safe pypdf, or as a last resort
        returns the first PDF with a warning.
        """
        if len(pdf_list) == 1:
            return pdf_list[0]

        # Try PyPDF2 first (ships with many Odoo installs)
        try:
            from PyPDF2 import PdfMerger
            import io

            merger = PdfMerger()
            for pdf_bytes in pdf_list:
                merger.append(io.BytesIO(pdf_bytes))
            output = io.BytesIO()
            merger.write(output)
            merger.close()
            return output.getvalue()
        except ImportError:
            pass

        # Try pypdf (newer replacement for PyPDF2)
        try:
            from pypdf import PdfMerger as PypdfMerger
            import io

            merger = PypdfMerger()
            for pdf_bytes in pdf_list:
                merger.append(io.BytesIO(pdf_bytes))
            output = io.BytesIO()
            merger.write(output)
            merger.close()
            return output.getvalue()
        except ImportError:
            pass

        # Last resort: return just the first PDF and log a warning
        _logger.warning(
            "Neither PyPDF2 nor pypdf is installed — cannot merge %d PDFs. "
            "Returning only the first document. Install PyPDF2: "
            "pip install PyPDF2",
            len(pdf_list),
        )
        return pdf_list[0]
