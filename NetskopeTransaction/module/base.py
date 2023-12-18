import json
import os
from abc import ABC
from pathlib import Path

from sekoia_automation.aio.connector import AsyncConnector
from sekoia_automation.module import ModuleItem


class GoogleBase(ModuleItem):
    CREDENTIALS_PATH = Path("/tmp/credentials.json")

    def execute(self) -> None:
        self.set_credentials()
        super().execute()

    def set_credentials(self) -> None:
        """
        Save the credentials in a file so they can be used by the Google client
        """
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(self.CREDENTIALS_PATH)
        with self.CREDENTIALS_PATH.open("w") as fp:
            json.dump(self.module.configuration["credentials"], fp)


class AsyncGoogleTrigger(GoogleBase, AsyncConnector, ABC):
    pass
