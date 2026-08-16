# Copyright 2016-2019 Camptocamp SA
# Copyright 2021 Open Source Integrators
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)
import io
import logging
import os
import re
from datetime import datetime, timedelta

from odoo import _, api, exceptions, models
from odoo.tools import config

from odoo.addons.base_attachment_object_storage.models.ir_attachment import is_true

_logger = logging.getLogger(__name__)

try:
    from azure.core.exceptions import HttpResponseError, ResourceExistsError
    from azure.storage.blob import (
        AccountSasPermissions,
        BlobServiceClient,
        ResourceTypes,
        generate_account_sas,
    )
except ImportError:
    _logger.debug("Cannot 'import azure-storage-blob'.")

try:
    from azure.identity import DefaultAzureCredential
except ImportError:
    DefaultAzureCredential = None  # noqa
    _logger.debug("Cannot 'import azure-identity'.")


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def _get_stores(self):
        return ["azure"] + super(IrAttachment, self)._get_stores()

    @api.model
    def _get_azure_config(self):
        """Return the Azure storage configuration of the current environment

        The configuration is read from a section of the Odoo configuration
        file named after the ``ODOO_STAGE`` environment variable, so that a
        single image can be deployed on several environments (this mirrors
        what ``attachment_s3`` does)::

            [production_storage_azure]
            azure_storage_connection_string = DefaultEndpointsProtocol=https;...
            azure_storage_container = ksafi-odoo-production

        Instead of a connection string, the account can be described with
        ``azure_storage_account_name``, ``azure_storage_account_url`` and
        ``azure_storage_account_key``, or with ``azure_storage_use_aad`` and
        ``azure_storage_account_url`` when a managed identity is available.
        """
        environment = os.environ.get("ODOO_STAGE")
        return config.misc.get("%s_storage_azure" % environment, {})

    @api.model
    def _get_blob_service_client(self):
        """Connect to Azure and return the blob service client

        See ``_get_azure_config`` for the expected configuration.
        """
        azure = self._get_azure_config()
        connect_str = azure.get("azure_storage_connection_string")
        account_name = azure.get("azure_storage_account_name")
        account_url = azure.get("azure_storage_account_url")
        account_key = azure.get("azure_storage_account_key")
        # a config file holds strings, so "0" must not read as enabled
        account_use_aad = is_true(azure.get("azure_storage_use_aad"))
        if not (
            connect_str
            or (account_name and account_url and account_key)
            or (account_use_aad and account_url)
        ):
            msg = _(
                "If you want to read from the Azure container, the section "
                "[%(section)s] of the Odoo configuration file must provide "
                "the following options:\n"
                "* azure_storage_connection_string\n"
                "or\n"
                "* azure_storage_account_name\n"
                "* azure_storage_account_url\n"
                "* azure_storage_account_key\n"
                "or\n"
                "* azure_storage_use_aad\n"
                "* azure_storage_account_url\n"
            ) % {"section": "%s_storage_azure" % os.environ.get("ODOO_STAGE")}
            raise exceptions.UserError(msg)
        blob_service_client = None
        if account_use_aad:
            if DefaultAzureCredential is None:
                raise exceptions.UserError(
                    _(
                        "azure_storage_use_aad is set but the 'azure-identity' "
                        "python package is not installed."
                    )
                )
            token_credential = DefaultAzureCredential()
            blob_service_client = BlobServiceClient(
                account_url=account_url, credential=token_credential
            )
        elif connect_str:
            try:
                blob_service_client = BlobServiceClient.from_connection_string(
                    connect_str
                )
            except HttpResponseError as error:
                _logger.exception(
                    "Error during the connection to Azure container using the "
                    "connection string."
                )
                raise exceptions.UserError(str(error)) from None
        else:
            try:
                sas_token = generate_account_sas(
                    account_name=account_name,
                    account_key=account_key,
                    resource_types=ResourceTypes(container=True, object=True),
                    permission=AccountSasPermissions(
                        read=True,
                        write=True,
                        create=True,
                        delete=True,
                        list=True,
                    ),
                    expiry=datetime.utcnow() + timedelta(hours=1),
                )
                blob_service_client = BlobServiceClient(
                    account_url=account_url,
                    credential=sas_token,
                )
            except HttpResponseError as error:
                _logger.exception(
                    "Error during the connection to Azure container using the Shared "
                    "Access Signature (SAS)"
                )
                raise exceptions.UserError(str(error)) from None
        return blob_service_client

    @api.model
    def _get_container_name(self):
        # Container naming rules:
        # https://docs.microsoft.com/en-us/rest/api/storageservices/naming-and-referencing-containers--blobs--and-metadata#container-names  # noqa: B950
        storage_name = self._get_azure_config().get("azure_storage_container")
        if not storage_name:
            raise exceptions.UserError(
                _(
                    "The option 'azure_storage_container' must be set in the "
                    "section [%s] of the Odoo configuration file. It is not "
                    "guessed from the database name on purpose: an instance "
                    "restored from another environment must never write in "
                    "the container of that environment."
                )
                % ("%s_storage_azure" % os.environ.get("ODOO_STAGE"),)
            )
        running_env = os.environ.get("RUNNING_ENV", "dev")
        storage_name = storage_name.format(env=running_env, db=self.env.cr.dbname)
        # replace invalid characters by -
        storage_name = re.sub(r"[\W_]+", "-", storage_name)
        # lowercase, max 63 chars
        return str.lower(storage_name)[:63]

    @api.model
    def _get_azure_container(self, container_name=None, check_exists=False):
        """Return the client of ``container_name``, or False if unreachable

        The container is never created: it is provisioned once with the rest
        of the infrastructure. Returning False instead of raising lets the
        read path degrade gracefully when an attachment points to a container
        of another environment (e.g. a production dump restored on staging).

        ``check_exists`` costs an extra round trip, so it is only worth it on
        the write path, where a missing container must be reported clearly
        rather than as a raw Azure error. On the read and delete paths a
        missing container surfaces as a missing blob, which is already handled.
        """
        try:
            if not container_name:
                container_name = self._get_container_name()
            blob_service_client = self._get_blob_service_client()
        except exceptions.UserError:
            _logger.exception(
                "error accessing to storage '%s' please check credentials ",
                container_name,
            )
            return False
        container_client = blob_service_client.get_container_client(container_name)
        if check_exists:
            try:
                if not container_client.exists():
                    _logger.warning(
                        "The Azure container '%s' does not exist", container_name
                    )
                    return False
            except HttpResponseError:
                _logger.exception(
                    "Error while checking the Azure container '%s'", container_name
                )
                return False
        return container_client

    @api.model
    def _store_file_read(self, fname):
        if fname.startswith("azure://"):
            key = fname.replace("azure://", "", 1).lower()
            if "/" in key:
                container_name, key = key.split("/", 1)
            else:
                container_name = None
            container_client = self._get_azure_container(container_name)
            # if container cannot be retrived, abort reading from azure storage
            if not container_client:
                return ""
            try:
                blob_client = container_client.get_blob_client(key)
                read = blob_client.download_blob().readall()
            except HttpResponseError:
                read = ""
                _logger.info("Attachment '%s' missing on object storage", fname)
            return read
        else:
            return super(IrAttachment, self)._store_file_read(fname)

    @api.model
    def _store_file_write(self, key, bin_data):
        location = self.env.context.get("storage_location") or self._storage()
        if location == "azure":
            container_client = self._get_azure_container(check_exists=True)
            if not container_client:
                raise exceptions.UserError(
                    _(
                        "The file could not be stored: the configured Azure "
                        "container is not reachable, see the server logs."
                    )
                )
            key = key.lower()
            filename = "azure://%s/%s" % (container_client.container_name, key)
            with io.BytesIO() as file:
                blob_client = container_client.get_blob_client(key)
                file.write(bin_data)
                file.seek(0)
                try:
                    blob_client.upload_blob(file, blob_type="BlockBlob")
                except ResourceExistsError:
                    # the key is the checksum of the content, so an existing
                    # blob already holds exactly these bytes: nothing to do
                    _logger.debug("File %s already on the object storage", filename)
                except HttpResponseError as error:
                    # log verbose error from azure, return short message for user
                    _logger.exception(
                        "HTTP Error during storage of the file %s" % filename
                    )
                    raise exceptions.UserError(
                        _("The file could not be stored: %s") % str(error)
                    ) from None
        else:
            _super = super(IrAttachment, self)
            filename = _super._store_file_write(key, bin_data)
        return filename

    @api.model
    def _store_file_delete(self, fname):
        if fname.startswith("azure://"):
            key = fname.replace("azure://", "", 1).lower()
            if "/" in key:
                container_name, key = key.split("/", 1)
            else:
                container_name = None
            # delete the file only if it is on the current configured container
            # otherwise, we might delete files used on a different environment
            try:
                configured_container = self._get_container_name()
            except exceptions.UserError:
                _logger.warning(
                    "Azure storage is not configured, file %s not deleted", fname
                )
                return
            if container_name != configured_container:
                _logger.info(
                    "File %s is not on the configured container, not deleted", fname
                )
                return
            container_client = self._get_azure_container(container_name)
            if not container_client:
                return
            try:
                blob_client = container_client.get_blob_client(key)
                blob_client.delete_blob()
                _logger.info("File %s deleted on the object storage" % (fname))
            except HttpResponseError:
                # log verbose error from azure, return short message for
                # user
                _logger.exception("Error during deletion of the file %s" % fname)
        else:
            super(IrAttachment, self)._store_file_delete(fname)
