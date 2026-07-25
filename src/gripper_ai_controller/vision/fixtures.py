"""License-aware local image fixtures and offline pose-evaluation helpers."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

from gripper_ai_controller.domain.models import ImageFrame
from gripper_ai_controller.pose.estimator import PoseEstimator
from gripper_ai_controller.pose.models import COCO_KEYPOINT_NAMES, PoseCandidate
from gripper_ai_controller.vision.analysis import JointVisibilityEvaluator
from gripper_ai_controller.vision.models import FrameQualityDiagnostics, JointVisibility
from gripper_ai_controller.vision.quality import (
    FRAME_QUALITY_WARNING_CODES,
    FrameQualityInspector,
)


class VisionFixtureManifestError(ValueError):
    """Report invalid, missing, or unlicensed offline evaluation fixture metadata."""


@dataclass(frozen=True)
class VisionFixture:
    """One redistributable static image and its intentionally tolerant expected outcomes."""

    fixture_id: str
    image_path: Path
    source_url: str
    author: str
    license_name: str
    sha256: str
    minimum_person_count: int
    maximum_person_count: int
    minimum_visible_joints: int
    required_visible_joints: Tuple[str, ...]
    required_quality_warnings: Tuple[str, ...]
    manual_review: str


@dataclass(frozen=True)
class VisionFixtureEvaluation:
    """One model evaluation result without raw image or tensor data in the report.

    Expected quality warnings are copied from the manifest because report generation
    runs after the evaluation loop and must not retain or dereference source fixtures.
    """

    fixture_id: str
    pixel_format: str
    quality: FrameQualityDiagnostics
    person_count: int
    visible_joints: Tuple[JointVisibility, ...]
    passed: bool
    failures: Tuple[str, ...]
    required_quality_warnings: Tuple[str, ...]
    candidates: Tuple[PoseCandidate, ...]


def load_vision_fixtures(manifest_path: str) -> Tuple[VisionFixture, ...]:
    """Load a JSON manifest and require committed fixtures to have a safe redistributable license."""

    source = Path(manifest_path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise VisionFixtureManifestError("The vision fixture manifest could not be read.") from error
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise VisionFixtureManifestError("The vision fixture manifest must be a version 1 JSON object.")
    raw_fixtures = payload.get("fixtures")
    if not isinstance(raw_fixtures, list) or not raw_fixtures:
        raise VisionFixtureManifestError("The vision fixture manifest must contain at least one fixture.")
    fixtures = []
    fixture_ids = set()
    for raw_fixture in raw_fixtures:
        fixture = _parse_fixture(raw_fixture, source.parent)
        if fixture.fixture_id in fixture_ids:
            raise VisionFixtureManifestError("Vision fixture identifiers must be unique.")
        fixture_ids.add(fixture.fixture_id)
        fixtures.append(fixture)
    return tuple(fixtures)


def verify_vision_fixture(fixture: VisionFixture) -> None:
    """Verify the committed image still matches its manifest checksum before model execution."""

    if not fixture.image_path.is_file():
        raise VisionFixtureManifestError(
            "The fixture image '{0}' is missing.".format(fixture.image_path.as_posix())
        )
    digest = hashlib.sha256()
    try:
        with fixture.image_path.open("rb") as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
    except OSError as error:
        raise VisionFixtureManifestError("The fixture image could not be verified.") from error
    if digest.hexdigest() != fixture.sha256:
        raise VisionFixtureManifestError(
            "The fixture image '{0}' does not match the manifest SHA-256.".format(fixture.fixture_id)
        )


def load_fixture_frame(fixture: VisionFixture, pixel_format: str) -> ImageFrame:
    """Load one verified fixture as RGB8 or deterministic Mono8 without camera hardware."""

    verify_vision_fixture(fixture)
    try:
        with Image.open(str(fixture.image_path)) as source:
            if pixel_format == "rgb8":
                image = source.convert("RGB")
                payload = image.tobytes()
            elif pixel_format == "mono8":
                image = source.convert("L")
                payload = image.tobytes()
            else:
                raise VisionFixtureManifestError("Fixture pixel format must be rgb8 or mono8.")
            width, height = image.size
    except (OSError, ValueError) as error:
        if isinstance(error, VisionFixtureManifestError):
            raise
        raise VisionFixtureManifestError("The fixture image could not be decoded.") from error
    return ImageFrame(
        "fixture-{0}".format(fixture.fixture_id),
        0.0,
        "fixture://{0}".format(fixture.fixture_id),
        None,
        True,
        payload,
        width,
        height,
        pixel_format,
    )


def evaluate_fixture(
    fixture: VisionFixture,
    pixel_format: str,
    estimator: PoseEstimator,
    quality_inspector: FrameQualityInspector,
    visibility_evaluator: JointVisibilityEvaluator,
    person_confidence_threshold: float,
) -> VisionFixtureEvaluation:
    """Evaluate one fixture in one input format with tolerant semantic acceptance criteria."""

    frame = load_fixture_frame(fixture, pixel_format)
    quality = quality_inspector.inspect(frame)
    candidates = tuple(
        candidate
        for candidate in estimator.infer(frame)
        if candidate.confidence >= person_confidence_threshold
    )
    selected = _highest_confidence_candidate(candidates)
    visibility = ()
    if selected is not None:
        visibility = visibility_evaluator.evaluate(selected.joints, frame.width, frame.height)
    visible_names = {joint.name for joint in visibility if joint.state == "detected"}
    failures = []
    if len(candidates) < fixture.minimum_person_count:
        failures.append("person_count_below_minimum")
    if len(candidates) > fixture.maximum_person_count:
        failures.append("person_count_above_maximum")
    if len(visible_names) < fixture.minimum_visible_joints:
        failures.append("visible_joint_count_below_minimum")
    for joint_name in fixture.required_visible_joints:
        if joint_name not in visible_names:
            failures.append("required_joint_missing:{0}".format(joint_name))
    for warning in fixture.required_quality_warnings:
        if warning not in quality.warnings:
            failures.append("required_quality_warning_missing:{0}".format(warning))
    return VisionFixtureEvaluation(
        fixture.fixture_id,
        pixel_format,
        quality,
        len(candidates),
        visibility,
        not failures,
        tuple(failures),
        fixture.required_quality_warnings,
        candidates,
    )


def evaluation_report(evaluations: Iterable[VisionFixtureEvaluation]) -> Dict[str, object]:
    """Build a JSON-safe summary that intentionally omits image bytes and model tensors."""

    results = []
    for evaluation in evaluations:
        results.append(
            {
                "fixture_id": evaluation.fixture_id,
                "pixel_format": evaluation.pixel_format,
                "passed": evaluation.passed,
                "failures": list(evaluation.failures),
                "person_count": evaluation.person_count,
                "visible_joint_names": [
                    joint.name for joint in evaluation.visible_joints if joint.state == "detected"
                ],
                "required_quality_warnings": list(evaluation.required_quality_warnings),
                "joint_visibility": [
                    {
                        "name": joint.name,
                        "state": joint.state,
                        "confidence": joint.confidence,
                    }
                    for joint in evaluation.visible_joints
                ],
                "quality": {
                    "valid": evaluation.quality.valid,
                    "width": evaluation.quality.width,
                    "height": evaluation.quality.height,
                    "pixel_format": evaluation.quality.pixel_format,
                    "brightness_mean": evaluation.quality.brightness_mean,
                    "contrast": evaluation.quality.contrast,
                    "sharpness": evaluation.quality.sharpness,
                    "warnings": list(evaluation.quality.warnings),
                },
            }
        )
    return {"fixtures": results, "passed": all(item["passed"] for item in results)}


def render_fixture_overlay(
    fixture: VisionFixture,
    evaluation: VisionFixtureEvaluation,
    destination: str,
) -> None:
    """Render an optional human-review overlay to an explicit temporary output path."""

    verify_vision_fixture(fixture)
    try:
        with Image.open(str(fixture.image_path)) as source:
            image = source.convert("L").convert("RGB") if evaluation.pixel_format == "mono8" else source.convert("RGB")
    except (OSError, ValueError) as error:
        raise VisionFixtureManifestError("The fixture overlay source could not be decoded.") from error
    draw = ImageDraw.Draw(image)
    width, height = image.size
    selected = _highest_confidence_candidate(evaluation.candidates)
    for candidate in evaluation.candidates:
        left = candidate.bounding_box.x * width
        top = candidate.bounding_box.y * height
        right = (candidate.bounding_box.x + candidate.bounding_box.width) * width
        bottom = (candidate.bounding_box.y + candidate.bounding_box.height) * height
        color = "#f59e0b" if candidate == selected else "#38bdf8"
        draw.rectangle((left, top, right, bottom), outline=color, width=3)
    for joint in evaluation.visible_joints:
        color = {"detected": "#22c55e", "low_confidence": "#f59e0b", "out_of_frame": "#ef4444"}.get(
            joint.state,
            "#94a3b8",
        )
        x = joint.normalized_x * width
        y = joint.normalized_y * height
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=color)
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(output), format="PNG")


def _parse_fixture(raw_fixture: object, manifest_directory: Path) -> VisionFixture:
    """Validate one fixture entry before a local image path can be accessed."""

    if not isinstance(raw_fixture, dict):
        raise VisionFixtureManifestError("Every vision fixture must be a JSON object.")
    fixture_id = _required_string(raw_fixture, "id")
    filename = _required_string(raw_fixture, "file")
    relative_path = Path(filename)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise VisionFixtureManifestError("Fixture image paths must be relative without parent traversal.")
    source_url = _required_string(raw_fixture, "source_url")
    if not source_url.startswith("https://"):
        raise VisionFixtureManifestError("Fixture source_url values must use HTTPS.")
    license_name = _required_string(raw_fixture, "license")
    if license_name not in ("CC0-1.0", "Public Domain"):
        raise VisionFixtureManifestError("Committed fixture images must be CC0-1.0 or Public Domain.")
    sha256 = _required_string(raw_fixture, "sha256")
    if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
        raise VisionFixtureManifestError("Fixture SHA-256 values must be lowercase hexadecimal strings.")
    required_visible = raw_fixture.get("required_visible_joints", [])
    if not isinstance(required_visible, list) or any(
        not isinstance(value, str) or value not in COCO_KEYPOINT_NAMES for value in required_visible
    ):
        raise VisionFixtureManifestError(
            "required_visible_joints must contain only supported COCO joint names."
        )
    required_warnings = raw_fixture.get("required_quality_warnings", [])
    if not isinstance(required_warnings, list) or any(
        not isinstance(value, str) or value not in FRAME_QUALITY_WARNING_CODES
        for value in required_warnings
    ):
        raise VisionFixtureManifestError(
            "required_quality_warnings must contain only supported frame-quality warnings."
        )
    minimum_people = _integer(raw_fixture, "minimum_person_count", 0, 20)
    maximum_people = _integer(raw_fixture, "maximum_person_count", minimum_people, 20)
    if maximum_people < minimum_people:
        raise VisionFixtureManifestError("maximum_person_count must be at least minimum_person_count.")
    return VisionFixture(
        fixture_id,
        manifest_directory / relative_path,
        source_url,
        _required_string(raw_fixture, "author"),
        license_name,
        sha256,
        minimum_people,
        maximum_people,
        _integer(raw_fixture, "minimum_visible_joints", 0, 17),
        tuple(required_visible),
        tuple(required_warnings),
        _required_string(raw_fixture, "manual_review"),
    )


def _required_string(payload: Dict[str, object], key: str) -> str:
    """Read one non-empty manifest string without coercing arbitrary JSON values."""

    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise VisionFixtureManifestError("Fixture field '{0}' must be a non-empty string.".format(key))
    return value


def _integer(payload: Dict[str, object], key: str, minimum: int, maximum: int) -> int:
    """Read one bounded integer while rejecting booleans and float coercion."""

    value = payload.get(key)
    if type(value) is not int or value < minimum or value > maximum:
        raise VisionFixtureManifestError(
            "Fixture field '{0}' must be an integer from {1} to {2}.".format(key, minimum, maximum)
        )
    return value


def _highest_confidence_candidate(candidates: Sequence[PoseCandidate]) -> Optional[PoseCandidate]:
    """Select the same highest-confidence person policy used by the live preview tracker."""

    selected = None
    for candidate in candidates:
        if selected is None or candidate.confidence > selected.confidence:
            selected = candidate
    return selected
