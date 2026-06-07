"""Shared constants and helpers for organization sections."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tagflow import tag, text

if TYPE_CHECKING:
    from polar.models import Organization


def render_checklist_row(label: str, is_set: bool, value: str | None) -> None:
    """Render a single checklist row with a green/red status dot."""
    with tag.div(
        classes="flex items-center justify-between py-2 border-b border-base-200"
    ):
        with tag.div(classes="flex items-center gap-2"):
            dot_class = "bg-success" if is_set else "bg-error"
            with tag.span(classes=f"w-2.5 h-2.5 rounded-full {dot_class} inline-block"):
                pass
            with tag.span(classes="text-sm font-medium"):
                text(label)
        with tag.span(
            classes="text-sm" + (" text-base-content/60" if not is_set else "")
        ):
            text((value or "Set") if is_set else "Missing")


class ChecklistMixin:
    """Mixin providing checklist properties for sections that have self.org."""

    org: Organization

    @property
    def has_email(self) -> bool:
        return bool(self.org.email)

    @property
    def has_website(self) -> bool:
        return bool(self.org.website)

    @property
    def has_socials(self) -> bool:
        return bool(self.org.socials and len(self.org.socials) >= 1)

    @property
    def missing_items(self) -> list[str]:
        items = []
        if not self.has_email:
            items.append("Add a support email in your organization settings")
        if not self.has_website:
            items.append("Add your website URL in your organization settings")
        if not self.has_socials:
            items.append(
                "Add at least one social media link in your organization settings"
            )
        return items
