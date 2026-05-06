"""Controlled tools for assistant identity and core profile updates."""


def assistant_identity_update(
    identity_store,
    *,
    name: str | None = None,
    backstory: str | None = None,
    style: str | None = None,
    principles: list[str] | None = None,
) -> str:
    _require_dependency(identity_store, "IdentityStore")
    current = identity_store.get_identity()
    merged = {
        "name": name if name is not None else current.get("name", ""),
        "backstory": backstory if backstory is not None else current.get("backstory", ""),
        "style": style if style is not None else current.get("style", ""),
        "principles": principles if principles is not None else current.get("principles", []),
    }
    if not any(value is not None for value in (name, backstory, style, principles)):
        raise ValueError("At least one field must be provided")
    if not merged["name"].strip():
        raise ValueError("Assistant identity requires a non-empty name")
    if not merged["backstory"].strip():
        raise ValueError("Assistant identity requires a non-empty backstory")
    if not merged["style"].strip():
        raise ValueError("Assistant identity requires a non-empty style")
    identity_store.replace_identity(**merged)
    return "ok"


def profile_core_update(
    profile_store,
    *,
    display_name: str | None = None,
    core_facts: list[str] | None = None,
) -> str:
    _require_dependency(profile_store, "ProfileStore")
    current = profile_store.get_profile()
    if display_name is None:
        merged_display_name = current.get("display_name")
    else:
        merged_display_name = display_name.strip() or None
    merged_core_facts = core_facts if core_facts is not None else current.get("core_facts", [])
    if display_name is None and core_facts is None:
        raise ValueError("At least one field must be provided")
    profile_store.replace_profile(
        display_name=merged_display_name,
        core_facts=merged_core_facts,
    )
    return "ok"


def _require_dependency(dependency, name: str) -> None:
    if dependency is None:
        raise RuntimeError(f"{name} dependency is not configured")
