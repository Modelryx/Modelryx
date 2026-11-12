from collections.abc import Iterable

from modelryx.models import User, UserRole

ASSIGNABLE_ROLES = frozenset({"reader", "designer", "reviewer", "administrator"})


def normalize_roles(values: Iterable[str]) -> frozenset[str]:
    roles = frozenset("designer" if value == "engineer" else value for value in values)
    if not roles or not roles <= ASSIGNABLE_ROLES:
        raise ValueError("At least one valid role is required")
    return roles


def roles_for_user(user: User) -> frozenset[str]:
    assigned = frozenset(value.role for value in user.role_assignments)
    if assigned:
        return assigned
    # Compatibility for fixtures and databases before migration 0069 is applied.
    return normalize_roles([user.role])


def effective_role(roles: Iterable[str]) -> str:
    values = normalize_roles(roles)
    if "administrator" in values:
        return "administrator"
    if "reviewer" in values:
        return "reviewer"
    if "designer" in values:
        return "engineer"
    return "reader"


def assign_roles(user: User, values: Iterable[str]) -> frozenset[str]:
    roles = normalize_roles(values)
    user.role_assignments[:] = [UserRole(role=value) for value in sorted(roles)]
    user.role = effective_role(roles)
    user.auth_version = (user.auth_version or 0) + 1
    return roles


def user_has_role(user: User, *roles: str) -> bool:
    wanted = normalize_roles(roles)
    return bool(roles_for_user(user) & wanted)
