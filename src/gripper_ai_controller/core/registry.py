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

    def replace(self, component: object, instance_name: str) -> None:
        """Replace one existing component while preserving its registry position.

        A selective development reload needs to make a newly started component
        visible atomically without rebuilding unrelated registry entries. The
        replacement remains deliberately strict so a typo cannot create a second
        hidden component under an unexpected name.
        """

        manifest = getattr(component, "manifest", None)
        if not isinstance(manifest, ComponentManifest):
            raise TypeError("Registered components must expose a ComponentManifest.")
        if instance_name not in self.components:
            raise KeyError("Unknown component name: {0}".format(instance_name))
        self.components[instance_name] = component

    def unregister(self, instance_name: str) -> None:
        """Remove one component that has completed its owned lifecycle."""

        if instance_name not in self.components:
            raise KeyError("Unknown component name: {0}".format(instance_name))
        del self.components[instance_name]
