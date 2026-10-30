"""Check the publisher in the exact release archive before starting distribution."""

import argparse
import gzip
import hashlib
import io
import json
import re
import sys
import tarfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen


# Match the distribution service's compressed and expanded import limits.
MAX_ARCHIVE_BYTES = 60_000_000
MAX_EXPANDED_BYTES = 100_000_000


def archive_publisher(archive: bytes, sha256: str) -> str | None:
    """Read one unambiguous connector manifest without extracting or running code."""
    if hashlib.sha256(archive).hexdigest() != sha256.lower():
        raise ValueError("Archive SHA-256 mismatch")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(archive)) as compressed:
            expanded = compressed.read(MAX_EXPANDED_BYTES + 1)
        if len(expanded) > MAX_EXPANDED_BYTES:
            raise ValueError("Connector archive exceeds the 100 MB expanded import limit")
        manifests = []
        with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as bundle:
            for member in bundle:
                if not member.isfile() or not member.name.lower().endswith(".json"):
                    continue
                with bundle.extractfile(member) as source:
                    try:
                        # The importer replaces invalid UTF-8. Unrelated numeric
                        # fields must not hide a metadata candidate during parsing.
                        document = json.loads(
                            source.read().decode("utf-8", errors="replace"), parse_int=str
                        )
                    except (ValueError, UnicodeDecodeError):
                        continue
                if isinstance(document, dict) and "appid" in document and "app_version" in document:
                    manifests.append(document)
    except (OSError, EOFError, tarfile.TarError) as error:
        raise ValueError("Invalid connector gzip/tar archive") from error
    if len(manifests) != 1:
        raise ValueError("Expected exactly one connector manifest in the release archive")
    publisher = manifests[0].get("publisher")
    return publisher if isinstance(publisher, str) else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-url", required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    parsed = urlsplit(args.archive_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        parser.error("--archive-url must be HTTPS without embedded credentials")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", args.sha256):
        parser.error("--sha256 must contain 64 hexadecimal characters")
    try:
        # The release archive is public. Never send either API token on download.
        with urlopen(args.archive_url, timeout=60) as response:
            archive = response.read(MAX_ARCHIVE_BYTES + 1)
        if len(archive) > MAX_ARCHIVE_BYTES:
            raise ValueError("Connector archive exceeds the 60 MB download limit")
        eligible = archive_publisher(archive, args.sha256) == "Splunk"
    except (HTTPError, URLError, OSError):
        print("Publisher check failed: archive download error", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"Publisher check failed: {error}", file=sys.stderr)
        return 1
    print("true" if eligible else "false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
