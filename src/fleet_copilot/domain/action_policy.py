"""What evidence each action type has to rest on.

Shared by two callers with different jobs: the action service *enforces* this as
a hard gate, and the review assessment *reports* against it so an approver can
see at a glance whether a proposal rests on a reading that speaks directly to
the action, or merely on something true about the same device.

Kept in ``domain`` so neither of those has to import the other.
"""
from __future__ import annotations

from .enums import ActionType

# Evidence field fragments that directly justify each action type.
#
# Checking only that evidence exists and names the right device is not enough:
# every device has a model name and an owner, so "flag the whole fleet for
# replacement" could cite a real record per device and pass.
#
# ``notify_employee`` is deliberately absent: telling someone about any reading
# on their own device is reasonable, and it changes nothing on the fleet.
EVIDENCE_MUST_MENTION: dict[ActionType, tuple[str, ...]] = {
    ActionType.FLAG_DEVICE_FOR_REPLACEMENT: (
        "battery.condition",
        "battery.cycle_count",
        "battery.full_charge_capacity",
    ),
    ActionType.CREATE_UPGRADE_ORDER: (
        "ram_used_pct",
        "disk_free_pct",
        "battery.",
    ),
    ActionType.OPEN_REMEDIATION_TICKET: (
        "compliance.",
        "disk_free_pct",
        "ram_used_pct",
    ),
}

WHAT_IT_NEEDS: dict[ActionType, str] = {
    ActionType.FLAG_DEVICE_FOR_REPLACEMENT: (
        "a reading showing the hardware is at end of life (battery condition, "
        "cycle count or capacity decline). A device that is merely full or "
        "non-compliant should get a ticket, not a replacement."
    ),
    ActionType.CREATE_UPGRADE_ORDER: (
        "a reading showing the hardware is insufficient for its workload "
        "(sustained memory pressure, or storage that keeps running out)."
    ),
    ActionType.OPEN_REMEDIATION_TICKET: (
        "a reading showing the condition to remediate (a failing compliance "
        "check, or a device low on storage)."
    ),
}


def supports_directly(action_type: ActionType, fields: list[str]) -> bool:
    """True when at least one cited field speaks to this action specifically."""
    wanted = EVIDENCE_MUST_MENTION.get(action_type)
    if not wanted:
        # No constraint declared, so any evidence about the device is on point.
        return bool(fields)
    return any(any(w in field for w in wanted) for field in fields)
