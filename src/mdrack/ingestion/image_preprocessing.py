"""Bounded ordinary-image preparation for the selected native image recipe.

This is an application/media boundary helper, not a generic-core image decoder.
It turns ordinary JPEG/PNG/WEBP bytes into the exact prepared RGB bitmap accepted
by the pinned caller-owned native encoder. Source bytes, decoded raster work and
model input bytes have independent limits. The original source locator/revision is
never replaced by a prepared file.

The selected native projector metadata (read from the pinned mmproj GGUF) declares
``gemma4v``, a 224x224 image, 16x16 patches, mean 0 and std 1. The pinned mtmd
runtime selects its ``mtmd_image_preprocessor_fixed_size`` for projector enum 13
(gemma4v); that class targets ``hparams.image_size``. The selected load_hparams
branch writes resize algorithm 1 (bicubic), while the hparams constructor sets
pad slot 1 (center) and zero pad color. The fixed-size processor therefore
resizes the full source with the selected bicubic mode to fit the declared square
and center-pads the remaining area with black. Python supplies only that RGB
bitmap; native mtmd owns uint8-to-float and per-channel mean/std tensor work.
Alpha background is not declared by the selected model/processor and remains an
explicit caller policy: opaque-white compositing. This module uses the same
Windows WIC decoder family as the native host and the host's exact PNG224/RGB
admission check. The bicubic kernel implementation is explicitly identified as
a caller implementation of the selected bicubic mode, not a claimed native
pixel-identical kernel.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import struct
import sys
import tempfile
import threading
import zlib
from array import array
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from mdrack_core import JSONValue

_SOURCE_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
_MAX_CACHE_BYTES = 8 * 1024 * 1024
_MAX_CACHE_ENTRIES = 8


class ImagePreparationError(ValueError):
    """Fixed, payload-free refusal/error for the image preparation boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ImageProcessPolicy:
    """Independent source, decode and model-input budgets."""

    max_source_bytes: int = 33_554_432
    max_pixels: int = 16_777_216
    max_dimension: int = 8192
    max_decoded_bytes: int = 67_108_864
    max_prepared_bytes: int = 1_048_576
    max_prepared_width: int = 224
    max_prepared_height: int = 224
    max_prepared_rgb_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        for value in (
            self.max_source_bytes,
            self.max_pixels,
            self.max_dimension,
            self.max_decoded_bytes,
            self.max_prepared_bytes,
            self.max_prepared_width,
            self.max_prepared_height,
            self.max_prepared_rgb_bytes,
        ):
            if type(value) is not int or value < 1:
                raise ValueError("image_policy_invalid")

    @classmethod
    def selected_native_geometry(cls) -> ImageProcessPolicy:
        """Variable prepared RGB transport; original source budgets stay unchanged."""
        return cls(
            max_source_bytes=33_554_432,
            max_pixels=16_777_216,
            max_dimension=8192,
            max_decoded_bytes=67_108_864,
            max_prepared_bytes=67_108_864,
            max_prepared_width=8192,
            max_prepared_height=8192,
            max_prepared_rgb_bytes=50_331_648,
        )

    @property
    def fingerprint(self) -> str:
        return _sha256_text(_canonical_json(asdict(self)))


