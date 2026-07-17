"""Contract helpers and registry for custom-route settings handlers.

This module defines a small contract table for special-case setting routes.
Each custom route in the schema should appear here, either as:

- a factory-backed route, where a helper module registers a callable; or
- an inline route, where the flow owns the handler directly.

That makes the special-case surface explicit and testable.
"""
from dataclasses import dataclass
from typing import Callable, Dict, Optional


@dataclass(frozen=True)
class CustomRouteContract:
    route_id: str
    kind: str
    owner: str
    summary: str = ""
    factory: Optional[Callable] = None


_REGISTRY: Dict[str, CustomRouteContract] = {}


def register_custom_route(route_id: str):
    """Decorator to register a factory/handler for a custom route id.

    Usage:
        @register_custom_route("logging_modules")
        def build_logging_modules_state_helpers(...):
            ...

    Returns the original function unchanged.
    """

    def _decorator(fn: Callable) -> Callable:
        _REGISTRY[route_id] = CustomRouteContract(
            route_id=route_id,
            kind="factory",
            owner=fn.__module__,
            summary=fn.__doc__ or "",
            factory=fn,
        )
        return fn

    return _decorator


def register_inline_custom_route(route_id: str, *, owner: str, summary: str = "") -> CustomRouteContract:
    """Register an inline custom route handled directly in the flow."""
    contract = CustomRouteContract(route_id=route_id, kind="inline", owner=owner, summary=summary)
    _REGISTRY[route_id] = contract
    return contract


def get_custom_route(route_id: str) -> Optional[Callable]:
    """Return the registered factory for `route_id`, or None if absent."""
    contract = _REGISTRY.get(route_id)
    return contract.factory if contract else None


def get_custom_route_contract(route_id: str) -> Optional[CustomRouteContract]:
    """Return the full contract metadata for `route_id`, if registered."""
    return _REGISTRY.get(route_id)


def clear_registry() -> None:
    """Clear the registry (helpful for tests)."""
    _REGISTRY.clear()


def list_registered() -> Dict[str, CustomRouteContract]:
    """Return a shallow copy of the registry."""
    return dict(_REGISTRY)


def ensure_routes_registered(route_ids: list[str]) -> list[str]:
    """Return any route ids that are missing from the registry."""
    return [route_id for route_id in route_ids if route_id not in _REGISTRY]


def validate_schema_routes_registered(schema_custom_routes: list[str]) -> None:
    """Validate that all schema custom-route IDs are registered.

    Raises:
        RuntimeError: If any schema route is not registered in the contract table.
    """
    missing = ensure_routes_registered(schema_custom_routes)
    if missing:
        raise RuntimeError(
            "Custom route validation failed: "
            f"{len(missing)} schema routes not registered in contract table: {missing}. "
            f"Registered routes: {list(_REGISTRY.keys())}"
        )


__all__ = [
    "CustomRouteContract",
    "register_custom_route",
    "register_inline_custom_route",
    "get_custom_route",
    "get_custom_route_contract",
    "clear_registry",
    "list_registered",
    "ensure_routes_registered",
    "validate_schema_routes_registered",
]
