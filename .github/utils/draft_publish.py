"""Resume draft publication from a durable queue checkpoint."""

from pathlib import PurePosixPath
import re
import tarfile

from .api.splunkbase import SplunkbaseResponseError, SplunkbaseValidationFailed


FLOW = "draft_v2"
TAB_FIELDS = {
    "Details": "documentation_source",
    "Installation": "installation_source",
    "Troubleshooting": "troubleshooting_source",
    "Contact": "contact_notes_source",
}
MAX_MARKDOWN_BYTES = 80_000


def package_metadata(artifact, manifest, repository, license_name, license_url):
    """Read Markdown without extracting or following links in the package.

    Dedicated tab files take precedence over matching README sections. Details
    defaults to manual_readme_content.md (legacy) or README.md (SDK).
    """
    markdown = {}
    with tarfile.open(artifact, "r:*") as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if not member.isfile() or len(path.parts) != 2 or ".." in path.parts:
                continue
            name = path.name.lower()
            if name not in {
                "readme.md",
                "manual_readme_content.md",
                "details.md",
                "installation.md",
                "troubleshooting.md",
                "contact.md",
            }:
                continue
            if member.size > MAX_MARKDOWN_BYTES or name in markdown:
                raise SplunkbaseValidationFailed(
                    f"Package documentation is oversized or duplicated: {name}"
                )
            try:
                markdown[name] = archive.extractfile(member).read().decode("utf-8").strip()
            except UnicodeError:
                raise SplunkbaseValidationFailed(
                    f"Package documentation is not UTF-8: {name}"
                ) from None

    readme = markdown.get("manual_readme_content.md") or markdown.get("readme.md", "")
    sections = {}
    headings = list(re.finditer(r"^## ([^\n]+)\s*$", readme, re.MULTILINE))
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(readme)
        sections[heading.group(1).strip().lower()] = readme[heading.end() : end].strip()
    metadata = {
        "description": manifest.get("description"),
        "repo_name": repository.split("/")[-1],
        "repo_url": f"https://github.com/{repository}",
        "license_name": license_name,
        "license_url": license_url,
    }
    for tab, field in TAB_FIELDS.items():
        value = markdown.get(f"{tab.lower()}.md") or sections.get(tab.lower())
        if tab == "Details" and not value:
            value = readme
        if value:
            if len(value) > 20_000:
                raise SplunkbaseValidationFailed(
                    f"{field} exceeds Splunkbase's 20000-character limit; provide {tab.lower()}.md"
                )
            metadata[field] = value
    # Use explicit listing metadata when supplied, and omit unavailable values.
    # Connector categories are vendor strings, not Splunkbase numeric category PKs.
    contact = manifest.get("contact_email")
    if contact:
        metadata["contact_email"] = contact
    support = manifest.get("support")
    if support in ("premium", "splunk", "cisco", "developer", "not_supported"):
        metadata["support"] = support
    elif manifest.get("publisher") == "Splunk":
        metadata["support"] = "splunk"
    return {key: value for key, value in metadata.items() if value not in (None, "")}


def release_for_version(app, version):
    releases = app.get("releases")
    if not isinstance(releases, list):
        raise SplunkbaseResponseError("Splunkbase app detail is missing releases")
    if any(not isinstance(release, dict) for release in releases):
        raise SplunkbaseResponseError("Splunkbase app detail contains invalid releases")
    matches = [
        release
        for release in releases
        if str(release.get("name", release.get("release_name", ""))) == version
    ]
    if len(matches) > 1:
        raise SplunkbaseResponseError("Splunkbase returned duplicate candidate releases")
    return matches[0] if matches else None


def publicly_available(client, app_id, version, app_guid):
    app = client.public_app(app_id)
    if app is None:
        return False
    if app.get("app_id") != app_guid:
        raise SplunkbaseValidationFailed("Public listing does not match the queued connector appid")
    return release_for_version(app, version) is not None


