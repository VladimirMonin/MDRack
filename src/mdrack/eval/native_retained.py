"""Offline, pinned retained-acquisition adapter. Never infers grades or reviews."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from mdrack.eval.native_corpus import Artifact, array, decode, digest, fail, integer, obj, text, validate_native_bundle


def import_retained_pack(root: Path, manifest_ref: str) -> dict[str, object]:
    with (root / "artifact-manifest.json").open("rb") as stream:
        manifest_bytes = stream.read(8 * 1024 * 1024 + 1)
    if len(manifest_bytes) > 8 * 1024 * 1024:
        fail("retained_manifest_too_large")
    if "sha256:" + hashlib.sha256(manifest_bytes).hexdigest() != digest(manifest_ref):
        fail("retained_manifest_identity")
    manifest = decode(manifest_bytes.decode("utf-8"))
    pinned: dict[str, Artifact] = {}
    for raw in array(manifest["artifacts"]):
        r = obj(raw, {"path", "bytes", "sha256"})
        size = integer(r["bytes"])
        checked = Artifact.parse({"path": r["path"], "bytes": max(1, size), "sha256": "sha256:" + text(r["sha256"])})
        # The retained manifest also pins empty Python package markers; media
        # artifacts themselves still require positive sizes in the new corpus.
        item = Artifact(checked.path, checked.sha256, size)
        if item.path in pinned:
            fail("duplicate_retained_artifact")
        item.resolve(root)
        pinned[item.path] = item

    def load(name: str) -> dict[str, object]:
        if name not in pinned:
            fail("retained_metadata_missing")
        return decode(pinned[name].resolve(root).read_text(encoding="utf-8"))

    samples = load("samples.json")
    groups = load("source-groups.json")
    recipe = load("selection-recipe.json")
    sources = load("sources.json")
    ledger = load("network-ledger.json")
    integer(ledger["downloaded_body_bytes"], 0, 32 * 1024 * 1024)
    if integer(obj(recipe["judgments"])["created_grades"]) != 0 or sources["product_corpus"] is not False:
        fail("not_acquisition_pack")
    credit = pinned["sources.json"]
    selected_esc = json.loads(pinned["esc-selected-original.json"].resolve(root).read_text(encoding="utf-8"))
    esc_rows = {text(obj(r)["filename"]): obj(r) for r in array(selected_esc)}
    resources = []
    by_id: dict[str, dict[str, object]] = {}
    for raw in array(samples["media"]):
        r = obj(raw)
        identity, source = text(r["sample_id"]), text(r["source"])
        artifact = obj(r["artifact"])
        path = text(artifact["path"])
        if (
            path not in pinned
            or pinned[path].sha256 != r["source_revision"]
            or r.get("relevance_judgments") is not None
            or r.get("temporal_judgments") is not None
        ):
            fail("retained_media_identity")
        if source == "xm3600":
            if (
                r["media_license_url"] != "https://creativecommons.org/licenses/by/2.0/"
                or r["annotation_license"] != "CC-BY-4.0"
            ):
                fail("image_license_mismatch")
            license_id, annotation = "CC-BY-2.0", "CC-BY-4.0"
        elif source == "esc10":
            original = esc_rows.get(text(r["original_id"]))
            if original is None or original["esc10"] != "True":
                fail("not_esc10")
            license_id, annotation = "CC-BY-3.0", "CC-BY-3.0"
        elif source == "blender-durian":
            license_id, annotation = "CC-BY-3.0", None
        else:
            fail("unknown_retained_media_source")
        kind = {"image": "image", "acoustic_audio": "audio", "video": "video"}.get(text(r["kind"]))
        if kind is None:
            fail("retained_media_kind")
        media = pinned[path]
        resource: dict[str, object] = {
            "id": identity,
            "kind": kind,
            "media_type": {"image": "image/jpeg", "audio": "audio/wav", "video": "video/mp4"}[kind],
            "source": source,
            "artifact": {"path": path, "sha256": media.sha256, "bytes": media.size},
            "attribution": {"path": credit.path, "sha256": credit.sha256, "bytes": credit.size},
            "group": r["group_id"],
            "split": r["split"],
            "media_license": license_id,
            "annotation_license": annotation,
            "local_reviewed": False,
            "publishable": False,
            "projection": None,
            "enrichment": None,
        }
        resources.append(resource)
        by_id[identity] = resource
    nodes = []
    for raw in array(groups["parents"]):
        p = obj(raw)
        identity = text(p["item_id"])
        media_resource = by_id.get(identity)
        nodes.append(
            {
                "id": identity,
                "group": p["group_id"],
                "split": p["split"],
                "parents": [],
                "content_ref": None if media_resource is None else obj(media_resource["artifact"])["sha256"],
                "kind": "speech_text_lead" if media_resource is None else "source",
            }
        )
    for raw in array(groups["annotations_and_derivatives"]):
        p = obj(raw)
        if p["relevance_grade"] is not None:
            fail("acquisition_grade_inferred")
        content = p.get("content_sha256")
        nodes.append(
            {
                "id": p["item_id"],
                "group": p["group_id"],
                "split": p["split"],
                "parents": [p["parent_id"]],
                "content_ref": None
                if content is None
                else digest(text(content) if text(content).startswith("sha256:") else "sha256:" + text(content)),
                "kind": p["kind"],
            }
        )
    document: dict[str, object] = {
        "contract": "mdrack.native-corpus",
        "schema_version": 2,
        "state": "draft",
        "evidence": "acquisition",
        "provenance_ref": manifest_ref,
        "selection_ref": pinned["selection-recipe.json"].sha256,
        "resources": resources,
        "groups": nodes,
        "queries": [],
    }
    validate_native_bundle(document, media_root=root)
    return document