@dataclass(frozen=True)
class ImageProcessRecipe:
    """Versioned processor identity, including the actual pinned projector bytes."""

    version: str = "mdrack-ordinary-image-v1"
    width: int = 224
    height: int = 224
    color_model: str = "rgb"
    geometry: str = "full-frame-bilinear"
    alpha_policy: str = "composite-white"
    orientation_policy: str = "exif-normalize"
    decoder: str = "windows-wic-v1"
    model_projector: str = "gemma4v"
    model_image_size: int = 224
    model_patch_size: int = 16
    model_image_mean: tuple[float, float, float] = (0.0, 0.0, 0.0)
    model_image_std: tuple[float, float, float] = (1.0, 1.0, 1.0)
    processor_artifact_sha256: str = ""
    native_runtime_sha256: str = ""
    model_metadata_sha256: str = "1a1a3195cd31fb9df57ed387001bad4e22f97e098551149f4cf5448121fd4b68"
    pad_style: str = "none"
    pad_color: str = "black"
    bicubic_kernel: str = "not-used"
    geometry_authority: str = "caller-policy-legacy-full-frame"
    interpolation_authority: str = "caller-policy-legacy-bilinear"
    pad_authority: str = "caller-policy-legacy-no-pad"
    pad_color_authority: str = "caller-policy-legacy"
    bicubic_kernel_authority: str = "caller-policy-legacy"
    alpha_authority: str = "caller-policy-no-selected-background-key"
    tensor_owner: str = "pinned-mtmd-fixed-size"
    native_target: str = "none"
    projector_patch_size: int = 16
    projector_scale_factor: int = 3
    native_target_factor: int = 48
    image_min_tokens: int = 70
    image_max_tokens: int = 1120

    @classmethod
    def selected_gemma4v(
        cls, *, processor_artifact_sha256: str = "", native_runtime_sha256: str = "",
        projector_scale_factor: int = 3,
    ) -> ImageProcessRecipe:
        """Selected gemma4v enum13 dyn_size recipe: preserve oriented source RGB."""
        factor = 16 * projector_scale_factor
        return cls(
            version="mdrack-ordinary-image-gemma4v-native-geometry-v3",
            width=224,
            height=224,
            geometry="native-dyn-size-source-rgb",
            pad_style="native",
            pad_color="native",
            bicubic_kernel="native-owned",
            geometry_authority="selected-gemma4v-dyn-size-enum13",
            interpolation_authority="native-mtmd-dyn-size-bicubic",
            pad_authority="native-mtmd-dyn-size-center-black",
            pad_color_authority="native-mtmd-dyn-size",
            bicubic_kernel_authority="native-mtmd-dyn-size",
            tensor_owner="pinned-mtmd-dyn-size",
            native_target="dyn_size",
            projector_patch_size=16,
            projector_scale_factor=projector_scale_factor,
            native_target_factor=factor,
            image_min_tokens=70,
            image_max_tokens=1120,
            processor_artifact_sha256=processor_artifact_sha256,
            native_runtime_sha256=native_runtime_sha256,
        )

    def __post_init__(self) -> None:
        if not self.version or not self.model_projector or not self.decoder:
            raise ValueError("image_recipe_invalid")
        if type(self.width) is not int or self.width < 1 or type(self.height) is not int or self.height < 1:
            raise ValueError("image_recipe_invalid")
        if self.color_model != "rgb":
            raise ValueError("image_recipe_invalid")
        if self.alpha_policy not in {"composite-white"} or self.orientation_policy not in {"exif-normalize"}:
            raise ValueError("image_recipe_invalid")
        legacy = (
            self.geometry == "full-frame-bilinear"
            and self.pad_style == "none"
            and self.pad_color == "black"
            and self.bicubic_kernel == "not-used"
            and self.geometry_authority == "caller-policy-legacy-full-frame"
            and self.interpolation_authority == "caller-policy-legacy-bilinear"
            and self.pad_authority == "caller-policy-legacy-no-pad"
            and self.pad_color_authority == "caller-policy-legacy"
            and self.bicubic_kernel_authority == "caller-policy-legacy"
        )
        selected = (
            self.geometry == "fit-center-pad-bicubic"
            and self.pad_style == "center"
            and self.pad_color == "black"
            and self.bicubic_kernel == "keys-a-minus-0.5-antialiased-v1"
            and self.geometry_authority == "selected-gemma4v-fixed-size-enum13"
            and self.interpolation_authority == "selected-load-hparams-bicubic-enum1"
            and self.pad_authority == "selected-hparams-constructor-pad-slot1-center"
            and self.pad_color_authority == "selected-hparams-constructor-zero-black"
            and self.bicubic_kernel_authority == "caller-implementation-of-selected-bicubic-mode"
        )
        native_geometry = (
            self.geometry == "native-dyn-size-source-rgb"
            and self.pad_style == "native"
            and self.pad_color == "native"
            and self.bicubic_kernel == "native-owned"
            and self.geometry_authority == "selected-gemma4v-dyn-size-enum13"
            and self.interpolation_authority == "native-mtmd-dyn-size-bicubic"
            and self.pad_authority == "native-mtmd-dyn-size-center-black"
            and self.pad_color_authority == "native-mtmd-dyn-size"
            and self.bicubic_kernel_authority == "native-mtmd-dyn-size"
            and self.tensor_owner == "pinned-mtmd-dyn-size"
            and self.native_target == "dyn_size"
            and self.projector_patch_size == 16
            and self.projector_scale_factor == 3
            and self.native_target_factor == 48
            and self.image_min_tokens == 70
            and self.image_max_tokens == 1120
        )
        if not (legacy or selected or native_geometry):
            raise ValueError("image_recipe_invalid")
        if self.alpha_authority not in {"caller-policy-no-selected-background-key"}:
            raise ValueError("image_recipe_invalid")
        if self.tensor_owner not in {"pinned-mtmd-fixed-size", "pinned-mtmd-dyn-size"}:
            raise ValueError("image_recipe_invalid")
        for digest in (self.processor_artifact_sha256, self.native_runtime_sha256, self.model_metadata_sha256):
            if digest and not _is_sha256_hex(digest):
                raise ValueError("image_recipe_invalid")

    @property
    def fingerprint(self) -> str:
        value = asdict(self)
        value["model_image_mean"] = list(self.model_image_mean)
        value["model_image_std"] = list(self.model_image_std)
        return _sha256_text(_canonical_json(value))

    @property
    def model_processor_fingerprint(self) -> str:
        return _sha256_text(_canonical_json({
            "projector": self.model_projector,
            "image_size": self.model_image_size,
            "patch_size": self.model_patch_size,
            "image_mean": list(self.model_image_mean),
            "image_std": list(self.model_image_std),
            "model_metadata_sha256": self.model_metadata_sha256,
            "native_runtime_sha256": self.native_runtime_sha256,
            "tensor_owner": self.tensor_owner,
            "geometry": self.geometry,
            "pad_style": self.pad_style,
            "pad_color": self.pad_color,
            "interpolation": "bicubic",
            "native_target": self.native_target,
            "projector_patch_size": self.projector_patch_size,
            "projector_scale_factor": self.projector_scale_factor,
            "native_target_factor": self.native_target_factor,
            "image_min_tokens": self.image_min_tokens,
            "image_max_tokens": self.image_max_tokens,
        }))


@dataclass(frozen=True)
class DecodedImage:
    width: int
    height: int
    pixels: array[int]  # RGBA packed as native uint32: R | G<<8 | B<<16 | A<<24
    orientation: int
    source_media_type: str