def advance_publication(client, artifact, manifest, metadata, release_notes, state, checkpoint):
    """Advance one publication; checkpoint every externally visible mutation.

    Unknown non-idempotent outcomes require operator reconciliation. Safe PATCH
    operations can be repeated from immutable package metadata.
    """
    state.update({"publication_flow": FLOW, "status": "verifying"})
    version = manifest["app_version"]

    def save(**updates):
        state.update(updates)
        checkpoint(dict(state))

    def mutation(operation, call, **updates):
        save(pending_operation=operation)
        result = call()
        save(pending_operation="", **updates)
        return result

    pending = state.get("pending_operation")
    if pending in {"create_draft", "register_package"}:
        # These APIs do not document idempotency keys or a lookup by source run.
        # Holding is safer than inventing a retry that could create a duplicate.
        raise SplunkbaseValidationFailed(
            f"Unknown {pending} outcome; reconcile the app/package ID with App Hosting before resuming"
        )
    app_id = state.get("splunkbase_app_id")
    if not app_id:
        save(pending_operation="create_draft")
        app_id = client.create_draft(
            {"name": manifest["name"], "product": "soar", "app_type": "connector", **metadata}
        )
        save(splunkbase_app_id=app_id, pending_operation="")

    if not state.get("package_id") and not state.get("release_id"):
        if pending == "upload_s3":
            # No registration was started, so the unregistered object cannot
            # create an app/release. A fresh presigned grant is safe to request.
            save(pending_operation="")

        def uploaded(s3_key):
            save(s3_key=s3_key, pending_operation="register_package")

        if state.get("s3_key"):
            save(pending_operation="register_package")
            package_id = client.register_package(
                app_id, PurePosixPath(artifact).name, state["s3_key"]
            )
        else:
            save(pending_operation="upload_s3")
            package_id = client.upload_package(app_id, artifact, uploaded)
        save(
            package_id=package_id,
            pending_operation="",
            request_id=getattr(client, "last_upload_request_id", None),
        )

    if not state.get("release_id"):
        response = client.get_upload_status(state["package_id"])
        if response.get("result") == "Fail":
            raise SplunkbaseValidationFailed(
                "Splunkbase package validation failed; inspect the validation report"
            )
        if response.get("result") != "Pass":
            return False
        details = response.get("details", {})
        if not isinstance(details, dict):
            raise SplunkbaseResponseError("Splunkbase returned malformed validation details")
        release = details.get("release", {})
        if not isinstance(release, dict):
            raise SplunkbaseResponseError(
                "Splunkbase returned malformed validation release metadata"
            )
        if (
            str(details.get("id")) != str(app_id)
            or details.get("app_id") != manifest["appid"]
            or release.get("name") != version
            or not release.get("id")
        ):
            raise SplunkbaseValidationFailed(
                "Validated package does not match the queued app, version, and release ID"
            )
        save(release_id=release["id"])

    if state.get("publication_requested"):
        return publicly_available(client, app_id, version, manifest["appid"])
    app = client.get_app(app_id)
    if app.get("app_id") != manifest["appid"]:
        raise SplunkbaseValidationFailed("Validated app identity differs from the queued connector")
    release = release_for_version(app, version)
    if not release or (release.get("id") and str(release["id"]) != str(state["release_id"])):
        raise SplunkbaseValidationFailed("Validated release differs from the persisted release ID")
    if state.get("pending_operation") == "submit_draft" and app.get("draft") is not False:
        raise SplunkbaseValidationFailed(
            "Unknown draft submission outcome; reconcile with App Hosting before resuming"
        )
    listing_metadata = dict(metadata)
    # MP-510 asks to fill contact/support only when the listing lacks them.
    for field in ("contact_email", "support"):
        if app.get(field):
            listing_metadata.pop(field, None)
    mutation("metadata", lambda: client.patch_metadata(app_id, listing_metadata))
    mutation(
        "release_metadata",
        lambda: client.publish_release(
            app_id, state["release_id"], release_notes, publish=app.get("draft") is False
        ),
    )
    if app.get("draft") is True:
        missing = client.required_fields(app_id)
        if missing:
            raise SplunkbaseValidationFailed(
                "Draft is missing required metadata: " + ", ".join(missing)
            )
        mutation("submit_draft", lambda: client.submit_draft(app_id))
    elif app.get("draft") is not False:
        raise SplunkbaseResponseError("App detail did not report its draft state")
    save(publication_requested=True)
    return publicly_available(client, app_id, version, manifest["appid"])
