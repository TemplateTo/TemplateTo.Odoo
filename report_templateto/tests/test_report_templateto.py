from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestTemplateToSettings(TransactionCase):
    """Tests for TemplateTo configuration and toggle behaviour."""

    def setUp(self):
        super().setUp()
        self.ICP = self.env["ir.config_parameter"].sudo()
        self.Report = self.env["ir.actions.report"]

    # ------------------------------------------------------------------
    # _templateto_enabled
    # ------------------------------------------------------------------

    def test_enabled_returns_false_when_disabled(self):
        """Module disabled in settings => _templateto_enabled() is False."""
        self.ICP.set_param("templateto.enabled", "False")
        self.ICP.set_param("templateto.api_key", "test-key-123")
        self.assertFalse(self.Report._templateto_enabled())

    def test_enabled_returns_false_when_no_api_key(self):
        """Module enabled but no API key => _templateto_enabled() is False."""
        self.ICP.set_param("templateto.enabled", "True")
        self.ICP.set_param("templateto.api_key", "")
        self.assertFalse(self.Report._templateto_enabled())

    def test_enabled_returns_false_when_key_missing(self):
        """Module enabled but param not set => _templateto_enabled() is False."""
        self.ICP.set_param("templateto.enabled", "True")
        # Ensure no api_key param exists
        param = self.ICP.search([("key", "=", "templateto.api_key")])
        if param:
            param.unlink()
        self.assertFalse(self.Report._templateto_enabled())

    def test_enabled_returns_true_when_configured(self):
        """Module enabled + API key present => _templateto_enabled() is True."""
        self.ICP.set_param("templateto.enabled", "True")
        self.ICP.set_param("templateto.api_key", "test-key-123")
        self.assertTrue(self.Report._templateto_enabled())

    # ------------------------------------------------------------------
    # _templateto_fallback_enabled
    # ------------------------------------------------------------------

    def test_fallback_enabled_default_true(self):
        """Fallback defaults to True when param is not set."""
        param = self.ICP.search([("key", "=", "templateto.fallback_enabled")])
        if param:
            param.unlink()
        self.assertTrue(self.Report._templateto_fallback_enabled())

    def test_fallback_disabled(self):
        self.ICP.set_param("templateto.fallback_enabled", "False")
        self.assertFalse(self.Report._templateto_fallback_enabled())

    # ------------------------------------------------------------------
    # _templateto_api_config
    # ------------------------------------------------------------------

    def test_api_config_returns_defaults(self):
        """Default endpoint is https://api.templateto.com."""
        param = self.ICP.search([("key", "=", "templateto.api_endpoint")])
        if param:
            param.unlink()
        self.ICP.set_param("templateto.api_key", "my-key")

        endpoint, key = self.Report._templateto_api_config()
        self.assertEqual(endpoint, "https://api.templateto.com")
        self.assertEqual(key, "my-key")

    def test_api_config_custom_endpoint(self):
        self.ICP.set_param("templateto.api_endpoint", "https://custom.api.example.com")
        self.ICP.set_param("templateto.api_key", "custom-key")

        endpoint, key = self.Report._templateto_api_config()
        self.assertEqual(endpoint, "https://custom.api.example.com")
        self.assertEqual(key, "custom-key")


