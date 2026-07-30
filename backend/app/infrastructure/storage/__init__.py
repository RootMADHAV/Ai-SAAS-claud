"""StoragePort adapter (MinIO).

Milestone 3: ``minio_storage.py`` (``MinioStoragePort``, backed by the
official synchronous ``minio`` SDK, wrapped in ``asyncio.to_thread`` to
honor this codebase's async contract)."""
