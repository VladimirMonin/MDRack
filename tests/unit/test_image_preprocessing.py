"""Ordinary image preparation: WIC decode, EXIF, alpha, limits and cache identity."""

from __future__ import annotations

import base64
import os
import struct
import zlib
from array import array

import pytest

from mdrack.ingestion.image_preprocessing import (
    DecodedImage,
    ImagePreparationCache,
    ImagePreparationError,
    ImagePreprocessor,
    ImageProcessPolicy,
    ImageProcessRecipe,
    WindowsWicDecoder,
    _exif_orientation,
    native_dyn_size_target,
    sha256_digest,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows WIC decoder")

JPEG_ORIENTATION_6 = base64.b64decode(
    "/9j/4QAiRXhpZgAASUkqAAgAAAABABIBAwABAAAABgAAAAAAAAD//gAPTGF2YzYzLjEuMTAxAP/bAEMACAQE"
    "BAQEBQUFBQUFBgYGBgYGBgYGBgYGBgcHBwgICAcHBwYGBwcICAgICQkJCAgICAkJCgoKDAwLCw4ODhERFP/E"
    "AFoAAQEAAAAAAAAAAAAAAAAAAAAHAQEBAQAAAAAAAAAAAAAAAAAABQcQAAEFAQAAAAAAAAAAAAAAAAAEAgWz"
    "NYMRAAEFAQEAAAAAAAAAAAAAAAAEBwU3tYWE/8AAEQgAAgAEAwESAAISAAMSAP/aAAwDAQACEQMRAD8AlE9r"
    "KOVTBPayjlUw1RkqwgOjpqwyVYQHR01ZQd+xJvwZ6YO/Yk34M9Mf/9k="
)
WEBP_RED_BLUE = base64.b64decode("UklGRiIAAABXRUJQVlA4TBYAAAAvA0AAAA8Q87//8x8OFAIIgIImov/x")


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _png(w: int, h: int, rgba: bytes) -> bytes:
    rows = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(rows))
        + _chunk(b"IEND", b"")
    )


def _png_rgb(width: int, height: int, rgb: bytes) -> bytes:
    stride = width * 3
    rows = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(rows))
        + _chunk(b"IEND", b"")
    )


def _read_png_rgb(data: bytes) -> tuple[int, int, bytes]:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    index = 8
    width = height = 0
    compressed = bytearray()
    while index + 12 <= len(data):
        length = struct.unpack(">I", data[index:index + 4])[0]
        kind = data[index + 4:index + 8]
        payload = data[index + 8:index + 8 + length]
        if kind == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", payload[:10])
            assert (depth, color) == (8, 2)
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            break
        index += 12 + length
    raw = zlib.decompress(bytes(compressed))
    stride = width * 3
    assert len(raw) == height * (stride + 1)
    rows = []
    for y in range(height):
        start = y * (stride + 1)
        assert raw[start] == 0
        rows.append(raw[start + 1:start + 1 + stride])
    return width, height, b"".join(rows)


def _pixel(rgb: bytes, width: int, x: int, y: int) -> tuple[int, int, int]:
    offset = (y * width + x) * 3
    return rgb[offset], rgb[offset + 1], rgb[offset + 2]


class _FakeDecoder:
    def __init__(self, width: int, height: int, pixels: list[int], orientation: int = 1) -> None:
        self.width = width
        self.height = height
        self.pixels = pixels
        self.orientation = orientation
        self.calls = 0

    def decode(self, content: bytes, media_type: str, policy: ImageProcessPolicy) -> DecodedImage:
        del content, policy
        self.calls += 1
        return DecodedImage(self.width, self.height, array("I", self.pixels), self.orientation, media_type)


def _rgba(r: int, g: int, b: int, a: int) -> int:
    return r | (g << 8) | (b << 16) | (a << 24)