@dataclass(frozen=True)
class PreparedImage:
    content: bytes = field(repr=False)
    identity: str
    source_revision: str
    recipe_fingerprint: str
    cache_key: str
    source_media_type: str
    source_width: int
    source_height: int
    orientation: int
    alpha_composited: bool
    content_identity: str = ""

    def __post_init__(self) -> None:
        if type(self.content) is not bytes or not self.content:
            raise ValueError("prepared_image_invalid")
        if not self.identity.startswith("sha256:") or not self.cache_key:
            raise ValueError("prepared_image_invalid")

    @property
    def media_type(self) -> str:
        return "image/png"

    def as_input(self) -> object:
        from mdrack.domain.embedding_inputs import ImageInput

        return ImageInput(self.content, self.media_type, self.identity)


class ImageDecoder(Protocol):
    def decode(self, content: bytes, media_type: str, policy: ImageProcessPolicy) -> DecodedImage: ...


class ImagePreparationCache:
    """Small bounded process cache; keys are source revision + processor recipe."""

    def __init__(self, *, max_entries: int = _MAX_CACHE_ENTRIES, max_bytes: int = _MAX_CACHE_BYTES) -> None:
        if type(max_entries) is not int or max_entries < 1 or type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("image_cache_invalid")
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.hits = 0
        self.misses = 0
        self._entries: OrderedDict[str, PreparedImage] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key: str) -> PreparedImage | None:
        with self._lock:
            value = self._entries.get(key)
            if value is None:
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return value

    def put(self, key: str, value: PreparedImage) -> None:
        with self._lock:
            old = self._entries.pop(key, None)
            if old is not None:
                self._bytes -= len(old.content)
            self._entries[key] = value
            self._bytes += len(value.content)
            while len(self._entries) > self.max_entries or self._bytes > self.max_bytes:
                _, removed = self._entries.popitem(last=False)
                self._bytes -= len(removed.content)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0
            self.hits = 0
            self.misses = 0


