import os
from unittest.mock import patch

from odoo.addons.attachment_azure.models.ir_attachment import IrAttachment
from odoo.tests.common import TransactionCase
from odoo.tools import config


class TestAzureConfig(TransactionCase):
    def _config(self, sections, environment="staging", running_env=None):
        environment_vars = {"ODOO_STAGE": environment}
        if running_env is not None:
            environment_vars["RUNNING_ENV"] = running_env
        return (
            patch.dict(config.misc, sections, clear=True),
            patch.dict(os.environ, environment_vars, clear=True),
        )

    def test_explicit_staging_config_wins(self):
        sections = {
            "production_storage_azure": {
                "azure_storage_connection_string": "production-credentials",
                "azure_storage_container": "odoo-production",
            },
            "staging_storage_azure": {
                "azure_storage_connection_string": "staging-credentials",
                "azure_storage_container": "odoo-staging",
            },
        }
        config_patch, env_patch = self._config(sections)
        with config_patch, env_patch:
            azure = self.env["ir.attachment"]._get_azure_config()

        self.assertEqual(azure, sections["staging_storage_azure"])

    def test_staging_uses_shared_credentials_and_legacy_container(self):
        sections = {
            "production_storage_azure": {
                "azure_storage_connection_string": "shared-credentials",
                "azure_storage_container": "odoo-production",
            },
            "staging_storage_s3": {"aws_bucketname": "odoo-staging"},
        }
        config_patch, env_patch = self._config(sections)
        with config_patch, env_patch:
            azure = self.env["ir.attachment"]._get_azure_config()

        self.assertEqual(
            azure,
            {
                "azure_storage_connection_string": "shared-credentials",
                "azure_storage_container": "odoo-staging",
            },
        )

    def test_staging_reads_restored_production_container(self):
        sections = {
            "production_storage_azure": {
                "azure_storage_connection_string": "production-credentials",
                "azure_storage_container": "odoo-production",
            },
            "staging_storage_azure": {
                "azure_storage_connection_string": "staging-credentials",
                "azure_storage_container": "odoo-staging",
            },
        }
        config_patch, env_patch = self._config(sections)
        with config_patch, env_patch:
            azure = self.env["ir.attachment"]._get_azure_config(
                container_name="odoo-production"
            )

        self.assertEqual(azure, sections["production_storage_azure"])

    def test_missing_staging_config_does_not_enable_production_writes(self):
        sections = {
            "production_storage_azure": {
                "azure_storage_connection_string": "production-credentials",
                "azure_storage_container": "odoo-production",
            },
        }
        config_patch, env_patch = self._config(sections)
        with config_patch, env_patch:
            azure = self.env["ir.attachment"]._get_azure_config()

        self.assertEqual(azure, {})

    def test_staging_does_not_delete_restored_production_blob(self):
        sections = {
            "production_storage_azure": {
                "azure_storage_connection_string": "shared-credentials",
                "azure_storage_container": "odoo-production",
            },
            "staging_storage_s3": {"aws_bucketname": "odoo-staging"},
        }
        config_patch, env_patch = self._config(sections)
        with (
            config_patch,
            env_patch,
            patch.object(IrAttachment, "_get_azure_container") as get_container,
        ):
            self.env["ir.attachment"]._store_file_delete(
                "azure://odoo-production/checksum"
            )

        get_container.assert_not_called()

    def test_container_template_falls_back_to_odoo_stage(self):
        sections = {
            "staging_storage_azure": {
                "azure_storage_connection_string": "staging-credentials",
                "azure_storage_container": "odoo-{env}",
            },
        }
        config_patch, env_patch = self._config(sections)
        with config_patch, env_patch:
            container = self.env["ir.attachment"]._get_container_name()

        self.assertEqual(container, "odoo-staging")
