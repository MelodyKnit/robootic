"""JPEG encoding for normalized acquisition frames without persistent image storage."""

from io import BytesIO

from PIL import Image

from gripper_ai_controller.domain.models import ImageFrame


class FrameEncodingError(ValueError):
    """Report an invalid or unsupported normalized camera frame for browser delivery."""


class JpegFrameEncoder:
    """Encode validated RGB8 or Mono8 payloads into browser-compatible JPEG bytes."""

    def __init__(self, quality: int) -> None:
        """Store a bounded JPEG quality already validated by preview configuration."""

        self.quality = quality

    def encode(self, frame: ImageFrame) -> bytes:
        """Return one JPEG payload without writing the source image or result to disk."""

        if not frame.healthy:
            raise FrameEncodingError("The camera marked this frame unhealthy.")
        if frame.pixel_payload is None:
            raise FrameEncodingError("The frame does not contain pixel data.")
        if frame.width is None or frame.height is None:
            raise FrameEncodingError("The frame lacks width or height metadata.")
        if frame.width <= 0 or frame.height <= 0:
            raise FrameEncodingError("The frame dimensions must be positive.")

        mode, bytes_per_pixel = self._pixel_layout(frame.pixel_format)
        expected_length = frame.width * frame.height * bytes_per_pixel
        if len(frame.pixel_payload) != expected_length:
            raise FrameEncodingError(
                "The frame pixel payload length does not match its dimensions and pixel format."
            )
        try:
            image = Image.frombytes(mode, (frame.width, frame.height), frame.pixel_payload)
            output = BytesIO()
            image.save(output, format="JPEG", quality=self.quality, optimize=False)
            return output.getvalue()
        except (ValueError, OSError) as error:
            raise FrameEncodingError("Unable to encode the normalized frame as JPEG.") from error

    @staticmethod
    def _pixel_layout(pixel_format):
        """Map stable generic pixel-format identifiers to Pillow image layouts."""

        if pixel_format == "rgb8":
            return "RGB", 3
        if pixel_format == "mono8":
            return "L", 1
        raise FrameEncodingError("The frame pixel format is not supported for browser preview.")
