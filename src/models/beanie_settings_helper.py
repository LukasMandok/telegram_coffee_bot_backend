from __future__ import annotations

from typing import Any, Callable, Optional

from pydantic import Field

from ..bot.settings_schema import Setting, SettingType, get_setting


def require_setting(category_key: str, setting_key: str) -> Setting:
    setting = get_setting(category_key, setting_key)
    if setting is None:
        raise ValueError(f"Missing schema setting: {category_key}.{setting_key}")
    return setting


def field_from_setting(setting: Setting, *, default_override: Any = None) -> Any:
    """Build a Pydantic Field from a schema setting definition."""
    default_value = setting.default if default_override is None else default_override
    field_kwargs = {"default": default_value, "description": setting.description}

    if setting.type == SettingType.NUMBER:
        if setting.min_value is not None:
            field_kwargs["ge"] = setting.min_value
        if setting.max_value is not None:
            field_kwargs["le"] = setting.max_value

    return Field(**field_kwargs)


def field_from_schema(
    setting_or_category: Setting | str,
    setting_key: Optional[str] = None,
    *,
    default_override: Any = None,
) -> Any:
    """Resolve a schema setting and build a Pydantic Field for it.

    Accepts either a Setting object directly or a category key plus setting key.
    """
    if isinstance(setting_or_category, Setting):
        return field_from_setting(setting_or_category, default_override=default_override)

    if setting_key is None:
        raise ValueError("setting_key is required when resolving a field by category key")

    return field_from_setting(
        require_setting(setting_or_category, setting_key),
        default_override=default_override,
    )


def validator_from_setting(
    setting: Setting,
    field_name: str,
    *,
    normalize: Optional[Callable[[Any], Any]] = None,
) -> Callable[[Any], Any]:
    """Build a reusable validator function from a schema setting definition."""
    if setting.type == SettingType.ENUM:
        if not setting.options:
            raise ValueError(f"Missing options for enum setting: {setting.key}")
        return make_choice_validator(setting.options, field_name, normalize=normalize)

    if setting.type == SettingType.NUMBER:
        return make_range_validator(setting.min_value, setting.max_value, field_name)

    if setting.type == SettingType.TOGGLE:
        def _validator(value: Any) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"true", "1", "yes", "on"}:
                    return True
                if normalized in {"false", "0", "no", "off"}:
                    return False
            raise ValueError(f"{field_name} must be a boolean")

        return _validator

    def _identity(value: Any) -> Any:
        return normalize(value) if normalize is not None else value

    return _identity


def make_choice_validator(
    allowed_values: list[str],
    field_name: str,
    *,
    normalize: Optional[Callable[[Any], Any]] = None,
) -> Callable[[Any], Any]:
    """Create a reusable value validator for enumerated settings."""

    def _validator(value: Any) -> Any:
        new_value = normalize(value) if normalize is not None else value
        if new_value not in allowed_values:
            raise ValueError(f"{field_name} must be one of {allowed_values}")
        return new_value

    return _validator


def make_range_validator(
    min_value: Optional[int],
    max_value: Optional[int],
    field_name: str,
) -> Callable[[Any], int]:
    """Create a reusable integer range validator for numeric settings."""

    def _validator(value: Any) -> int:
        try:
            parsed = int(value)
        except Exception as exc:
            raise ValueError(f"{field_name} must be an integer") from exc

        if min_value is not None and parsed < min_value:
            raise ValueError(f"{field_name} must be >= {min_value}")
        if max_value is not None and parsed > max_value:
            raise ValueError(f"{field_name} must be <= {max_value}")
        return parsed

    return _validator