def test_png_alpha_is_composited_white_and_output_is_224_rgb() -> None:
    source = _png(2, 1, bytes((0, 0, 0, 0)) + bytes((255, 0, 0, 128)))
    preprocessor = ImagePreprocessor()
    prepared = preprocessor.prepare(source, "image/png", sha256_digest(source))
    width, height, rgb = _read_png_rgb(prepared.content)
    assert (width, height) == (224, 224)
    assert prepared.alpha_composited is True
    assert _pixel(rgb, 224, 0, 0) == (255, 255, 255)
    assert _pixel(rgb, 224, 223, 0) == (255, 127, 127)
    assert prepared.source_revision == sha256_digest(source)
    assert prepared.identity != prepared.source_revision


def test_jpeg_exif_orientation_6_and_original_revision() -> None:
    preprocessor = ImagePreprocessor()
    prepared = preprocessor.prepare(JPEG_ORIENTATION_6, "image/jpeg", sha256_digest(JPEG_ORIENTATION_6))
    width, height, rgb = _read_png_rgb(prepared.content)
    assert (width, height) == (224, 224)
    assert (prepared.source_width, prepared.source_height, prepared.orientation) == (4, 2, 6)
    top = _pixel(rgb, 224, 112, 10)
    bottom = _pixel(rgb, 224, 112, 213)
    assert top[0] > 240 and top[1] < 15 and top[2] < 15
    assert bottom[0] < 15 and bottom[1] < 15 and bottom[2] > 240
    assert prepared.source_revision == sha256_digest(JPEG_ORIENTATION_6)


def test_webp_decodes_through_the_same_bounded_recipe() -> None:
    preprocessor = ImagePreprocessor()
    prepared = preprocessor.prepare(WEBP_RED_BLUE, "image/webp", sha256_digest(WEBP_RED_BLUE))
    width, height, rgb = _read_png_rgb(prepared.content)
    assert (width, height) == (224, 224)
    left = _pixel(rgb, 224, 10, 112)
    right = _pixel(rgb, 224, 213, 112)
    assert left[0] > 240 and left[1] < 15 and left[2] < 15
    assert right[0] < 15 and right[1] < 15 and right[2] > 240


def test_derived_cache_key_is_source_revision_plus_recipe_and_reuses() -> None:
    decoder = _FakeDecoder(2, 2, [_rgba(10, 20, 30, 255)] * 4)
    cache = ImagePreparationCache()
    recipe = ImageProcessRecipe()
    preprocessor = ImagePreprocessor(recipe=recipe, cache=cache, decoder=decoder)
    source = b"ordinary-source-bytes"
    first = preprocessor.prepare(source, "image/png", sha256_digest(source))
    second = preprocessor.prepare(source, "image/png", sha256_digest(source))
    assert decoder.calls == 1
    assert cache.hits == 1
    assert first is second
    assert first.cache_key == preprocessor.cache_key(source, first.source_revision)
    other_recipe = ImagePreprocessor(
        recipe=ImageProcessRecipe(version="ordinary-image-v2"),
        cache=cache,
        decoder=decoder,
    )
    third = other_recipe.prepare(source, "image/png", sha256_digest(source))
    assert decoder.calls == 2
    assert third.cache_key != first.cache_key
    fourth = other_recipe.prepare(b"different", "image/png", sha256_digest(b"different"))
    assert decoder.calls == 3
    assert fourth.cache_key not in {first.cache_key, third.cache_key}




def test_cache_identity_uses_actual_bytes_not_opaque_identity() -> None:
    decoder = _FakeDecoder(2, 2, [_rgba(10, 20, 30, 255)] * 4)
    preprocessor = ImagePreprocessor(decoder=decoder)
    red = b"red-image-bytes"
    blue = b"blue-image-bytes"
    first = preprocessor.prepare(red, "image/png", "query")
    second = preprocessor.prepare(blue, "image/png", "query")
    assert decoder.calls == 2
    assert first.cache_key != second.cache_key
    assert first.content_identity != second.content_identity
    assert first.source_revision == second.source_revision == "query"