class ImagePreprocessor:
    """Decode, orient, alpha-composite, resize and encode one bounded ordinary image."""

    def __init__(
        self,
        recipe: ImageProcessRecipe | None = None,
        policy: ImageProcessPolicy | None = None,
        cache: ImagePreparationCache | None = None,
        decoder: ImageDecoder | None = None,
    ) -> None:
        self.recipe = recipe or ImageProcessRecipe()
        self.policy = policy or ImageProcessPolicy()
        self.cache = cache or ImagePreparationCache()
        self.decoder = decoder or WindowsWicDecoder()

    def cache_key(self, content: bytes, source_revision: str | None = None) -> str:
        if type(content) is not bytes or not content:
            raise ValueError("image_source_revision_invalid")
        if source_revision is not None and (not isinstance(source_revision, str) or not source_revision):
            raise ValueError("image_source_revision_invalid")
        return _sha256_text(_canonical_json({
            "content_identity": sha256_digest(content),
            "source_revision": source_revision or "",
            "processor_recipe": self.recipe.fingerprint,
            "policy": self.policy.fingerprint,
        }))

    def identity(self) -> dict[str, JSONValue]:
        """Persistent image-specific readiness identity; shared text space is untouched."""
        return {
            "schema": "mdrack.image-preparation.v1",
            "recipe_fingerprint": self.recipe.fingerprint,
            "policy_fingerprint": self.policy.fingerprint,
            "recipe_version": self.recipe.version,
            "decoder": self.recipe.decoder,
            "geometry": self.recipe.geometry,
            "pad_style": self.recipe.pad_style,
            "pad_color": self.recipe.pad_color,
            "bicubic_kernel": self.recipe.bicubic_kernel,
            "geometry_authority": self.recipe.geometry_authority,
            "interpolation_authority": self.recipe.interpolation_authority,
            "pad_authority": self.recipe.pad_authority,
            "pad_color_authority": self.recipe.pad_color_authority,
            "bicubic_kernel_authority": self.recipe.bicubic_kernel_authority,
            "alpha_policy": self.recipe.alpha_policy,
            "alpha_authority": self.recipe.alpha_authority,
            "orientation_policy": self.recipe.orientation_policy,
            "model_processor_fingerprint": self.recipe.model_processor_fingerprint,
            "tensor_owner": self.recipe.tensor_owner,
            "native_target": self.recipe.native_target,
            "projector_patch_size": self.recipe.projector_patch_size,
            "projector_scale_factor": self.recipe.projector_scale_factor,
            "native_target_factor": self.recipe.native_target_factor,
            "image_min_tokens": self.recipe.image_min_tokens,
            "image_max_tokens": self.recipe.image_max_tokens,
        }

    def validate_prepared(self, content: bytes) -> None:
        """Real WIC admission check for an input classified as already prepared."""
        if type(content) is not bytes or not content:
            raise ImagePreparationError("image_prepared_invalid")
        if len(content) > self.policy.max_prepared_bytes:
            raise ImagePreparationError("image_prepared_too_large")
        native_geometry = self.recipe.geometry == "native-dyn-size-source-rgb"
        if native_geometry:
            header_width, header_height = _png_rgb_dimensions(content)
            if (
                header_width > self.policy.max_prepared_width
                or header_height > self.policy.max_prepared_height
                or header_width * header_height > self.policy.max_pixels
                or header_width * header_height * 3 > self.policy.max_prepared_rgb_bytes
            ):
                raise ImagePreparationError("image_prepared_too_large")
        elif not _is_png_rgb(content, self.recipe.width, self.recipe.height):
            raise ImagePreparationError("image_prepared_invalid")
        prepared_policy = replace(self.policy, max_source_bytes=self.policy.max_prepared_bytes)
        try:
            decoded = self.decoder.decode(content, "image/png", prepared_policy)
        except ImagePreparationError:
            raise ImagePreparationError("image_prepared_invalid") from None
        except (OSError, ValueError, struct.error):
            raise ImagePreparationError("image_prepared_invalid") from None
        if decoded.orientation != 1:
            raise ImagePreparationError("image_prepared_invalid")
        if native_geometry:
            if (
                decoded.width != header_width or decoded.height != header_height
                or decoded.width > self.policy.max_prepared_width
                or decoded.height > self.policy.max_prepared_height
                or decoded.width * decoded.height > self.policy.max_pixels
                or decoded.width * decoded.height * 3 > self.policy.max_prepared_rgb_bytes
            ):
                raise ImagePreparationError("image_prepared_invalid")
        elif decoded.width != self.recipe.width or decoded.height != self.recipe.height:
            raise ImagePreparationError("image_prepared_invalid")

    def prepare(self, content: bytes, media_type: str, source_revision: str) -> PreparedImage:
        if type(content) is not bytes or not content:
            raise ImagePreparationError("image_source_empty")
        if media_type not in _SOURCE_MEDIA_TYPES:
            raise ImagePreparationError("image_format_unsupported")
        if len(content) > self.policy.max_source_bytes:
            raise ImagePreparationError("image_source_too_large")
        content_identity = sha256_digest(content)
        key = self.cache_key(content, source_revision)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        try:
            decoded = self.decoder.decode(content, media_type, self.policy)
        except ImagePreparationError:
            raise
        except (OSError, ValueError, struct.error):
            raise ImagePreparationError("image_decode_failed") from None
        if decoded.orientation < 1 or decoded.orientation > 8:
            raise ImagePreparationError("image_decode_failed")
        if (
            decoded.width < 1 or decoded.height < 1
            or decoded.width > self.policy.max_dimension or decoded.height > self.policy.max_dimension
        ):
            raise ImagePreparationError("image_dimensions_too_large")
        pixel_count = decoded.width * decoded.height
        if pixel_count > self.policy.max_pixels:
            raise ImagePreparationError("image_pixels_too_large")
        if pixel_count * 4 > self.policy.max_decoded_bytes:
            raise ImagePreparationError("image_decoded_too_large")
        pixels, width, height = _apply_orientation(
            decoded.pixels, decoded.width, decoded.height, decoded.orientation
        )
        alpha = _has_alpha(pixels)
        if alpha:
            _composite_white(pixels)
        if self.recipe.geometry == "native-dyn-size-source-rgb":
            if (
                width > self.policy.max_prepared_width or height > self.policy.max_prepared_height
                or width * height > self.policy.max_pixels
                or width * height * 3 > self.policy.max_prepared_rgb_bytes
            ):
                raise ImagePreparationError("image_prepared_too_large")
            rgb = _pixels_to_rgb(pixels, width, height)
            encoded = _encode_png(rgb, width, height)
            prepared_width, prepared_height = width, height
        elif self.recipe.geometry == "fit-center-pad-bicubic":
            rgb = _resize_selected(
                pixels, width, height, self.recipe.width, self.recipe.height,
                pad_color=(0, 0, 0),
            )
            encoded = _encode_png(rgb, self.recipe.width, self.recipe.height)
            prepared_width, prepared_height = self.recipe.width, self.recipe.height
        else:
            rgb = _resize_legacy_bilinear(pixels, width, height, self.recipe.width, self.recipe.height)
            encoded = _encode_png(rgb, self.recipe.width, self.recipe.height)
            prepared_width, prepared_height = self.recipe.width, self.recipe.height
        if len(encoded) > self.policy.max_prepared_bytes:
            raise ImagePreparationError("image_prepared_too_large")
        if not _is_png_rgb(encoded, prepared_width, prepared_height):
            raise ImagePreparationError("image_prepared_invalid")
        result = PreparedImage(
            encoded,
            sha256_digest(encoded),
            source_revision,
            self.recipe.fingerprint,
            key,
            media_type,
            decoded.width,
            decoded.height,
            decoded.orientation,
            alpha,
            content_identity,
        )
        self.cache.put(key, result)
        return result


def native_dyn_size_target(
    width: int, height: int, *, factor: int = 48, min_tokens: int = 70, max_tokens: int = 1120,
) -> tuple[int, int]:
    """Source-derived discriminator for selected native dyn_size arithmetic.

    This is a finite offline oracle only; production geometry remains in pinned
    MTMD. It reproduces the selected q48/min70/max1120 target table with float
    round/ceil/floor semantics (no caller resize is performed).
    """
    if min(width, height, factor, min_tokens, max_tokens) < 1:
        raise ValueError("image_target_invalid")
    amin = min_tokens * factor * factor
    amax = max_tokens * factor * factor

    def round_away(value: float) -> int:
        return int(math.floor(value + 0.5))

    target_width = max(factor, factor * round_away(width / factor))
    target_height = max(factor, factor * round_away(height / factor))
    area = target_width * target_height
    if area > amax:
        scale = math.sqrt(width * height / amax)
        target_width = max(factor, factor * math.floor(width / scale / factor))
        target_height = max(factor, factor * math.floor(height / scale / factor))
    elif area < amin:
        scale = math.sqrt(amin / (width * height))
        target_width = max(factor, factor * math.ceil(width * scale / factor))
        target_height = max(factor, factor * math.ceil(height * scale / factor))
    return target_width, target_height


