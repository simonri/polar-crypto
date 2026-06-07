"""Overview section with payment, setup/checklist and profile cards."""

import contextlib
from collections.abc import Generator

from fastapi import Request
from tagflow import tag, text

from polar.models import Organization

from ....components import card
from ....components._metric_card import Variant
from ._shared import (
    ChecklistMixin,
    render_checklist_row,
)


class OverviewSection(ChecklistMixin):
    """Render the overview section."""

    def __init__(
        self,
        organization: Organization,
        orders_count: int = 0,
        unrefunded_orders_count: int = 0,
    ) -> None:
        self.org = organization
        self.orders_count = orders_count
        self.unrefunded_orders_count = unrefunded_orders_count

    # ------------------------------------------------------------------
    # Payment Metrics card
    # ------------------------------------------------------------------

    @staticmethod
    def _to_variant(value: str) -> Variant:
        """Map threshold evaluation result to UI variant."""
        _map: dict[str, Variant] = {"ok": "default", "warn": "warning", "crit": "error"}
        return _map.get(value, "default")

    @contextlib.contextmanager
    def payment_card(
        self, payment_stats: dict[str, int | float] | None = None
    ) -> Generator[None]:
        """Render payment statistics card with health-rate metrics."""

        with card(bordered=True):
            with tag.div(classes="flex items-center justify-between mb-4"):
                with tag.h2(classes="text-lg font-bold"):
                    text("Payment Metrics")

            if not payment_stats:
                with tag.p(classes="text-base-content/60"):
                    text("No payment data available.")
            else:
                with tag.div(classes="space-y-0"):
                    # Summary line items
                    self._payment_line(
                        "Total Payments",
                        str(payment_stats.get("payment_count", 0)),
                    )
                    self._payment_line(
                        "Total Amount",
                        f"${payment_stats.get('total_amount', 0) / 100:,.2f}",
                    )
                    self._payment_line(
                        "Total Net Amount",
                        f"${payment_stats.get('total_net_amount', 0) / 100:,.2f}",
                    )
                    self._payment_line(
                        "Current Balance",
                        f"${payment_stats.get('account_balance', 0) / 100:,.2f}",
                    )

                    # Divider before rate metrics
                    tag.div(classes="border-b border-base-300 my-1")

                    # Rate metrics
                    auth_rate = payment_stats.get("auth_rate", 100)
                    failed_count = payment_stats.get("failed_count", 0)
                    self._payment_line(
                        "Auth Rate",
                        f"{auth_rate:.1f}%",
                        detail=f"{failed_count} failed",
                    )

                    refund_rate = payment_stats.get("refund_rate", 0)
                    self._payment_line(
                        "Refund Rate",
                        f"{refund_rate:.1f}%",
                        detail=f"${payment_stats.get('refunds_amount', 0) / 100:,.2f}",
                    )

                    dispute_rate = payment_stats.get("dispute_rate", 0)
                    self._payment_line(
                        "Dispute Rate",
                        f"{dispute_rate:.2f}%",
                        detail=f"{payment_stats.get('dispute_count', 0)} · ${payment_stats.get('dispute_amount', 0) / 100:,.2f}",
                    )

                    chargeback_rate = payment_stats.get("chargeback_rate", 0)
                    self._payment_line(
                        "Chargeback Rate",
                        f"{chargeback_rate:.2f}%",
                        detail=f"{payment_stats.get('chargeback_count', 0)} lost · ${payment_stats.get('chargeback_amount', 0) / 100:,.2f}",
                    )

                    # Risk scores
                    risk_scores_count = payment_stats.get("risk_scores_count", 0)
                    if risk_scores_count > 0:
                        tag.div(classes="border-b border-base-300 my-1")

                        p50_risk = payment_stats.get("p50_risk", 0)
                        p90_risk = payment_stats.get("p90_risk", 0)
                        self._payment_line(
                            "P50 Risk",
                            f"{p50_risk:.0f}",
                            detail=f"median of {risk_scores_count}",
                        )
                        self._payment_line(
                            "P90 Risk",
                            f"{p90_risk:.0f}",
                            detail="90th pctl",
                        )

            yield

    @staticmethod
    def _payment_line(
        label: str,
        value: str,
        *,
        variant: Variant = "default",
        detail: str | None = None,
    ) -> None:
        """Render a single compact metric line with background tint for alerts."""
        row_bg: dict[Variant, str] = {
            "default": "",
            "success": "",
            "warning": "bg-warning/5",
            "error": "bg-error/10",
            "info": "",
        }

        with tag.div(
            classes=f"flex items-center justify-between py-1.5 px-1 rounded {row_bg[variant]}"
        ):
            # Left: label + detail
            with tag.div(classes="flex items-center gap-2 min-w-0"):
                with tag.span(classes="text-sm truncate"):
                    text(label)
                if detail:
                    with tag.span(classes="text-xs text-base-content/50 truncate"):
                        text(detail)

            # Right: value
            weight = "font-bold" if variant != "default" else "font-semibold"
            with tag.span(classes=f"font-mono text-sm {weight}"):
                text(value)

    # ------------------------------------------------------------------
    # Setup & Checklist card
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def setup_checklist_card(
        self, setup_data: dict[str, int | bool] | None = None
    ) -> Generator[None]:
        """Merged setup status + account checklist + reply template."""
        with card(bordered=True):
            with tag.h2(classes="text-lg font-bold mb-4"):
                text("Setup & Checklist")

            # --- Setup Status section ---
            if not setup_data:
                with tag.p(classes="text-base-content/60 mb-4"):
                    text("Setup metrics not available.")
            else:
                with tag.div(classes="space-y-2"):
                    payment_ready = setup_data.get("payment_ready", False)

                    metrics = [
                        ("Payment Ready", "Yes" if payment_ready else "No"),
                        ("Checkout Links", setup_data.get("checkout_links_count", 0)),
                        ("Webhooks", setup_data.get("webhooks_count", 0)),
                        ("API Keys", setup_data.get("api_keys_count", 0)),
                        ("Products", setup_data.get("products_count", 0)),
                    ]

                    for label, value in metrics:
                        with tag.div(
                            classes="flex items-center justify-between py-1.5 border-b border-base-200"
                        ):
                            with tag.span(classes="text-sm"):
                                text(label)
                            with tag.span(classes="font-mono text-sm font-semibold"):
                                text(str(value))

            # --- Checklist section ---
            self._render_checklist()

            yield

    # ------------------------------------------------------------------
    # Organization Profile card
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def organization_profile_card(self) -> Generator[None]:
        """Read-only org profile: website, details, social links."""
        with card(bordered=True):
            with tag.h2(classes="text-lg font-bold mb-4"):
                text("Organization Profile")

            has_content = False

            # Website
            if self.org.website:
                has_content = True
                with tag.div(classes="mb-4"):
                    with tag.div(classes="text-sm font-semibold mb-2"):
                        text("Website")
                    with tag.div(classes="text-sm text-base-content/80"):
                        with tag.a(
                            href=str(self.org.website),
                            target="_blank",
                            rel="noopener noreferrer",
                            classes="link link-primary",
                        ):
                            text(str(self.org.website))

            # Details: about, product description, intended use
            if hasattr(self.org, "details") and self.org.details:
                details = self.org.details

                if details.get("about"):
                    has_content = True
                    with tag.div(classes="mb-4"):
                        with tag.div(classes="text-sm font-semibold mb-2"):
                            text("About")
                        with tag.div(
                            classes="text-sm text-base-content/80 whitespace-pre-wrap"
                        ):
                            text(details["about"])

                if details.get("product_description"):
                    has_content = True
                    with tag.div(classes="mb-4"):
                        with tag.div(classes="text-sm font-semibold mb-2"):
                            text("Product Description")
                        with tag.div(
                            classes="text-sm text-base-content/80 whitespace-pre-wrap"
                        ):
                            text(details["product_description"])

                if details.get("intended_use"):
                    has_content = True
                    with tag.div(classes="mb-4"):
                        with tag.div(classes="text-sm font-semibold mb-2"):
                            text("Intended Use")
                        with tag.div(
                            classes="text-sm text-base-content/80 whitespace-pre-wrap"
                        ):
                            text(details["intended_use"])

            # Social media links
            socials = self.org.socials or []
            if socials:
                has_content = True
                with tag.div(
                    classes="pt-4 mt-4 border-t border-base-200" if has_content else ""
                ):
                    with tag.div(classes="text-sm font-semibold mb-3"):
                        text("Social Media Links")
                    with tag.div(classes="space-y-2"):
                        for social in socials:
                            platform = social.get("platform", "").title()
                            url = social.get("url", "")
                            if platform and url:
                                with tag.div(
                                    classes="flex items-center justify-between py-1.5"
                                ):
                                    with tag.span(
                                        classes="text-sm font-medium capitalize"
                                    ):
                                        text(platform)
                                    with tag.a(
                                        href=url,
                                        target="_blank",
                                        rel="noopener noreferrer",
                                        classes="text-sm link link-primary truncate max-w-xs",
                                    ):
                                        text(url)

            if not has_content and not socials:
                with tag.p(classes="text-sm text-base-content/60"):
                    text("No profile information available.")

            yield

    def _render_checklist(self) -> None:
        """Render the account checklist rows."""
        with tag.div(classes="pt-4 mt-4 border-t border-base-200"):
            with tag.h3(classes="text-sm font-bold mb-3"):
                text("Account Checklist")

            with tag.div(classes="space-y-3"):
                render_checklist_row(
                    "Support Email",
                    self.has_email,
                    self.org.email if self.has_email else None,
                )

                render_checklist_row(
                    "Website URL",
                    self.has_website,
                    self.org.website if self.has_website else None,
                )

                if self.has_socials:
                    social_count = len(self.org.socials)
                    render_checklist_row(
                        "Social Media",
                        True,
                        f"{social_count} link{'s' if social_count != 1 else ''}",
                    )
                else:
                    render_checklist_row("Social Media", False, None)

                # Test Sales — dot color based on unrefunded orders
                if self.unrefunded_orders_count == 0:
                    dot_class = "bg-success"
                elif self.unrefunded_orders_count == 1:
                    dot_class = "bg-warning"
                else:
                    dot_class = "bg-error"

                with tag.div(
                    classes="flex items-center justify-between py-2 border-b border-base-200"
                ):
                    with tag.div(classes="flex items-center gap-2"):
                        with tag.span(
                            classes=f"w-2.5 h-2.5 rounded-full {dot_class} inline-block"
                        ):
                            pass
                        with tag.span(classes="text-sm font-medium"):
                            text("Test Sales")
                    with tag.span(classes="text-sm"):
                        text(
                            f"{self.orders_count} order{'s' if self.orders_count != 1 else ''}"
                        )

                if self.unrefunded_orders_count > 0:
                    with tag.div(
                        classes="mt-2 p-3 bg-warning/10 border border-warning/30 rounded text-sm"
                    ):
                        text(
                            f"{self.unrefunded_orders_count} unrefunded order{'s' if self.unrefunded_orders_count != 1 else ''} — should be auto-refunded before approval."
                        )

    # ------------------------------------------------------------------
    # Main render
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def render(
        self,
        request: Request,
        setup_data: dict[str, int | bool] | None = None,
        payment_stats: dict[str, int | float] | None = None,
    ) -> Generator[None]:
        """Render the overview section."""

        with tag.div(classes="flex flex-col lg:flex-row gap-6"):
            # Left: Payment Metrics
            with tag.div(classes="lg:w-3/5 min-w-0"):
                with self.payment_card(payment_stats):
                    pass

            # Right: supporting evidence stacked
            with tag.div(classes="lg:w-2/5 space-y-6"):
                with self.setup_checklist_card(setup_data):
                    pass

                with self.organization_profile_card():
                    pass

            yield


__all__ = ["OverviewSection"]
