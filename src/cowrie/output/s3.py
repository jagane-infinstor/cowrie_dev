"""
Send downloaded/uplaoded files to S3 (or compatible)
"""

from __future__ import annotations

from configparser import NoOptionError

from botocore.exceptions import ClientError
from botocore.session import get_session

from twisted.internet import defer, threads
from twisted.python import log

import cowrie.core.output
from cowrie.core.config import CowrieConfig

import os
import tempfile
import hashlib
import json


class Output(cowrie.core.output.Output):
    """
    s3 output
    """

    def start(self):
        self.bucket = CowrieConfig.get("output_s3", "bucket")
        self.seen = set()
        self.session = get_session()

        try:
            if CowrieConfig.get("output_s3", "access_key_id") and CowrieConfig.get(
                "output_s3", "secret_access_key"
            ):
                self.session.set_credentials(
                    CowrieConfig.get("output_s3", "access_key_id"),
                    CowrieConfig.get("output_s3", "secret_access_key"),
                )
        except NoOptionError:
            log.msg(
                "No AWS credentials found in config - using botocore global settings."
            )

        self.client = self.session.create_client(
            "s3",
            region_name=CowrieConfig.get("output_s3", "region"),
            endpoint_url=CowrieConfig.get("output_s3", "endpoint", fallback=None),
            verify=CowrieConfig.getboolean("output_s3", "verify", fallback=True),
        )

    def stop(self):
        pass

    def write(self, entry):
        if entry["eventid"] == "cowrie.session.file_download":
            self.upload('cowrie/downloads/' + entry["shasum"], entry["outfile"])

        elif entry["eventid"] == "cowrie.session.file_upload":
            self.upload('cowrie/uploads/' + entry["shasum"], entry["outfile"])
        else:
            for i in list(entry.keys()):
                # Remove twisted 15 legacy keys
                if i.startswith("log_") or i == "time" or i == "system":
                    del entry[i]
            try:
                tfd, tfname = tempfile.mkstemp()
                with os.fdopen(tfd, 'w') as f:
                    json.dump(entry, f, separators=(",", ":"))
                    f.write("\n")
                    f.flush()
                sha1 = hashlib.sha256()
                sha1.update(json.dumps(entry).encode('utf-8'))
                if 'session' in entry and entry['session']:
                    self.upload('cowrie/events/' + entry['session'] + '-' + sha1.hexdigest(), tfname)
            except TypeError as te:
                print("s3: Can't serialize: '" + repr(entry) + "'")
                print("s3: error=" + str(te))

    @defer.inlineCallbacks
    def _object_exists_remote(self, shasum):
        try:
            yield threads.deferToThread(
                self.client.head_object,
                Bucket=self.bucket,
                Key=shasum,
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                defer.returnValue(False)
            raise

        defer.returnValue(True)

    @defer.inlineCallbacks
    def upload(self, shasum, filename):
        if shasum in self.seen:
            print(f"Already uploaded file with sha {shasum} to S3")
            return

        exists = yield self._object_exists_remote(shasum)
        if exists:
            print(f"Somebody else already uploaded file with sha {shasum} to S3")
            self.seen.add(shasum)
            return

        print(f"Uploading file with sha {shasum} ({filename}) to S3")
        with open(filename, "rb") as fp:
            yield threads.deferToThread(
                self.client.put_object,
                Bucket=self.bucket,
                Key=shasum,
                Body=fp.read(),
                ContentType="application/octet-stream",
            )
        os.unlink(filename)
        self.seen.add(shasum)