def sha256_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and set(value) <= set("0123456789abcdef")


def _has_alpha(pixels: array[int]) -> bool:
    if not pixels:
        return False
    alpha = pixels.tobytes()[3::4]
    return alpha.count(255) != len(alpha)


def _composite_white(pixels: array[int]) -> None:
    for index, value in enumerate(pixels):
        a = value >> 24
        if a == 255:
            continue
        r = value & 0xFF
        g = (value >> 8) & 0xFF
        b = (value >> 16) & 0xFF
        blend = 255 - a
        pixels[index] = (
            0xFF000000
            | (((b * a + 255 * blend + 127) // 255) << 16)
            | (((g * a + 255 * blend + 127) // 255) << 8)
            | ((r * a + 255 * blend + 127) // 255)
        )


def _apply_orientation(
    pixels: array[int], width: int, height: int, orientation: int
) -> tuple[array[int], int, int]:
    if width < 1 or height < 1 or len(pixels) != width * height:
        raise ImagePreparationError("image_decode_failed")
    if orientation == 1:
        return pixels, width, height
    rows = [pixels[y * width:(y + 1) * width] for y in range(height)]
    if orientation == 2:
        return _join_rows(row[::-1] for row in rows), width, height
    if orientation == 3:
        return pixels[::-1], width, height
    if orientation == 4:
        return _join_rows(rows[::-1]), width, height
    if orientation == 5:
        return _join_rows(pixels[x::width] for x in range(width)), height, width
    if orientation == 6:
        return _join_rows(pixels[x::width][::-1] for x in range(width)), height, width
    if orientation == 7:
        return _join_rows(pixels[(width - 1 - x)::width][::-1] for x in range(width)), height, width
    if orientation == 8:
        return _join_rows(pixels[(width - 1 - x)::width] for x in range(width)), height, width
    raise ImagePreparationError("image_decode_failed")


def _join_rows(rows: Iterable[array[int]]) -> array[int]:
    payload = b"".join(row.tobytes() for row in rows)
    result = array("I")
    result.frombytes(payload)
    return result


def _resize_legacy_bilinear(
    pixels: array[int], width: int, height: int, target_width: int, target_height: int,
) -> bytes:
    """Explicit generic/legacy caller policy, retained for optional providers."""
    x_axis = [_legacy_axis(index, target_width, width) for index in range(target_width)]
    y_axis = [_legacy_axis(index, target_height, height) for index in range(target_height)]
    output = bytearray(target_width * target_height * 3)
    offset = 0
    for y0, y1, fy in y_axis:
        row0 = y0 * width
        row1 = y1 * width
        for x0, x1, fx in x_axis:
            p00 = pixels[row0 + x0]
            p10 = pixels[row0 + x1]
            p01 = pixels[row1 + x0]
            p11 = pixels[row1 + x1]
            r = _legacy_blend(p00 & 0xFF, p10 & 0xFF, p01 & 0xFF, p11 & 0xFF, fx, fy)
            g = _legacy_blend((p00 >> 8) & 0xFF, (p10 >> 8) & 0xFF, (p01 >> 8) & 0xFF, (p11 >> 8) & 0xFF, fx, fy)
            b = _legacy_blend((p00 >> 16) & 0xFF, (p10 >> 16) & 0xFF, (p01 >> 16) & 0xFF, (p11 >> 16) & 0xFF, fx, fy)
            output[offset] = r
            output[offset + 1] = g
            output[offset + 2] = b
            offset += 3
    return bytes(output)


def _legacy_axis(index: int, target: int, source: int) -> tuple[int, int, float]:
    position = (index + 0.5) * source / target - 0.5
    if position < 0.0:
        position = 0.0
    elif position > source - 1:
        position = float(source - 1)
    left = int(position)
    right = left + 1 if left + 1 < source else left
    return left, right, position - left


def _legacy_blend(a: int, b: int, c: int, d: int, fx: float, fy: float) -> int:
    top = a + (b - a) * fx
    bottom = c + (d - c) * fx
    return int(top + (bottom - top) * fy + 0.5)


def _resize_selected(
    pixels: array[int], width: int, height: int, target_width: int, target_height: int,
    *, pad_color: tuple[int, int, int],
) -> bytes:
    """Selected fixed-size geometry: aspect-fit bicubic resize, center black pad."""
    if width < 1 or height < 1 or target_width < 1 or target_height < 1:
        raise ImagePreparationError("image_decode_failed")
    scale = min(target_width / width, target_height / height)
    scaled_width = min(target_width, max(1, math.ceil(width * scale)))
    scaled_height = min(target_height, max(1, math.ceil(height * scale)))
    if scaled_width == width and scaled_height == height:
        resized = _pixels_to_rgb(pixels, width, height)
    else:
        resized = _resize_bicubic(pixels, width, height, scaled_width, scaled_height)
    canvas = bytearray(target_width * target_height * 3)
    if pad_color != (0, 0, 0):
        pixel = bytes(pad_color)
        canvas[:] = pixel * (target_width * target_height)
    offset_x = (target_width - scaled_width) // 2
    offset_y = (target_height - scaled_height) // 2
    source_stride = scaled_width * 3
    target_stride = target_width * 3
    for y in range(scaled_height):
        source_at = y * source_stride
        target_at = (offset_y + y) * target_stride + offset_x * 3
        canvas[target_at:target_at + source_stride] = resized[source_at:source_at + source_stride]
    return bytes(canvas)


def _pixels_to_rgb(pixels: array[int], width: int, height: int) -> bytes:
    output = bytearray(width * height * 3)
    at = 0
    for value in pixels:
        output[at] = value & 0xFF
        output[at + 1] = (value >> 8) & 0xFF
        output[at + 2] = (value >> 16) & 0xFF
        at += 3
    return bytes(output)


def _resize_bicubic(
    pixels: array[int], width: int, height: int, target_width: int, target_height: int,
) -> bytes:
    x_weights = [_filter_weights(index, target_width, width) for index in range(target_width)]
    y_weights = [_filter_weights(index, target_height, height) for index in range(target_height)]
    output = bytearray(target_width * target_height * 3)
    at = 0
    for y, yw in enumerate(y_weights):
        rows = [(source_y * width, weight) for source_y, weight in yw]
        for x_weights_for_pixel in x_weights:
            red = green = blue = 0.0
            for source_x, xw in x_weights_for_pixel:
                weight = xw
                for source_row, yw_value in rows:
                    value = pixels[source_row + source_x]
                    factor = weight * yw_value
                    red += (value & 0xFF) * factor
                    green += ((value >> 8) & 0xFF) * factor
                    blue += ((value >> 16) & 0xFF) * factor
            output[at] = _clamp_byte(red)
            output[at + 1] = _clamp_byte(green)
            output[at + 2] = _clamp_byte(blue)
            at += 3
    return bytes(output)


def _filter_weights(index: int, target: int, source: int) -> tuple[tuple[int, float], ...]:
    scale = source / target
    center = (index + 0.5) * scale - 0.5
    filter_scale = max(1.0, scale)
    support = 2.0 * filter_scale
    left = max(0, int(math.floor(center - support)))
    right = min(source - 1, int(math.ceil(center + support)))
    weights: list[tuple[int, float]] = []
    total = 0.0
    for source_index in range(left, right + 1):
        weight = _cubic((source_index - center) / filter_scale)
        if weight:
            weights.append((source_index, weight))
            total += weight
    if not weights or total == 0.0:
        nearest = min(source - 1, max(0, int(round(center))))
        return ((nearest, 1.0),)
    return tuple((source_index, weight / total) for source_index, weight in weights)


def _cubic(value: float, a: float = -0.5) -> float:
    value = abs(value)
    if value < 1.0:
        return (a + 2.0) * value ** 3 - (a + 3.0) * value ** 2 + 1.0
    if value < 2.0:
        return a * value ** 3 - 5.0 * a * value ** 2 + 8.0 * a * value - 4.0 * a
    return 0.0


def _clamp_byte(value: float) -> int:
    rounded = int(value + 0.5)
    return 0 if rounded < 0 else 255 if rounded > 255 else rounded


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _encode_png(rgb: bytes, width: int, height: int) -> bytes:
    stride = width * 3
    if len(rgb) != stride * height:
        raise ImagePreparationError("image_prepared_invalid")
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


def _png_rgb_dimensions(content: bytes) -> tuple[int, int]:
    if (
        len(content) < 33
        or content[:8] != b"\x89PNG\r\n\x1a\n"
        or content[8:16] != b"\x00\x00\x00\rIHDR"
        or content[24:29] != b"\x08\x02\x00\x00\x00"
    ):
        raise ImagePreparationError("image_prepared_invalid")
    width, height = struct.unpack(">II", content[16:24])
    if width < 1 or height < 1:
        raise ImagePreparationError("image_prepared_invalid")
    return width, height


def _is_png_rgb(content: bytes, width: int, height: int) -> bool:
    return (
        len(content) >= 33
        and content[:8] == b"\x89PNG\r\n\x1a\n"
        and content[8:16] == b"\x00\x00\x00\rIHDR"
        and struct.unpack(">II", content[16:24]) == (width, height)
        and content[24:29] == b"\x08\x02\x00\x00\x00"
    )


# --- EXIF orientation (container parsing; no image library) -----------------

def _container_looks_complete(content: bytes, media_type: str) -> bool:
    """Cheap structural gate before handing bytes to a system codec."""
    if media_type == "image/png":
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            return False
        index = 8
        while index + 12 <= len(content):
            length = struct.unpack(">I", content[index:index + 4])[0]
            end = index + 12 + length
            if end > len(content):
                return False
            if content[index + 4:index + 8] == b"IEND":
                return True
            index = end
        return False
    if media_type == "image/webp":
        if len(content) < 12 or content[:4] != b"RIFF" or content[8:12] != b"WEBP":
            return False
        declared = int(struct.unpack("<I", content[4:8])[0]) + 8
        if declared > len(content):
            return False
        index = 12
        while index + 8 <= declared:
            length = struct.unpack("<I", content[index + 4:index + 8])[0]
            end = index + 8 + length
            if end > declared:
                return False
            index = end + (length & 1)
        return index >= declared
    if media_type == "image/jpeg":
        if len(content) < 4 or content[:2] != b"\xff\xd8":
            return False
        index = 2
        saw_scan = False
        while index + 1 < len(content):
            if content[index] != 0xFF:
                index += 1
                continue
            while index < len(content) and content[index] == 0xFF:
                index += 1
            if index >= len(content):
                return False
            marker = content[index]
            index += 1
            if marker == 0xDA:
                saw_scan = True
                break
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > len(content):
                return False
            length = struct.unpack(">H", content[index:index + 2])[0]
            if length < 2 or index + length > len(content):
                return False
            index += length
        return saw_scan and content.rfind(b"\xff\xd9") > index
    return False


def _exif_orientation(content: bytes, media_type: str) -> int:
    try:
        if media_type == "image/jpeg":
            payload = _jpeg_exif(content)
        elif media_type == "image/png":
            payload = _png_exif(content)
        elif media_type == "image/webp":
            payload = _webp_exif(content)
        else:
            return 1
        if payload is None:
            return 1
        return _tiff_orientation(payload)
    except (IndexError, struct.error, ValueError):
        return 1


def _jpeg_exif(content: bytes) -> bytes | None:
    if len(content) < 4 or content[:2] != b"\xff\xd8":
        return None
    index = 2
    while index + 4 <= len(content):
        if content[index] != 0xFF:
            index += 1
            continue
        while index < len(content) and content[index] == 0xFF:
            index += 1
        if index >= len(content):
            return None
        marker = content[index]
        index += 1
        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0xDA:
            return None
        if index + 2 > len(content):
            return None
        length = struct.unpack(">H", content[index:index + 2])[0]
        if length < 2 or index + length > len(content):
            return None
        payload = content[index + 2:index + length]
        if marker == 0xE1 and payload.startswith(b"Exif\x00\x00"):
            return payload[6:]
        index += length
    return None


def _png_exif(content: bytes) -> bytes | None:
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    index = 8
    while index + 12 <= len(content):
        length = struct.unpack(">I", content[index:index + 4])[0]
        kind = content[index + 4:index + 8]
        end = index + 12 + length
        if end > len(content):
            return None
        if kind == b"eXIf":
            return content[index + 8:index + 8 + length]
        if kind == b"IEND":
            return None
        index = end
    return None


def _webp_exif(content: bytes) -> bytes | None:
    if len(content) < 12 or content[:4] != b"RIFF" or content[8:12] != b"WEBP":
        return None
    index = 12
    while index + 8 <= len(content):
        kind = content[index:index + 4]
        length = struct.unpack("<I", content[index + 4:index + 8])[0]
        end = index + 8 + length
        if end > len(content):
            return None
        if kind == b"EXIF":
            payload = content[index + 8:end]
            return payload[6:] if payload.startswith(b"Exif\x00\x00") else payload
        index = end + (length & 1)
    return None


def _tiff_orientation(data: bytes) -> int:
    if len(data) < 8:
        return 1
    if data[:2] == b"II":
        endian = "<"
    elif data[:2] == b"MM":
        endian = ">"
    else:
        return 1
    if struct.unpack(endian + "H", data[2:4])[0] != 42:
        return 1
    offset = struct.unpack(endian + "I", data[4:8])[0]
    if offset + 2 > len(data):
        return 1
    count = struct.unpack(endian + "H", data[offset:offset + 2])[0]
    if offset + 2 + count * 12 > len(data):
        return 1
    for item in range(count):
        start = offset + 2 + item * 12
        tag, kind = struct.unpack(endian + "HH", data[start:start + 4])
        if tag != 0x0112:
            continue
        amount = struct.unpack(endian + "I", data[start + 4:start + 8])[0]
        if amount != 1:
            return 1
        if kind == 3:
            value = struct.unpack(endian + "H", data[start + 8:start + 10])[0]
        elif kind == 4:
            value = struct.unpack(endian + "I", data[start + 8:start + 12])[0]
        else:
            return 1
        return value if 1 <= value <= 8 else 1
    return 1


# --- Windows WIC decoder ----------------------------------------------------

_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(d1: int, d2: int, d3: int, rest: tuple[int, ...]) -> _GUID:
    return _GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*rest))


_CLSID_WICImagingFactory = _guid(0xCACAF262, 0x9370, 0x4615, (0xA1, 0x3B, 0x9F, 0x55, 0x39, 0xDA, 0x4C, 0x0A))
_IID_IWICImagingFactory = _guid(0xEC5EC8A9, 0xC395, 0x4314, (0x9C, 0x77, 0x54, 0xD7, 0xA9, 0x35, 0xFF, 0x70))
_WICPixelFormat32bppRGBA = _guid(0xF5C7AD2D, 0x6A8D, 0x43DD, (0xA7, 0xA8, 0xA2, 0x99, 0x35, 0x26, 0x1A, 0xE9))
_ContainerPng = _guid(0x1B7CFAF4, 0x713F, 0x473C, (0xBB, 0xCD, 0x61, 0x37, 0x42, 0x5F, 0xAE, 0xAF))
_ContainerJpeg = _guid(0x19E4A5AA, 0x5662, 0x4FC5, (0xA0, 0xC0, 0x17, 0x58, 0x02, 0x8E, 0x10, 0x57))
_ContainerWebp = _guid(0xE094B0E2, 0x67F2, 0x45B3, (0xB0, 0xEA, 0x11, 0x53, 0x37, 0xCA, 0x7C, 0xF3))
_RPC_E_CHANGED_MODE = -2147417850  # 0x80010106


def _com_call(obj: int | None, index: int, restype: Any, *argtypes: Any) -> Any:
    if obj is None:
        raise OSError("null COM interface")
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return _WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])


def _check_hresult(value: Any) -> None:
    if int(value) < 0:
        raise OSError(f"HRESULT 0x{int(value) & 0xFFFFFFFF:08X}")


def _release(obj: ctypes.c_void_p | None) -> None:
    if obj and obj.value:
        try:
            _com_call(obj.value, 2, ctypes.c_uint32)(obj.value)
        except OSError:
            pass


class WindowsWicDecoder:
    """In-memory-content decoder through the pinned host's Windows WIC path."""

    def decode(self, content: bytes, media_type: str, policy: ImageProcessPolicy) -> DecodedImage:
        if os.name != "nt":
            raise ImagePreparationError("image_decoder_unavailable")
        if media_type not in _SOURCE_MEDIA_TYPES:
            raise ImagePreparationError("image_format_unsupported")
        if len(content) > policy.max_source_bytes:
            raise ImagePreparationError("image_source_too_large")
        if not _container_looks_complete(content, media_type):
            raise ImagePreparationError("image_decode_failed")
        ole32 = getattr(ctypes, "windll").ole32
        ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
        ]
        ole32.CoCreateInstance.restype = ctypes.c_int32
        initialized = False
        factory: ctypes.c_void_p | None = None
        decoder: ctypes.c_void_p | None = None
        frame: ctypes.c_void_p | None = None
        converter: ctypes.c_void_p | None = None
        path: Path | None = None
        try:
            result = ole32.CoInitializeEx(None, 0)
            if result < 0 and result != _RPC_E_CHANGED_MODE:
                raise ImagePreparationError("image_decoder_unavailable")
            initialized = result >= 0
            factory = ctypes.c_void_p()
            _check_hresult(ole32.CoCreateInstance(
                ctypes.byref(_CLSID_WICImagingFactory), None, 1,
                ctypes.byref(_IID_IWICImagingFactory), ctypes.byref(factory),
            ))
            with tempfile.NamedTemporaryFile(prefix="mdrack-image-", suffix=".bin", delete=False) as stream:
                stream.write(content)
                path = Path(stream.name)
            decoder = ctypes.c_void_p()
            try:
                _check_hresult(_com_call(
                    factory.value, 3, ctypes.c_int32, ctypes.c_wchar_p, ctypes.POINTER(_GUID),
                    ctypes.c_uint32, ctypes.c_int32, ctypes.POINTER(ctypes.c_void_p),
                )(factory.value, str(path), None, 0x80000000, 0, ctypes.byref(decoder)))
            except OSError:
                raise ImagePreparationError("image_decoder_unavailable") from None
            container = _GUID()
            _check_hresult(_com_call(
                decoder.value, 5, ctypes.c_int32, ctypes.POINTER(_GUID)
            )(decoder.value, ctypes.byref(container)))
            expected = {
                "image/png": _ContainerPng,
                "image/jpeg": _ContainerJpeg,
                "image/webp": _ContainerWebp,
            }[media_type]
            if bytes(container) != bytes(expected):
                raise ImagePreparationError("image_format_unsupported")
            frame_count = ctypes.c_uint32()
            _check_hresult(_com_call(
                decoder.value, 12, ctypes.c_int32, ctypes.POINTER(ctypes.c_uint32)
            )(decoder.value, ctypes.byref(frame_count)))
            if frame_count.value != 1:
                raise ImagePreparationError("image_format_unsupported")
            frame = ctypes.c_void_p()
            _check_hresult(_com_call(
                decoder.value, 13, ctypes.c_int32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)
            )(decoder.value, 0, ctypes.byref(frame)))
            width = ctypes.c_uint32()
            height = ctypes.c_uint32()
            _check_hresult(_com_call(
                frame.value, 3, ctypes.c_int32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)
            )(frame.value, ctypes.byref(width), ctypes.byref(height)))
            pixel_count = width.value * height.value
            if (
                width.value < 1 or height.value < 1
                or width.value > policy.max_dimension or height.value > policy.max_dimension
            ):
                raise ImagePreparationError("image_dimensions_too_large")
            if pixel_count > policy.max_pixels:
                raise ImagePreparationError("image_pixels_too_large")
            decoded_bytes = pixel_count * 4
            if decoded_bytes > policy.max_decoded_bytes:
                raise ImagePreparationError("image_decoded_too_large")
            converter = ctypes.c_void_p()
            _check_hresult(_com_call(
                factory.value, 10, ctypes.c_int32, ctypes.POINTER(ctypes.c_void_p)
            )(factory.value, ctypes.byref(converter)))
            _check_hresult(_com_call(
                converter.value, 8, ctypes.c_int32, ctypes.c_void_p, ctypes.POINTER(_GUID),
                ctypes.c_int32, ctypes.c_void_p, ctypes.c_double, ctypes.c_int32,
            )(converter.value, frame.value, ctypes.byref(_WICPixelFormat32bppRGBA), 0, None, 0.0, 0))
            buffer = (ctypes.c_ubyte * decoded_bytes)()
            _check_hresult(_com_call(
                converter.value, 7, ctypes.c_int32, ctypes.c_void_p, ctypes.c_uint32,
                ctypes.c_uint32, ctypes.POINTER(ctypes.c_ubyte),
            )(converter.value, None, width.value * 4, decoded_bytes, buffer))
            pixels = array("I")
            pixels.frombytes(bytes(buffer))
            if sys.byteorder != "little":
                pixels.byteswap()
            return DecodedImage(
                width.value, height.value, pixels,
                _exif_orientation(content, media_type), media_type,
            )
        finally:
            _release(converter)
            _release(frame)
            _release(decoder)
            _release(factory)
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            if initialized:
                ole32.CoUninitialize()
