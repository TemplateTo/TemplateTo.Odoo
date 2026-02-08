import requests as http_requests

from odoo import models, fields
from odoo.exceptions import UserError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    templateto_enabled = fields.Boolean(
        string="Enable TemplateTo PDF Rendering",
        config_parameter="templateto.enabled",
        default=False,
    )
    templateto_api_key = fields.Char(
        string="API Key",
        config_parameter="templateto.api_key",
        help="Your TemplateTo API key. Get one at https://app.templateto.com",
    )
    templateto_api_endpoint = fields.Char(
        string="API Endpoint",
        config_parameter="templateto.api_endpoint",
        default="https://api.templateto.com",
    )
    templateto_fallback_enabled = fields.Boolean(
        string="Fallback to wkhtmltopdf on failure",
        config_parameter="templateto.fallback_enabled",
        default=True,
    )
    templateto_batch_threshold = fields.Integer(
        string="Batch threshold (records)",
        config_parameter="templateto.batch_threshold",
        default=20,
        help="Above this number of records, use background batch generation.",
    )

    def action_templateto_test_connection(self):
        """Test connection to the TemplateTo API with minimal HTML."""
        self.ensure_one()
        api_key = self.templateto_api_key
        api_endpoint = self.templateto_api_endpoint or "https://api.templateto.com"

        if not api_key:
            raise UserError("Please enter an API key first.")

        import base64
        test_html = "<html><body><p>TemplateTo connection test</p></body></html>"
        b64_html = base64.b64encode(test_html.encode("utf-8")).decode("ascii")

        try:
            resp = http_requests.post(
                f"{api_endpoint}/render/pdf/fromhtml",
                json={"base64HtmlString": b64_html},
                headers={
                    "X-API-KEY": api_key,
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
        except http_requests.exceptions.ConnectionError:
            raise UserError(
                "Could not connect to TemplateTo API at %s. "
                "Please check the endpoint URL." % api_endpoint
            )
        except http_requests.exceptions.Timeout:
            raise UserError(
                "Connection to TemplateTo API timed out. Please try again."
            )

        if resp.status_code == 200 and resp.content:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Connection Successful",
                    "message": "TemplateTo API is reachable and your API key is valid.",
                    "type": "success",
                    "sticky": False,
                },
            }
        elif resp.status_code in (401, 403):
            raise UserError(
                "Authentication failed (HTTP %d). "
                "Please check your API key." % resp.status_code
            )
        else:
            raise UserError(
                "TemplateTo API returned HTTP %d: %s"
                % (resp.status_code, resp.text[:500])
            )
