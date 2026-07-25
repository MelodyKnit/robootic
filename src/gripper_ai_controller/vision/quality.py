"""Pure image-quality measurements for normalized camera frames."""

import math

import numpy
from PIL import Image

from gripper_ai_controller.configuration import VisionAnalysisSettings
from gripper_ai_controller.domain.models import ImageFrame
from gripper_ai_controller.vision.models import FrameQualityDiagnostics


FRAME_QUALITY_WARNING_CODES = (
    "resolution_low",
    "brightness_low",
    "brightness_high",
    "contrast_low",
    "sharpness_low",
    "frame_unavailable",
    "frame_dimensions_invalid",
    "mono_payload_size_invalid",
    "rgb_payload_size_invalid",
    "pixel_format_unsupported",
)
"""Stable warning identifiers emitted by :class:`FrameQualityInspector`."""


class FrameQualityInspector:
    """Classify image health without changing camera state or retaining pixel payloads."""

    def __init__(self, settings: VisionAnalysisSettings) -> None:
        """Store validated warning thresholds for one preview service."""

        self.settings = settings

    def inspect(self, frame: ImageFrame) -> FrameQualityDiagnostics:
        """Measure a bounded sample of normalized pixels and report warnings.

        The diagnostics describe the full acquired frame, but their pixel statistics
        intentionally use a bounded working image.  This keeps quality inspection a
        lightweight diagnostic path when a camera publishes high-resolution frames.
        """

        if not frame.healthy or frame.pixel_payload is None:
            return self._invalid(frame, "frame_unavailable")
        if frame.width is None or frame.height is None or frame.width <= 0 or frame.height <= 0:
            return self._invalid(frame, "frame_dimensions_invalid")
        try:
            luminance = self._luminance(frame)
        except ValueError as error:
            return self._invalid(frame, str(error))

        brightness = float(numpy.mean(luminance))
        contrast = float(numpy.std(luminance))
        sharpness = self._laplacian_variance(luminance)
        warnings = []
        if frame.width < self.settings.minimum_width or frame.height < self.settings.minimum_height:
            warnings.append("resolution_low")
        if brightness < self.settings.minimum_brightness:
            warnings.append("brightness_low")
        elif brightness > self.settings.maximum_brightness:
            warnings.append("brightness_high")
        if contrast < self.settings.minimum_contrast:
            warnings.append("contrast_low")
        if sharpness < self.settings.minimum_sharpness:
            warnings.append("sharpness_low")
        return FrameQualityDiagnostics(
            frame.captured_at,
            True,
            frame.width,
            frame.height,
            frame.pixel_format,
            brightness,
            contrast,
            sharpness,
            tuple(warnings),
        )

    def _luminance(self, frame: ImageFrame):
        """Return a sampled monochrome matrix after validating the payload layout.

        Pillow performs the RGB-to-luminance conversion and resize in native code.
        The return value therefore has a longest side no larger than the configured
        sample limit, while all public diagnostics retain the original dimensions.
        """

        pixel_count = frame.width * frame.height
        if frame.pixel_format == "mono8":
            if len(frame.pixel_payload) != pixel_count:
                raise ValueError("mono_payload_size_invalid")
            image = Image.frombytes("L", (frame.width, frame.height), frame.pixel_payload)
        elif frame.pixel_format == "rgb8":
            if len(frame.pixel_payload) != pixel_count * 3:
                raise ValueError("rgb_payload_size_invalid")
            image = Image.frombytes("RGB", (frame.width, frame.height), frame.pixel_payload).convert("L")
        else:
            raise ValueError("pixel_format_unsupported")

        maximum_side = max(frame.width, frame.height)
        sample_max_side = int(getattr(self.settings, "sample_max_side", 640))
        if sample_max_side <= 0:
            # Configuration validation rejects this in normal startup. Keeping the
            # diagnostic path total prevents a malformed direct test fixture from
            # taking down the camera capture loop.
            sample_max_side = 640
        if maximum_side > sample_max_side:
            scale = float(sample_max_side) / float(maximum_side)
            width = max(1, int(math.floor(frame.width * scale)))
            height = max(1, int(math.floor(frame.height * scale)))
            resampling = getattr(Image, "Resampling", Image).BILINEAR
            image = image.resize((width, height), resampling)
        return numpy.asarray(image, dtype=numpy.uint8)

    @staticmethod
    def _laplacian_variance(luminance) -> float:
        """Calculate a compact focus proxy without depending on OpenCV native bindings."""

        if luminance.shape[0] < 3 or luminance.shape[1] < 3:
            return 0.0
        values = luminance.astype(numpy.float32)
        padded = numpy.pad(values, 1, mode="edge")
        laplacian = (
            padded[:-2, 1:-1]
            + padded[2:, 1:-1]
            + padded[1:-1, :-2]
            + padded[1:-1, 2:]
            - 4.0 * values
        )
        return float(numpy.var(laplacian))

    @staticmethod
    def _invalid(frame: ImageFrame, warning: str) -> FrameQualityDiagnostics:
        """Expose malformed frames as diagnostics rather than throwing from the capture loop."""

        return FrameQualityDiagnostics(
            frame.captured_at,
            False,
            frame.width,
            frame.height,
            frame.pixel_format,
            None,
            None,
            None,
            (warning,),
        )
