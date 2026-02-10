import base64
import io
import json
import logging
import zipfile

import requests

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class TemplateToBatchJob(models.Model):
    _name = "templateto.batch.job"
    _description = "TemplateTo Batch PDF Generation Job"
    _inherit = ["mail.thread"]
    _order = "create_date desc"

    name = fields.Char(string="Batch Name", required=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("submitting", "Submitting Jobs"),
            ("processing", "Processing"),
            ("done", "Completed"),
            ("failed", "Failed"),
        ],
        default="draft",
        tracking=True,
    )
    report_id = fields.Many2one("ir.actions.report", string="Report")
    model_name = fields.Char(string="Model")
    res_ids = fields.Text(string="Record IDs (JSON)")
    total_count = fields.Integer(string="Total Records")
    completed_count = fields.Integer(string="Completed")
    failed_count = fields.Integer(string="Failed")
    progress = fields.Float(string="Progress (%)", compute="_compute_progress")
    zip_attachment_id = fields.Many2one("ir.attachment", string="ZIP Download")
    error_log = fields.Text(string="Errors")
    job_line_ids = fields.One2many(
        "templateto.batch.job.line", "batch_id", string="Job Lines"
    )

    @api.depends("total_count", "completed_count", "failed_count")
    def _compute_progress(self):
        for rec in self:
            if rec.total_count:
                rec.progress = (
                    (rec.completed_count + rec.failed_count) / rec.total_count * 100
                )
            else:
                rec.progress = 0

    def action_start(self):
        """Mark batch as submitting so the cron picks it up."""
        self.write({"state": "submitting"})

    def action_cancel(self):
        """Cancel a running batch job."""
        self.write({"state": "failed"})
        self.message_post(
            body="Batch job cancelled by user.",
            message_type="notification",
        )

    def action_download_zip(self):
        """Return an action to download the ZIP attachment."""
        self.ensure_one()
        if not self.zip_attachment_id:
            return False
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % self.zip_attachment_id.id,
            "target": "new",
        }

    @api.model
    def create_from_records(self, records, report_ref):
        """Create a batch job from a recordset. Called from server action or wizard."""
        report = self.env["ir.actions.report"]._get_report(report_ref)
        if not report:
            raise models.ValidationError(
                "Report '%s' not found." % report_ref
            )

        batch = self.create(
            {
                "name": "%s - %s (%d records)"
                % (
                    report.name,
                    fields.Datetime.now().strftime("%Y-%m-%d %H:%M"),
                    len(records),
                ),
                "report_id": report.id,
                "model_name": records._name,
                "res_ids": json.dumps(records.ids),
                "total_count": len(records),
            }
        )
        # Submit to TemplateTo immediately instead of waiting for cron
        batch.action_start()
        try:
            batch._submit_async_jobs()
            # Poll immediately — if the API is fast, we can finalize
            # in this same request without waiting for the cron at all
            if batch.state == "processing":
                batch._poll_async_jobs()
        except Exception as e:
            _logger.error("Batch submit failed for %s: %s", batch.id, e)
            if batch.state not in ("done", "failed"):
                batch.write({"state": "failed", "error_log": str(e)})

        # If any jobs are still pending, trigger the cron to pick them up soon
        if batch.state == "processing":
            cron = self.env.ref(
                "report_templateto.ir_cron_templateto_batch_poll",
                raise_if_not_found=False,
            )
            if cron:
                cron._trigger()

        return {
            "type": "ir.actions.act_window",
            "res_model": "templateto.batch.job",
            "res_id": batch.id,
            "view_mode": "form",
            "target": "current",
        }

    @api.model
    def _cron_process_batch_jobs(self):
        """Cron entry point: poll processing jobs and fetch results.

        Submission now happens immediately in create_from_records(),
        so this cron only handles polling and finalization.
        Any jobs still in 'submitting' state (e.g. from a crash) are
        also picked up here as a safety net.
        """
        submitting = self.search([("state", "=", "submitting")])
        for batch in submitting:
            try:
                batch._submit_async_jobs()
            except Exception as e:
                _logger.error("Batch submit phase failed for %s: %s", batch.id, e)
                batch.write({"state": "failed", "error_log": str(e)})

        processing = self.search([("state", "=", "processing")])
        for batch in processing:
            try:
                batch._poll_async_jobs()
            except Exception as e:
                _logger.error("Batch poll phase failed for %s: %s", batch.id, e)

    def _submit_async_jobs(self):
        """Submit each record's HTML to TemplateTo async endpoint."""
        self.ensure_one()
        res_ids = json.loads(self.res_ids)
        report = self.report_id
        api_endpoint, api_key = self.env[
            "ir.actions.report"
        ]._templateto_api_config()

        errors = []
        for res_id in res_ids:
            try:
                html_result = report._render_qweb_html(
                    report.report_name, [res_id]
                )
                html_bytes = (
                    html_result[0]
                    if isinstance(html_result, tuple)
                    else html_result
                )
                if isinstance(html_bytes, str):
                    html_bytes = html_bytes.encode("utf-8")

                b64_html = base64.b64encode(html_bytes).decode("ascii")

                resp = requests.post(
                    "%s/render/async/pdf/fromhtml" % api_endpoint,
                    json={"base64HtmlString": b64_html},
                    headers={
                        "X-API-KEY": api_key,
                        "Content-Type": "application/json",
                    },
                    timeout=30,
                )

                if resp.status_code in (200, 202):
                    job_data = resp.json()
                    self.env["templateto.batch.job.line"].create(
                        {
                            "batch_id": self.id,
                            "res_id": res_id,
                            "templateto_job_id": job_data.get("jobId"),
                            "status_url": job_data.get("statusUrl"),
                            "result_url": job_data.get("resultUrl"),
                            "state": "submitted",
                        }
                    )
                else:
                    error_msg = "HTTP %d: %s" % (
                        resp.status_code,
                        resp.text[:200],
                    )
                    self.env["templateto.batch.job.line"].create(
                        {
                            "batch_id": self.id,
                            "res_id": res_id,
                            "state": "failed",
                            "error": error_msg,
                        }
                    )
                    self.failed_count += 1
                    errors.append("Record %s: %s" % (res_id, error_msg))
            except Exception as e:
                _logger.error(
                    "Batch submit error for record %s: %s", res_id, e
                )
                self.env["templateto.batch.job.line"].create(
                    {
                        "batch_id": self.id,
                        "res_id": res_id,
                        "state": "failed",
                        "error": str(e),
                    }
                )
                self.failed_count += 1
                errors.append("Record %s: %s" % (res_id, e))

        if errors:
            existing = self.error_log or ""
            self.error_log = existing + "\n".join(errors) + "\n"

        self.state = "processing"

    def _poll_async_jobs(self):
        """Poll TemplateTo for status of pending async jobs."""
        self.ensure_one()
        api_endpoint, api_key = self.env[
            "ir.actions.report"
        ]._templateto_api_config()
        headers = {"X-API-KEY": api_key}

        pending_lines = self.job_line_ids.filtered(
            lambda l: l.state == "submitted"
        )

        errors = []
        for line in pending_lines:
            try:
                status_resp = requests.get(
                    "%s%s" % (api_endpoint, line.status_url),
                    headers=headers,
                    timeout=15,
                )
                if status_resp.status_code != 200:
                    continue

                status_data = status_resp.json()
                job_status = status_data.get("status", "")

                if job_status == "Completed":
                    result_resp = requests.get(
                        "%s%s" % (api_endpoint, line.result_url),
                        headers=headers,
                        timeout=60,
                    )
                    if result_resp.status_code == 200 and result_resp.content:
                        record = self.env[self.model_name].browse(line.res_id)
                        record_name = (
                            record.display_name
                            if record.exists()
                            else str(line.res_id)
                        )
                        attachment = self.env["ir.attachment"].create(
                            {
                                "name": "%s.pdf" % record_name,
                                "type": "binary",
                                "datas": base64.b64encode(
                                    result_resp.content
                                ),
                                "res_model": self.model_name,
                                "res_id": line.res_id,
                                "mimetype": "application/pdf",
                            }
                        )
                        line.write(
                            {
                                "state": "done",
                                "attachment_id": attachment.id,
                            }
                        )
                        self.completed_count += 1
                    else:
                        error_msg = "Failed to fetch PDF: HTTP %d" % (
                            result_resp.status_code,
                        )
                        line.write({"state": "failed", "error": error_msg})
                        self.failed_count += 1
                        errors.append(
                            "Record %s: %s" % (line.res_id, error_msg)
                        )

                elif job_status == "Failed":
                    error_msg = status_data.get(
                        "errorMessage", "Unknown error"
                    )
                    line.write({"state": "failed", "error": error_msg})
                    self.failed_count += 1
                    errors.append(
                        "Record %s: %s" % (line.res_id, error_msg)
                    )
                # Pending/Processing: do nothing, poll again on next cron cycle

            except Exception as e:
                _logger.error(
                    "Batch poll error for job %s: %s",
                    line.templateto_job_id,
                    e,
                )

        if errors:
            existing = self.error_log or ""
            self.error_log = existing + "\n".join(errors) + "\n"

        # Check if batch is complete (no more submitted lines)
        remaining = self.job_line_ids.filtered(
            lambda l: l.state == "submitted"
        )
        if not remaining:
            self._finalize_batch()

    def _finalize_batch(self):
        """Bundle completed PDFs into ZIP and notify user via chatter."""
        self.ensure_one()
        done_lines = self.job_line_ids.filtered(lambda l: l.state == "done")

        if done_lines:
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(
                zip_buffer, "w", zipfile.ZIP_DEFLATED
            ) as zf:
                for line in done_lines:
                    if line.attachment_id and line.attachment_id.datas:
                        pdf_data = base64.b64decode(line.attachment_id.datas)
                        zf.writestr(line.attachment_id.name, pdf_data)
            zip_buffer.seek(0)

            self.zip_attachment_id = self.env["ir.attachment"].create(
                {
                    "name": "%s.zip" % self.name,
                    "type": "binary",
                    "datas": base64.b64encode(zip_buffer.getvalue()),
                    "res_model": self._name,
                    "res_id": self.id,
                    "mimetype": "application/zip",
                }
            )

        if self.failed_count and not self.completed_count:
            self.state = "failed"
        else:
            self.state = "done"

        self.message_post(
            body="Batch PDF generation complete: "
            "%d succeeded, %d failed out of %d total."
            % (self.completed_count, self.failed_count, self.total_count),
            message_type="notification",
        )


class TemplateToBatchJobLine(models.Model):
    _name = "templateto.batch.job.line"
    _description = "TemplateTo Batch Job Line"
    _order = "id"

    batch_id = fields.Many2one(
        "templateto.batch.job", string="Batch Job", ondelete="cascade"
    )
    res_id = fields.Integer(string="Record ID")
    templateto_job_id = fields.Char(string="TemplateTo Job ID")
    status_url = fields.Char(string="Status URL")
    result_url = fields.Char(string="Result URL")
    state = fields.Selection(
        [
            ("submitted", "Submitted"),
            ("done", "Done"),
            ("failed", "Failed"),
        ],
        default="submitted",
        string="Status",
    )
    attachment_id = fields.Many2one("ir.attachment", string="PDF Attachment")
    error = fields.Char(string="Error Message")