def test_selected_processor_policy_binding_is_explicit() -> None:
    recipe = ImageProcessRecipe.selected_gemma4v(
        processor_artifact_sha256="74bf65c860d292a1eebafd7ca85b44532a2528f03017f9204fe1695916d2c66f",
        native_runtime_sha256="adde27eda9830320c41dc079fc66a47692f3c0fb2e3eed9b3a5f4bdaeaf25e27",
    )
    identity = ImagePreprocessor(recipe=recipe).identity()
    assert identity["tensor_owner"] == "pinned-mtmd-dyn-size"
    assert identity["geometry"] == "native-dyn-size-source-rgb"
    assert identity["pad_style"] == "native"
    assert identity["geometry_authority"] == "selected-gemma4v-dyn-size-enum13"
    assert identity["interpolation_authority"] == "native-mtmd-dyn-size-bicubic"
    assert identity["pad_authority"] == "native-mtmd-dyn-size-center-black"
    assert identity["native_target"] == "dyn_size"
    assert identity["projector_scale_factor"] == 3
    assert identity["native_target_factor"] == 48
    assert identity["image_min_tokens"] == 70 and identity["image_max_tokens"] == 1120
    assert identity["alpha_authority"] == "caller-policy-no-selected-background-key"
    assert recipe.model_metadata_sha256 == "1a1a3195cd31fb9df57ed387001bad4e22f97e098551149f4cf5448121fd4b68"


def test_selected_non_square_preserves_source_pixels_without_caller_resize() -> None:
    width, height = 7, 3
    rgba = bytearray()
    for y in range(height):
        for x in range(width):
            rgba.extend(((x * 37 + 11) % 256, (y * 83 + 7) % 256, (x * 19 + y * 29 + 3) % 256, 255))
    source = _png(width, height, bytes(rgba))
    legacy = ImagePreprocessor().prepare(source, "image/png", sha256_digest(source))
    selected = ImagePreprocessor(
        ImageProcessRecipe.selected_gemma4v(),
        policy=ImageProcessPolicy.selected_native_geometry(),
    ).prepare(source, "image/png", sha256_digest(source))
    out_width, out_height, rgb = _read_png_rgb(selected.content)
    assert (out_width, out_height) == (width, height)
    expected = b"".join(bytes(rgba[y * width * 4:(y + 1) * width * 4][i:i + 3])
                        for y in range(height) for i in range(0, width * 4, 4))
    assert rgb == expected
    assert selected.content != legacy.content
    assert native_dyn_size_target(width, height) == (624, 288)


def test_selected_native_transport_bounds_are_separate_from_source() -> None:
    policy = ImageProcessPolicy.selected_native_geometry()
    assert policy.max_source_bytes == 33_554_432
    assert policy.max_decoded_bytes == 67_108_864
    assert policy.max_prepared_bytes == 67_108_864
    assert (policy.max_prepared_width, policy.max_prepared_height) == (8192, 8192)
    assert policy.max_pixels == 16_777_216
    assert policy.max_prepared_rgb_bytes == 50_331_648
    with pytest.raises(ImagePreparationError) as failure:
        ImagePreprocessor(
            ImageProcessRecipe.selected_gemma4v(),
            policy=ImageProcessPolicy(max_prepared_rgb_bytes=10),
            decoder=_FakeDecoder(7, 3, [_rgba(0, 0, 0, 255)] * 21),
        ).prepare(b"x", "image/png", sha256_digest(b"x"))
    assert failure.value.code == "image_prepared_too_large"


def test_prepared_png_uses_its_own_transport_budget() -> None:
    source = _png_rgb(7, 3, bytes((1, 2, 3)) * 21)
    policy = ImageProcessPolicy(max_source_bytes=10, max_prepared_bytes=1000,
                                max_prepared_width=7, max_prepared_height=3,
                                max_prepared_rgb_bytes=63)
    ImagePreprocessor(
        ImageProcessRecipe.selected_gemma4v(), policy=policy
    ).validate_prepared(source)


def test_selected_native_target_discriminator_table() -> None:
    assert native_dyn_size_target(7, 3) == (624, 288)
    assert native_dyn_size_target(3, 7) == (288, 624)
    assert native_dyn_size_target(224, 224) == (432, 432)
    assert native_dyn_size_target(640, 480) == (624, 480)
    assert native_dyn_size_target(4000, 3000) == (1824, 1344)


