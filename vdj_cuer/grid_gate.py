"""Refuse AutoCue writes until the user 1 on disk is the one we will snap to."""

from __future__ import annotations

from typing import Any, Mapping, Optional


class UnsettledGridError(RuntimeError):
    """Onset/preflight says the downbeat is not the saved user 1."""


def user_one_is_settled(
    preflight: Optional[Mapping[str, Any]],
    *,
    confirmed: bool = False,
) -> bool:
    """True when AutoCue may write cues on this FilePath's Scan Phase / 1.

    Confirmed ("Grid is correct") means the user already signed off the disk 1.
    Deep onset ``corrected`` or ``needs_align`` from that check means kick
    wants a different 1 — do not write until Align or confirm.
    """
    if confirmed:
        return True
    if not preflight:
        return True
    alignment = preflight.get("alignment") or {}
    if alignment.get("corrected"):
        return False
    # Deep-onset "needs align" (not just Phase≠POI structural warn).
    if preflight.get("needs_align") and alignment:
        return False
    return True


def assert_user_one_settled(
    preflight: Optional[Mapping[str, Any]],
    *,
    confirmed: bool = False,
) -> None:
    if user_one_is_settled(preflight, confirmed=confirmed):
        return
    raise UnsettledGridError(
        "Refusing cue write: the user 1 is not settled on disk "
        "(onset wants a different 1). Align or confirm Grid is correct first. "
        "No AutoCue."
    )