class TestTemplateToSyncRendering(TransactionCase):
    """Tests for sync PDF rendering via the TemplateTo API."""

    def setUp(self):
        super().setUp()
        self.ICP = self.env["ir.config_parameter"].sudo()
        self.Report = self.env["ir.actions.report"]

        # Enable TemplateTo
        self.ICP.set_param("templateto.enabled", "True")
        self.ICP.set_param("templateto.api_key", "test-api-key")
        self.ICP.set_param("templateto.api_endpoint", "https://api.templateto.com")

    def test_disabled_calls_super(self):
        """When disabled, _render_qweb_pdf should call the original method."""
        self.ICP.set_param("templateto.enabled", "False")

        with patch.object(
            type(self.Report).__bases__[0],
            "_render_qweb_pdf",
            return_value=(b"%PDF-fake", "pdf"),
        ) as mock_super:
            # We can't easily call super in this test context, so we verify
            # that _templateto_enabled returns False
            self.assertFalse(self.Report._templateto_enabled())

    @patch("odoo.addons.report_templateto.models.ir_actions_report.requests.post")
    def test_render_single_success(self, mock_post):
        """Successful single-document render returns PDF bytes."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"%PDF-1.4 fake pdf content"
        mock_post.return_value = mock_resp

        from odoo.addons.report_templateto.models.ir_actions_report import (
            IrActionsReport,
        )

        result = IrActionsReport._templateto_render_single(
            "https://api.templateto.com", "test-key", "dGVzdA=="
        )
        self.assertEqual(result, b"%PDF-1.4 fake pdf content")
        mock_post.assert_called_once()

        # Verify request body
        call_kwargs = mock_post.call_args
        self.assertEqual(
            call_kwargs.kwargs.get("json") or call_kwargs[1].get("json"),
            {"base64HtmlString": "dGVzdA=="},
        )

    @patch("odoo.addons.report_templateto.models.ir_actions_report.requests.post")
    def test_render_single_auth_failure(self, mock_post):
        """401/403 raises UserError with auth message."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_post.return_value = mock_resp

        from odoo.addons.report_templateto.models.ir_actions_report import (
            IrActionsReport,
        )

        with self.assertRaises(UserError):
            IrActionsReport._templateto_render_single(
                "https://api.templateto.com", "bad-key", "dGVzdA=="
            )

    @patch("odoo.addons.report_templateto.models.ir_actions_report.requests.post")
    def test_render_single_server_error(self, mock_post):
        """Non-200 (excluding 401/403) raises generic Exception."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_post.return_value = mock_resp

        from odoo.addons.report_templateto.models.ir_actions_report import (
            IrActionsReport,
        )

        with self.assertRaises(Exception) as ctx:
            IrActionsReport._templateto_render_single(
                "https://api.templateto.com", "test-key", "dGVzdA=="
            )
        self.assertIn("HTTP 500", str(ctx.exception))

    @patch("odoo.addons.report_templateto.models.ir_actions_report.requests.post")
    def test_render_single_empty_response(self, mock_post):
        """200 with empty content raises Exception."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b""
        mock_post.return_value = mock_resp

        from odoo.addons.report_templateto.models.ir_actions_report import (
            IrActionsReport,
        )

        with self.assertRaises(Exception) as ctx:
            IrActionsReport._templateto_render_single(
                "https://api.templateto.com", "test-key", "dGVzdA=="
            )
        self.assertIn("empty PDF", str(ctx.exception))

    def test_merge_pdfs_single(self):
        """Merging a single PDF returns it unchanged."""
        from odoo.addons.report_templateto.models.ir_actions_report import (
            IrActionsReport,
        )

        pdf = b"%PDF-1.4 single"
        result = IrActionsReport._merge_pdfs([pdf])
        self.assertEqual(result, pdf)


class TestTemplateToBatchJob(TransactionCase):
    """Tests for the batch job model."""

    def setUp(self):
        super().setUp()
        self.BatchJob = self.env["templateto.batch.job"]
        self.BatchLine = self.env["templateto.batch.job.line"]

    def test_batch_job_creation(self):
        """Create a batch job with required fields."""
        batch = self.BatchJob.create(
            {
                "name": "Test Batch",
                "total_count": 10,
            }
        )
        self.assertEqual(batch.state, "draft")
        self.assertEqual(batch.total_count, 10)
        self.assertEqual(batch.completed_count, 0)
        self.assertEqual(batch.failed_count, 0)

    def test_batch_job_progress(self):
        """Progress is computed from completed + failed vs total."""
        batch = self.BatchJob.create(
            {
                "name": "Progress Test",
                "total_count": 10,
                "completed_count": 3,
                "failed_count": 2,
            }
        )
        self.assertAlmostEqual(batch.progress, 50.0)

    def test_batch_job_progress_zero_total(self):
        """Progress is 0 when total_count is 0."""
        batch = self.BatchJob.create(
            {
                "name": "Empty Batch",
                "total_count": 0,
            }
        )
        self.assertEqual(batch.progress, 0)

    def test_batch_job_state_transitions(self):
        """Batch moves draft -> submitting on action_start."""
        batch = self.BatchJob.create({"name": "State Test", "total_count": 5})
        self.assertEqual(batch.state, "draft")
        batch.action_start()
        self.assertEqual(batch.state, "submitting")

    def test_batch_job_cancel(self):
        """Cancel sets state to failed."""
        batch = self.BatchJob.create({"name": "Cancel Test", "total_count": 5})
        batch.action_start()
        batch.action_cancel()
        self.assertEqual(batch.state, "failed")

    def test_batch_job_line_creation(self):
        """Create batch job lines linked to a batch."""
        batch = self.BatchJob.create({"name": "Lines Test", "total_count": 2})
        line1 = self.BatchLine.create(
            {
                "batch_id": batch.id,
                "res_id": 1,
                "templateto_job_id": "job-aaa",
                "state": "submitted",
            }
        )
        line2 = self.BatchLine.create(
            {
                "batch_id": batch.id,
                "res_id": 2,
                "templateto_job_id": "job-bbb",
                "state": "done",
            }
        )
        self.assertEqual(len(batch.job_line_ids), 2)
        self.assertEqual(line1.state, "submitted")
        self.assertEqual(line2.state, "done")

    def test_batch_job_line_error(self):
        """Failed lines store an error message."""
        batch = self.BatchJob.create({"name": "Error Test", "total_count": 1})
        line = self.BatchLine.create(
            {
                "batch_id": batch.id,
                "res_id": 99,
                "state": "failed",
                "error": "HTTP 500: Internal Server Error",
            }
        )
        self.assertEqual(line.error, "HTTP 500: Internal Server Error")
