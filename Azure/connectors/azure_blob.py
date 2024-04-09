"""Connector to pull data from Azure Blob Storage."""

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from gzip import decompress
from typing import Any, Optional
from collections.abc import AsyncGenerator

import aiofiles
from azure.storage.blob import BlobProperties
from dateutil.parser import isoparse
from loguru import logger
from pydantic import Field
from sekoia_automation.aio.connector import AsyncConnector
from sekoia_automation.aio.helpers.files.utils import delete_file
from sekoia_automation.connector import DefaultConnectorConfiguration
from sekoia_automation.module import Module
from sekoia_automation.storage import PersistentJSON

from azure_helpers.io import is_gzip_compressed
from azure_helpers.storage import AzureBlobStorageConfig, AzureBlobStorageWrapper

from .metrics import EVENTS_LAG, FORWARD_EVENTS_DURATION, OUTCOMING_EVENTS


class AzureBlobConnectorConfig(DefaultConnectorConfiguration):
    """Connector configuration."""

    container_name: str
    account_name: str
    account_key: str = Field(secret=True)


class AzureBlobConnector(AsyncConnector):
    """AzureBlobConnector."""

    name = "AzureBlobConnector"
    module: Module
    configuration: AzureBlobConnectorConfig

    _azure_blob_storage_wrapper: AzureBlobStorageWrapper | None = None

    def __init__(self, *args: Any, **kwargs: Optional[Any]) -> None:
        """Init AzureBlobConnector."""

        super().__init__(*args, **kwargs)
        self.context = PersistentJSON("context.json", self._data_path)
        self.limit_of_events_to_push = int(os.getenv("AZURE_BATCH_SIZE", 10000))

    @property
    def last_event_date(self) -> datetime:
        """
        Get last event date.

        Returns:
            datetime:
        """
        now = datetime.now(timezone.utc)
        one_hour_ago = (now - timedelta(hours=1)).replace(microsecond=0)

        with self.context as cache:
            last_event_date_str = cache.get("last_event_date")

            # If undefined, retrieve events from the last 1 hour
            if last_event_date_str is None:
                return one_hour_ago

            # Parse the most recent date seen
            last_event_date = isoparse(last_event_date_str).replace(microsecond=0)

            # We don't retrieve messages older than 1 hour
            if last_event_date < one_hour_ago:
                return one_hour_ago

            return last_event_date

    def azure_blob_wrapper(self) -> AzureBlobStorageWrapper:
        """
        Get Azure blob wrapper.

        Returns:
            AzureBlobStorageWrapper:
        """
        if not self._azure_blob_storage_wrapper:
            config = AzureBlobStorageConfig(**self.configuration.dict(exclude_unset=True, exclude_none=True))
            self._azure_blob_storage_wrapper = AzureBlobStorageWrapper(config)

        return self._azure_blob_storage_wrapper

    async def get_most_recent_blobs(self, lower_bound: datetime) -> AsyncGenerator[BlobProperties, None]:
        """
        Return the list of blobs, more recent than lower_bound

        Args:
            lower_bound: datetime

        Returns:
            AsyncGenerator[BlobProperties, None]
        """

        blob_list = self.azure_blob_wrapper().list_blobs()
        return (blob async for blob in blob_list if blob.last_modified > lower_bound)

    async def get_azure_blob_data(self) -> list[str]:
        """
        Get Azure Blob Storage data.

        Returns:
            list[str]:
        """
        _last_modified_date = self.last_event_date

        # Get the blobs more recent than _last_modified_date
        logger.info(
            "From blobs from {lower_bound}",
            lower_bound=_last_modified_date.isoformat(),
        )
        most_recent_blobs = await self.get_most_recent_blobs(_last_modified_date)

        records: list[str] = []
        result: list[str] = []

        # For each blob
        async for blob in most_recent_blobs:
            logger.info(
                "Process blob {name} modified at {modified_at}",
                name=blob.name,
                modified_at=blob.last_modified.isoformat(),
            )
            # Save the most recent date seen
            if _last_modified_date is None or blob.last_modified > _last_modified_date:
                _last_modified_date = blob.last_modified

            # Get the content of the current blob
            file, content = await self.azure_blob_wrapper().download_blob(blob.name, download=True)

            # process the downloaded blob
            if file:
                async with aiofiles.open(file, "rb") as file_data:
                    file_content = await file_data.read()

                    if is_gzip_compressed(file_content):
                        file_content = decompress(file_content)

                    records.extend([line for line in file_content.decode("utf-8").split("\n") if line != ""])

                await delete_file(file)

            # process the content of the blob
            if content:
                records.extend([line for line in content.decode("utf-8").split("\n") if line != ""])

            # Push the events if exceed the defined threshold
            if len(records) >= self.limit_of_events_to_push:
                result.extend(await self.push_data_to_intakes(events=records))
                records = []

        result: list[str] = await self.push_data_to_intakes(events=records)

        with self.context as cache:
            logger.info(
                "New last event date now is {last_event_date}",
                last_event_date=_last_modified_date.isoformat(),
            )

            cache["last_event_date"] = _last_modified_date.isoformat()

        return result

    def run(self) -> None:  # pragma: no cover
        """Runs Azure Blob Storage."""
        previous_processing_end = None

        while self.running:
            try:
                loop = asyncio.get_event_loop()

                while self.running:
                    processing_start = time.time()
                    if previous_processing_end is not None:
                        EVENTS_LAG.labels(intake_key=self.configuration.intake_key).set(
                            processing_start - self.last_event_date.timestamp()
                        )

                    message_ids: list[str] = loop.run_until_complete(self.get_azure_blob_data())
                    processing_end = time.time()
                    OUTCOMING_EVENTS.labels(intake_key=self.configuration.intake_key).inc(len(message_ids))

                    log_message = "No records to forward"
                    if len(message_ids) > 0:
                        log_message = "Pushed {0} records".format(len(message_ids))

                    self.log(message=log_message, level="info")

                    logger.info(
                        "Processing took {processing_time} seconds",
                        processing_time=(processing_end - processing_start),
                    )

                    FORWARD_EVENTS_DURATION.labels(intake_key=self.configuration.intake_key).observe(
                        processing_end - processing_start
                    )

                    previous_processing_end = processing_end

            except Exception as e:
                logger.error("Error while running Azure Blob Storage: {error}", error=e)