def test_selected_alpha_pixels_are_preserved_at_source_geometry() -> None:
    source = _png(2, 1, bytes((0, 0, 0, 0)) + bytes((255, 0, 0, 128)))
    selected = ImagePreprocessor(
        ImageProcessRecipe.selected_gemma4v(),
        policy=ImageProcessPolicy.selected_native_geometry(),
    ).prepare(source, "image/png", sha256_digest(source))
    width, height, rgb = _read_png_rgb(selected.content)
    assert (width, height) == (2, 1)
    assert _pixel(rgb, 2, 0, 0) == (255, 255, 255)
    assert _pixel(rgb, 2, 1, 0) == (255, 127, 127)


def test_selected_jpeg_orientation_and_alpha_pixels_are_preserved() -> None:
    selected = ImagePreprocessor(
        ImageProcessRecipe.selected_gemma4v(),
        policy=ImageProcessPolicy.selected_native_geometry(),
    ).prepare(JPEG_ORIENTATION_6, "image/jpeg", sha256_digest(JPEG_ORIENTATION_6))
    width, height, rgb = _read_png_rgb(selected.content)
    assert (width, height) == (2, 4)
    assert _pixel(rgb, 2, 0, 0)[0] > 240 and _pixel(rgb, 2, 0, 0)[2] < 15
    assert _pixel(rgb, 2, 0, 3)[2] > 240 and _pixel(rgb, 2, 0, 3)[0] < 15


@pytest.mark.parametrize(
    ("policy", "width", "height", "code"),
    [
        (ImageProcessPolicy(max_dimension=50), 100, 10, "image_dimensions_too_large"),
        (ImageProcessPolicy(max_pixels=10), 10, 10, "image_pixels_too_large"),
        (ImageProcessPolicy(max_decoded_bytes=10), 2, 2, "image_decoded_too_large"),
    ],
)
def test_pixel_dimension_and_decode_limits_fail_before_preparation(
    policy: ImageProcessPolicy, width: int, height: int, code: str
) -> None:
    decoder = _FakeDecoder(width, height, [_rgba(0, 0, 0, 255)] * (width * height))
    preprocessor = ImagePreprocessor(policy=policy, decoder=decoder)
    with pytest.raises(ImagePreparationError) as failure:
        preprocessor.prepare(b"x", "image/png", sha256_digest(b"x"))
    assert failure.value.code == code


def test_source_and_prepared_byte_limits_are_separate() -> None:
    source_policy = ImageProcessPolicy(max_source_bytes=2)
    with pytest.raises(ImagePreparationError) as failure:
        ImagePreprocessor(policy=source_policy, decoder=_FakeDecoder(1, 1, [0xFFFFFFFF])).prepare(
            b"three", "image/png", sha256_digest(b"three")
        )
    assert failure.value.code == "image_source_too_large"
    prepared_policy = ImageProcessPolicy(max_prepared_bytes=1)
    with pytest.raises(ImagePreparationError) as failure:
        ImagePreprocessor(policy=prepared_policy, decoder=_FakeDecoder(1, 1, [0xFFFFFFFF])).prepare(
            b"x", "image/png", sha256_digest(b"x")
        )
    assert failure.value.code == "image_prepared_too_large"


def test_corrupt_and_unsupported_inputs_refuse_clearly() -> None:
    with pytest.raises(ImagePreparationError) as failure:
        ImagePreprocessor().prepare(b"not-an-image", "image/png", sha256_digest(b"not-an-image"))
    assert failure.value.code in {"image_decode_failed", "image_decoder_unavailable"}
    with pytest.raises(ImagePreparationError) as failure:
        ImagePreprocessor().prepare(b"GIF89a", "image/gif", sha256_digest(b"GIF89a"))
    assert failure.value.code == "image_format_unsupported"


def test_exif_orientation_parser_rejects_malformed_metadata() -> None:
    assert _exif_orientation(b"\xff\xd8\xff\xe1\x00\x02", "image/jpeg") == 1
    assert _exif_orientation(b"not-exif", "image/png") == 1
    assert WindowsWicDecoder is not None
