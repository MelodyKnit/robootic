"""Small registries for project-local adapters and plugins."""

from typing import Dict, Iterable

from gripper_ai_controller.domain.models import ComponentManifest


class ComponentRegistry:
    """Registers independently updateable project-local components by manifest name."""

    def __init__(self) -> None:
        """Create an insertion-ordered registry scoped to one runtime lifecycle."""

        self.components = {}  # type: Dict[str, object]

    def register(self, component: object, instance_name: str = None) -> None:
        """Register a component with a manifest and reject duplicate names."""

        manifest = getattr(component, "manifest", None)
        if not isinstance(manifest, ComponentManifest):
            raise TypeError("Registered components must expose a ComponentManifest.")
        key = manifest.name if instance_name is None else instance_name
        if key in self.components:
            raise ValueError("Duplicate component name: {0}".format(key))
        self.components[key] = component

    def all(self) -> Iterable[object]:
        """Return registered components in insertion order."""

        return self.components.values()
