import json
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BatchGenerateWizard(models.TransientModel):
    _name = "templateto.batch.generate.wizard"
    _description = "TemplateTo Batch PDF Generation Wizard"

    report_id = fields.Many2one(
        "ir.actions.report",
        string="Report Template",
        required=True,
        domain="[('model', '=', model_name)]",
    )
    model_name = fields.Char(string="Model", readonly=True)
    record_count = fields.Integer(string="Records Selected", readonly=True)
    res_ids = fields.Text(string="Record IDs (JSON)", readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_model = self.env.context.get("active_model")
        active_ids = self.env.context.get("active_ids", [])

        if not active_model or not active_ids:
            raise UserError("No records selected for batch generation.")

        res["model_name"] = active_model
        res["record_count"] = len(active_ids)
        res["res_ids"] = json.dumps(active_ids)

        # Pre-select the first available report for this model
        reports = self.env["ir.actions.report"].search(
            [("model", "=", active_model), ("report_type", "=", "qweb-pdf")],
            limit=1,
        )
        if reports:
            res["report_id"] = reports.id

        return res

    def action_generate(self):
        """Create a batch job and start processing."""
        self.ensure_one()

        if not self.env["ir.actions.report"]._templateto_enabled():
            raise UserError(
                "TemplateTo is not enabled. Please configure it in "
                "Settings > Technical > TemplateTo."
            )

        records = self.env[self.model_name].browse(
            json.loads(self.res_ids)
        )
        return self.env["templateto.batch.job"].create_from_records(
            records, self.report_id.report_name
        )

    @classmethod
    def create_from_records(cls, env, records, report_ref):
        """Convenience class method to open the wizard pre-filled."""
        return env["templateto.batch.job"].create_from_records(
            records, report_ref
        )
