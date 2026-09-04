===========================================
Attachments on Microsoft Azure Blob Storage
===========================================

This addon allows to store the attachments (documents and assets) on `Microsoft Azure
Blob Storage <https://docs.microsoft.com/azure/storage/blobs/>`_.

Configuration
-------------

Activate Azure Blob storage:

* Create or set the system parameter with the key ``ir_attachment.location``
  and the value in the form ``azure``.

Configure accesses in the Odoo configuration file, in a section named after the
``ODOO_STAGE`` environment variable (this fork reads the configuration file
instead of environment variables, like ``attachment_s3`` does, because the
hosting platform does not allow setting arbitrary environment variables)::

    [production_storage_azure]
    azure_storage_connection_string = DefaultEndpointsProtocol=https;AccountName=...
    azure_storage_container = ksafi-odoo-production

Instead of a connection string, the account can be described with:

* ``azure_storage_account_name``
* ``azure_storage_account_url``
* ``azure_storage_account_key``

or, when a managed identity is available (not the case on Odoo.sh):

* ``azure_storage_use_aad``
* ``azure_storage_account_url``

``azure_storage_container`` is required and has no default. The strings ``{db}``
and ``{env}`` can be used inside it and are replaced respectively by the database
name and the ``RUNNING_ENV`` environment variable (or ``ODOO_STAGE`` when
``RUNNING_ENV`` is absent). The container is **not** created automatically:
provision it with the rest of the infrastructure.

During a migration from ``attachment_s3``, a non-production environment may
reuse the account-level credentials from ``[production_storage_azure]`` when it
does not yet have an Azure section. Its Azure container is then taken from the
``aws_bucketname`` in its existing ``[<ODOO_STAGE>_storage_s3]`` section. This
keeps writes in the environment-specific container while allowing a restored
database to read its existing production Azure attachments.

The container name is stored in the database for each attachment, and is used to
access the right container in the storage.

Read-only mode:

The container and the file key are stored in the attachment. So if you change
``azure_storage_container`` or ``ir_attachment.location``, the existing
attachments will still be read from their former container. Files are deleted
only when they live in the container configured for the current environment, so
an instance restored from a production dump can read the production attachments
without any risk of altering the production data.

This addon must be added in the server wide addons with (``--load`` option):

``--load=web,attachment_azure``

When migrating from another object storage, keep the addon of the former storage
installed and server-wide loaded as well, so that attachments that still point to
it remain readable.

The System Parameter ``ir_attachment.storage.force.database`` can be customized to
force storage of files in the database. See the documentation of the module
``base_attachment_object_storage``.

Limitations
-----------

* You need to call ``env['ir.attachment'].force_storage()`` after
  having changed the ``ir_attachment.location`` configuration in order to
  migrate the existing attachments to Azure Blob Storage.
