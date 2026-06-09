import contextlib
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import UUID4, BeforeValidator, ValidationError
from pydantic_core import PydanticCustomError
from sqlalchemy import or_
from sqlalchemy.orm import contains_eager, joinedload
from sse_starlette.sse import EventSourceResponse
from tagflow import classes, document, tag, text

from polar.integrations.plain.service import plain as plain_service
from polar.integrations.polar.service import polar_self as polar_self_service
from polar.kit.currency import format_currency
from polar.kit.pagination import PaginationParams
from polar.models import (
    Account,
    Organization,
)
from polar.models.organization import OrganizationStatus
from polar.models.user_session import UserSession
from polar.organization import sorting
from polar.organization.repository import OrganizationRepository
from polar.organization.schemas import OrganizationFeatureSettings
from polar.organization.service import organization as organization_service
from polar.organization.sorting import OrganizationSortProperty
from polar.postgres import AsyncSession, get_db_read_session, get_db_session
from polar.user.repository import UserRepository
from polar.user_organization.service import (
    CannotRemoveOrganizationOwner,
    UserNotMemberOfOrganization,
)
from polar.user_organization.service import (
    OrganizationNotFound as UserOrgOrganizationNotFound,
)
from polar.user_organization.service import (
    user_organization as user_organization_service,
)

from ..components import accordion, button, datatable, description_list, input, modal
from ..dependencies import get_admin
from ..layout import layout
from ..responses import HXRedirectResponse
from ..toast import add_toast
from .forms import (
    AddPaymentMethodDomainForm,
    OrganizationOrdersImportForm,
    UpdateOrganizationDetailsForm,
    UpdateOrganizationForm,
    UpdateOrganizationInternalNotesForm,
)
from .orders_import import orders_import_sse

router = APIRouter()

logger = structlog.getLogger(__name__)


def empty_str_to_none_before_enum(value: Any) -> Any:
    """Convert empty strings to None before enum parsing."""
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


def empty_str_to_none_before_bool(value: Any) -> Any:
    """Convert empty strings to None before boolean parsing."""
    if isinstance(value, str) and value.strip() == "":
        return None
    if isinstance(value, str):
        if value.lower() in ("true", "1", "yes", "on"):
            return True
        elif value.lower() in ("false", "0", "no", "off"):
            return False
    return value


@contextlib.contextmanager
def organization_badge(organization: Organization) -> Generator[None]:
    with tag.div(classes="badge"):
        if organization.status == OrganizationStatus.ACTIVE:
            classes("badge-success")
        elif organization.status in (
            OrganizationStatus.DENIED,
            OrganizationStatus.OFFBOARDING,
        ):
            classes("badge-warning")
        else:
            classes("badge-secondary")
        text(organization.status.get_display_name())
    yield


class OrganizationStatusColumn(
    datatable.DatatableAttrColumn[Organization, OrganizationSortProperty]
):
    def render(self, request: Request, item: Organization) -> Generator[None] | None:
        with organization_badge(item):
            pass
        return None


class DaysInStatusColumn(
    datatable.DatatableAttrColumn[Organization, OrganizationSortProperty]
):
    def render(self, request: Request, item: Organization) -> Generator[None] | None:
        if item.status_updated_at:
            delta = datetime.now(UTC) - item.status_updated_at
            days = delta.days
        else:
            delta = datetime.now(UTC) - item.created_at
            days = delta.days

        text(f"{days} days in status")
        return None


@router.get("/", name="organizations-classic:list")
async def list(
    request: Request,
    sorting: sorting.ListSorting,
    session: AsyncSession = Depends(get_db_read_session),
    page: int = Query(1, description="Page number, defaults to 1.", gt=0),
    limit: int = Query(100, description="Size of a page, defaults to 100.", gt=0),
    query: str | None = Query(None),
    organization_status: Annotated[
        OrganizationStatus | None,
        BeforeValidator(empty_str_to_none_before_enum),
        Query(),
    ] = None,
) -> None:
    # Create custom pagination with default limit of 100
    pagination = PaginationParams(page, min(100, limit))
    repository = OrganizationRepository.from_session(session)
    statement = (
        repository.get_base_statement(include_deleted=True)
        .join(Account, Organization.account_id == Account.id)
        .options(
            contains_eager(Organization.account),
        )
    )
    if query:
        try:
            statement = statement.where(Organization.id == uuid.UUID(query))
        except ValueError:
            statement = statement.where(
                or_(
                    Organization.name.ilike(f"%{query}%"),
                    Organization.slug.ilike(f"%{query}%"),
                    Organization.website.ilike(f"%{query}%"),
                )
            )
    if organization_status:
        statement = statement.where(Organization.status == organization_status)

    statement = repository.apply_sorting(statement, sorting)
    items, count = await repository.paginate(
        statement, limit=pagination.limit, page=pagination.page
    )

    with layout(
        request,
        [
            ("Organizations", str(request.url_for("organizations-classic:list"))),
        ],
        "organizations-classic:list",
    ):
        with tag.div(classes="flex flex-col gap-4"):
            with tag.div(classes="flex items-center justify-between"):
                with tag.h1(classes="text-4xl"):
                    text("Organizations")
                with tag.a(
                    href=str(request.url_for("organizations:list")),
                    classes="btn btn-ghost btn-sm",
                ):
                    text("Try New View →")
            with tag.form(method="GET", classes="w-full flex flex-col gap-4"):
                with tag.div(classes="flex flex-row gap-2"):
                    with input.search("query", query):
                        pass
                    with input.select(
                        [
                            ("All Organization Statuses", ""),
                            *[
                                (status.get_display_name(), status.value)
                                for status in OrganizationStatus
                            ],
                        ],
                        organization_status.value if organization_status else "",
                        name="organization_status",
                    ):
                        pass
                with tag.div(classes="flex flex-row gap-2"):
                    with input.select(
                        [
                            ("25 per page", "25"),
                            ("50 per page", "50"),
                            ("100 per page", "100"),
                        ],
                        str(limit),
                        name="limit",
                    ):
                        pass
                    with button(type="submit"):
                        text("Filter")
            with datatable.Datatable[Organization, OrganizationSortProperty](
                datatable.DatatableAttrColumn(
                    "id",
                    "ID",
                    href_route_name="organizations-classic:get",
                    clipboard=True,
                ),
                datatable.DatatableDateTimeColumn(
                    "created_at",
                    "Created At",
                    sorting=OrganizationSortProperty.created_at,
                ),
                OrganizationStatusColumn("status", "Status"),
                datatable.DatatableAttrColumn(
                    "name",
                    "Name",
                    sorting=OrganizationSortProperty.organization_name,
                ),
                datatable.DatatableAttrColumn(
                    "slug",
                    "Slug",
                    sorting=OrganizationSortProperty.slug,
                    clipboard=True,
                ),
                DaysInStatusColumn(
                    "status_updated_at",
                    "Days in Status",
                    sorting=OrganizationSortProperty.days_in_status,
                ),
            ).render(request, items, sorting=sorting):
                pass
            with datatable.pagination(request, pagination, count):
                pass


@router.api_route(
    "/{id}/update", name="organizations-classic:update", methods=["GET", "POST"]
)
async def update(
    request: Request,
    id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    org_repo = OrganizationRepository.from_session(session)
    organization = await org_repo.get_by_id(id, include_deleted=True)
    if not organization:
        raise HTTPException(status_code=404)

    validation_error: ValidationError | None = None

    if request.method == "POST":
        data = await request.form()
        try:
            form = UpdateOrganizationForm.model_validate_form(data)
            if form.slug != organization.slug:
                existing_slug = await org_repo.get_by_slug(form.slug)
                if existing_slug is not None:
                    raise ValidationError.from_exception_data(
                        title="SlugAlreadyExists",
                        line_errors=[
                            {
                                "loc": ("slug",),
                                "type": PydanticCustomError(
                                    "SlugAlreadyExists",
                                    "An organization with this slug already exists.",
                                ),
                                "input": form.slug,
                            }
                        ],
                    )

            form_dict = form.model_dump(exclude_none=True)
            feature_flags_from_form = form_dict.pop("feature_flags", None)
            checkout_settings_from_form = form_dict.pop("checkout_settings", None)

            # Dynamically handle all feature flags from OrganizationFeatureSettings
            # If None, all checkboxes were unchecked - set all to False
            if feature_flags_from_form is None:
                # Get all field names from OrganizationFeatureSettings and set to False
                feature_flags = {
                    field_name: False
                    for field_name in OrganizationFeatureSettings.model_fields.keys()
                }
            else:
                # Use the values from the form, ensuring all fields have values
                feature_flags = {
                    field_name: feature_flags_from_form.get(field_name, False)
                    for field_name in OrganizationFeatureSettings.model_fields.keys()
                }

            # Merge with existing feature_settings
            updated_feature_settings = {
                **organization.feature_settings,
                **feature_flags,
            }

            # Handle checkout_settings - if None, all checkboxes were unchecked
            checkout_settings = {
                "require_3ds": checkout_settings_from_form.get("require_3ds", False)
                if checkout_settings_from_form
                else False
            }

            # Merge with existing checkout_settings
            updated_checkout_settings = {
                **organization.checkout_settings,
                **checkout_settings,
            }

            previous_slug = organization.slug
            # Update organization with basic fields, feature_settings, and checkout_settings
            organization = await org_repo.update(
                organization,
                update_dict={
                    **form_dict,
                    "feature_settings": updated_feature_settings,
                    "checkout_settings": updated_checkout_settings,
                },
            )
            if organization.slug != previous_slug:
                polar_self_service.enqueue_update_customer_slug(
                    organization_id=organization.id,
                    slug=organization.slug,
                )
            return HXRedirectResponse(
                request, str(request.url_for("organizations-classic:get", id=id)), 303
            )

        except ValidationError as e:
            validation_error = e

    # Prepare data for form rendering with current feature settings
    # Dynamically populate all feature flags from OrganizationFeatureSettings
    form_data = {
        "name": organization.name,
        "slug": organization.slug,
        "feature_flags": {
            field_name: organization.feature_settings.get(field_name, False)
            for field_name in OrganizationFeatureSettings.model_fields.keys()
        },
        "checkout_settings": {
            "require_3ds": organization.checkout_require_3ds,
        },
    }

    with modal("Update Organization", open=True):
        with UpdateOrganizationForm.render(
            form_data,
            hx_post=str(request.url_for("organizations-classic:update", id=id)),
            hx_target="#modal",
            classes="flex flex-col gap-4",
            validation_error=validation_error,
        ):
            with tag.div(classes="modal-action"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(
                    type="submit",
                    variant="primary",
                ):
                    text("Update")


@router.api_route(
    "/{id}/update_details",
    name="organizations-classic:update_details",
    methods=["GET", "POST"],
)
async def update_details(
    request: Request,
    id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    org_repo = OrganizationRepository.from_session(session)
    organization = await org_repo.get_by_id(id, include_deleted=True)
    if not organization:
        raise HTTPException(status_code=404)

    validation_error: ValidationError | None = None

    if request.method == "POST":
        data = await request.form()
        try:
            form = UpdateOrganizationDetailsForm.model_validate_form(data)
            organization = await org_repo.update(
                organization, update_dict=form.model_dump(exclude_none=True)
            )
            return HXRedirectResponse(
                request, str(request.url_for("organizations-classic:get", id=id)), 303
            )

        except ValidationError as e:
            validation_error = e

    with modal("Edit Business Information", open=True):
        with tag.p(classes="text-sm text-base-content-secondary"):
            text("Update the key information about your business and products")

        with UpdateOrganizationDetailsForm.render(
            data=organization,
            validation_error=validation_error,
            hx_post=str(request.url_for("organizations-classic:update_details", id=id)),
            hx_target="#modal",
            classes="space-y-6",
        ):
            # Action buttons
            with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(
                    type="submit",
                    variant="primary",
                ):
                    text("Update Details")


@router.api_route(
    "/{id}/update_internal_notes",
    name="organizations-classic:update_internal_notes",
    methods=["GET", "POST"],
)
async def update_internal_notes(
    request: Request,
    id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    org_repo = OrganizationRepository.from_session(session)
    organization = await org_repo.get_by_id(id, include_deleted=True)
    if not organization:
        raise HTTPException(status_code=404)

    validation_error: ValidationError | None = None

    if request.method == "POST":
        data = await request.form()
        try:
            form = UpdateOrganizationInternalNotesForm.model_validate_form(data)
            organization = await org_repo.update(
                organization, update_dict=form.model_dump(exclude_none=True)
            )
            return HXRedirectResponse(
                request, str(request.url_for("organizations-classic:get", id=id)), 303
            )

        except ValidationError as e:
            validation_error = e

    with modal("Edit Internal Notes", open=True):
        with tag.p(classes="text-sm text-base-content-secondary"):
            text("Add or update internal notes about this organization (admin only)")

        with UpdateOrganizationInternalNotesForm.render(
            data=organization,
            validation_error=validation_error,
            hx_post=str(
                request.url_for("organizations-classic:update_internal_notes", id=id)
            ),
            hx_target="#modal",
            classes="space-y-4",
        ):
            # Action buttons
            with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(
                    type="submit",
                    variant="primary",
                ):
                    text("Save Notes")


@router.api_route(
    "/{id}/delete", name="organizations-classic:delete", methods=["GET", "POST"]
)
async def delete(
    request: Request,
    id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    org_repo = OrganizationRepository.from_session(session)
    organization = await org_repo.get_by_id(id, include_deleted=True)
    if not organization:
        raise HTTPException(status_code=404)

    if organization.is_deleted:
        await add_toast(
            request, "This organization is already deleted", variant="error"
        )
        return

    if request.method == "POST":
        await organization_service.delete(session, organization)
        await add_toast(
            request,
            f"Organization with ID {organization.id} has been deleted",
            "success",
        )

        return

    with modal(f"Delete Organization {organization.id}", open=True):
        with tag.div(classes="flex flex-col gap-4"):
            with tag.p():
                text("Are you sure you want to delete this Organization? ")

            with tag.p():
                text("Deleting this Organization DOES NOT:")
            with tag.ul(classes="list-disc list-inside"):
                with tag.li():
                    text("Delete or anonymize Users related Organization")
                with tag.li():
                    text("Delete or anonymize Account of the Organization")
                with tag.li():
                    text(
                        "Delete or anonymize Customers, Products, Discounts, Benefits, Checkouts of the Organization"
                    )
                with tag.li():
                    text("Revoke Benefits granted")
                with tag.li():
                    text("Remove API tokens (organization or personal)")

            with tag.p():
                text("The User can be deleted separately")

            with tag.div(classes="modal-action"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with tag.form(method="dialog"):
                    with button(
                        type="button",
                        variant="primary",
                        hx_post=str(request.url),
                        hx_target="#modal",
                    ):
                        text("Delete")


@router.get(
    "/{id}/confirm_remove_member/{user_id}",
    name="organizations-classic:confirm_remove_member",
)
async def confirm_remove_member(
    request: Request,
    id: UUID4,
    user_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Show confirmation modal for removing a member."""

    # Get user info for the modal
    user_repo = UserRepository.from_session(session)
    user = await user_repo.get_by_id(user_id)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    with modal(f"Remove {user.email}", open=True):
        with tag.div(classes="flex items-start gap-4 mb-6"):
            # Message content
            with tag.div(classes="flex-1"):
                with tag.p(classes="text-sm text-gray-600 mb-4"):
                    text("Are you sure you want to remove ")
                    with tag.strong():
                        text(user.email)
                    text(" from this organization?")

                with tag.p(classes="text-xs text-gray-500"):
                    text(
                        "This action cannot be undone. The user will lose access to all organization resources."
                    )

        # Action buttons
        with tag.div(classes="modal-action"):
            with tag.form(method="dialog"):
                with button(ghost=True):
                    text("Cancel")

            with tag.form(method="dialog"):
                with button(
                    variant="error",
                    hx_delete=str(
                        request.url_for(
                            "organizations-classic:remove_member",
                            id=id,
                            user_id=user_id,
                        )
                    ),
                    hx_target="#modal",
                ):
                    text("Remove User")


@router.api_route(
    "/{id}/remove_member/{user_id}",
    name="organizations-classic:remove_member",
    methods=["DELETE"],
)
async def remove_member(
    request: Request,
    id: UUID4,
    user_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Remove member endpoint with DELETE method."""

    try:
        # Get user info for better error messages
        user_repo = UserRepository.from_session(session)
        user = await user_repo.get_by_id(user_id)
        user_email = user.email if user else str(user_id)

        # Attempt to remove the member safely
        await user_organization_service.remove_member_safe(
            session,
            user_id=user_id,
            organization_id=id,
        )

        # Add success toast and redirect
        await add_toast(
            request,
            f"{user_email} has been removed from the organization",
            "success",
        )

        return HXRedirectResponse(
            request, request.url_for("organizations-classic:get", id=id), 303
        )

    except UserOrgOrganizationNotFound:
        raise HTTPException(status_code=404, detail="Organization not found")

    except UserNotMemberOfOrganization:
        raise HTTPException(
            status_code=400, detail="User is not a member of this organization"
        )

    except CannotRemoveOrganizationOwner:
        raise HTTPException(
            status_code=403,
            detail=f"Cannot remove {user_email} - they are the organization owner",
        )

    except Exception:
        raise HTTPException(
            status_code=500, detail="An error occurred while removing the user"
        )


@router.post(
    "/{id}/create_plain_thread",
    name="organizations-classic:create_plain_thread",
)
async def create_plain_thread(
    request: Request,
    id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Create a Plain thread for this organization."""
    try:
        form = await request.form()
        logger.info(f"Form data received: {dict(form)}")
        title_field = form.get("title", "")
        title = title_field.strip() if isinstance(title_field, str) else ""
        logger.info(f"Extracted title: '{title}'")
        if not title:
            await add_toast(
                request,
                "Thread title is required",
                "error",
            )
            return RedirectResponse(
                url=request.url_for("organizations-classic:get_organization", id=id),
                status_code=302,
            )

        org_repo = OrganizationRepository.from_session(session)
        organization = await org_repo.get_by_id(id, include_deleted=True)
        if not organization:
            raise HTTPException(status_code=404)

        owner_user = await org_repo.get_owner_user(organization)
        if not owner_user:
            raise HTTPException(status_code=404, detail="No owner user found")

        thread_id = await plain_service.create_manual_organization_thread(
            session, organization, owner_user, title
        )
        logger.info(
            f"Created Plain thread {thread_id} for organization {organization.id}"
        )

        thread_url = f"https://app.plain.com/workspace/w_01JE9TRRX9KT61D8P2CH77XDQM/thread/{thread_id}"

        with document() as doc:
            with tag.div(id="modal"):
                with tag.dialog(classes="modal modal-open"):
                    with tag.div(classes="modal-box"):
                        with tag.h3(classes="font-bold text-lg text-success"):
                            text("✅ Thread Created Successfully!")

                        with tag.p(classes="py-4"):
                            text(
                                "Your Plain thread has been created. Click the link below to open it:"
                            )

                        with tag.div(classes="modal-action"):
                            with tag.a(
                                href=thread_url,
                                target="_blank",
                                classes="btn btn-primary",
                            ):
                                text("🔗 Open Plain Thread")
                            with tag.button(
                                type="button",
                                classes="btn",
                                hx_get=str(
                                    request.url_for(
                                        "organizations-classic:clear_modal",
                                        id=organization.id,
                                    )
                                ),
                                hx_target="#modal",
                            ):
                                text("Close")

                    with tag.div(
                        classes="modal-backdrop",
                        hx_get=str(
                            request.url_for(
                                "organizations-classic:clear_modal", id=organization.id
                            )
                        ),
                        hx_target="#modal",
                    ):
                        pass

        return HTMLResponse(str(doc))

    except Exception as e:
        logger.error(f"Error in create_plain_thread: {str(e)}", exc_info=True)
        await add_toast(
            request,
            f"Failed to create Plain thread: {str(e)}",
            "error",
        )

        return HXRedirectResponse(
            request, str(request.url_for("organizations-classic:get", id=id)), 303
        )


@router.api_route("/{id}", name="organizations-classic:get", methods=["GET", "POST"])
async def get(
    request: Request,
    id: UUID4,
    session: AsyncSession = Depends(get_db_session),
    user_session: UserSession = Depends(get_admin),
) -> Any:
    repository = OrganizationRepository.from_session(session)
    organization = await repository.get_by_id(
        id,
        options=(
            joinedload(Organization.account),
            joinedload(Organization.payout_account),
        ),
        include_deleted=True,
        include_blocked=True,
    )

    if organization is None:
        raise HTTPException(status_code=404)

    user_repository = UserRepository.from_session(session)
    users = await user_repository.get_all_by_organization(organization.id)

    with layout(
        request,
        [
            (organization.name, str(request.url)),
            ("Organizations", str(request.url_for("organizations-classic:list"))),
        ],
        "organizations-classic:get",
    ):
        with tag.div(classes="flex flex-col gap-4"):
            with tag.div(classes="flex justify-between items-center"):
                with tag.h1(classes="text-4xl"):
                    text(organization.name)
                with tag.div(classes="flex gap-2"):
                    # Plain Actions
                    with tag.a(
                        classes="btn",
                        href=str(
                            request.url_for(
                                "organizations-classic:plain_search_url",
                                id=organization.id,
                            )
                        ),
                        title="Search in Plain",
                        target="_blank",
                    ):
                        with tag.div(classes="icon-search"):
                            pass
                        text("Search in Plain")
                    with tag.button(
                        classes="btn",
                        hx_get=str(
                            request.url_for(
                                "organizations-classic:create_thread_modal",
                                id=organization.id,
                            )
                        ),
                        hx_target="#modal",
                        title="Create Thread in Plain",
                    ):
                        with tag.div(classes="icon-message-square-more"):
                            pass
                        text("Create Thread")
                    with tag.button(
                        classes="btn",
                        hx_get=str(
                            request.url_for(
                                "organizations-classic:import_orders",
                                id=organization.id,
                            )
                        ),
                        hx_target="#modal",
                        title="Import Orders",
                    ):
                        with tag.div(classes="icon-upload"):
                            pass
                        text("Import Orders")
                    with tag.button(
                        classes="btn",
                        hx_get=str(
                            request.url_for(
                                "organizations-classic:add_payment_method_domain",
                                id=organization.id,
                            )
                        ),
                        hx_target="#modal",
                        title="Add Domain to Apple Pay / Google Pay Allowlist",
                    ):
                        with tag.div(classes="icon-globe"):
                            pass
                        text("Add Domain to Allowlist")
                    with button(
                        variant="primary",
                        hx_get=str(
                            request.url_for(
                                "organizations-classic:update", id=organization.id
                            )
                        ),
                        hx_target="#modal",
                    ):
                        text("Edit")
                    with tag.a(
                        classes="btn",
                        href=str(
                            request.url_for(
                                "organizations:detail",
                                organization_id=organization.id,
                            )
                        ),
                        title="Switch to new view",
                    ):
                        text("View V2")
            with tag.div(classes="grid grid-cols-1 lg:grid-cols-2 gap-4"):
                with description_list.DescriptionList[Organization](
                    description_list.DescriptionListAttrItem(
                        "id", "ID", clipboard=True
                    ),
                    description_list.DescriptionListAttrItem(
                        "slug", "Slug", clipboard=True
                    ),
                    description_list.DescriptionListDateTimeItem(
                        "created_at", "Created At"
                    ),
                    description_list.DescriptionListDateTimeItem(
                        "deleted_at", "Deleted At"
                    ),
                    description_list.DescriptionListLinkItem(
                        "website", "Website", external=True
                    ),
                    description_list.DescriptionListAttrItem(
                        "email", "Support email", clipboard=True
                    ),
                    description_list.DescriptionListSocialsItem("Social Links"),
                ).render(request, organization):
                    pass
                # Simple users table
                with tag.div(classes="card card-border w-full shadow-sm"):
                    with tag.div(classes="card-body"):
                        with tag.div(classes="flex justify-between items-center mb-4"):
                            with tag.h2(classes="card-title"):
                                text(f"Team Members ({len(users)})")

                        owner_user = await repository.get_owner_user(organization)

                        if users:
                            # Users table
                            with tag.div(classes="overflow-x-auto"):
                                with tag.table(classes="table table-zebra w-full"):
                                    # Table header
                                    with tag.thead():
                                        with tag.tr():
                                            with tag.th():
                                                text("User")
                                            with tag.th():
                                                text("Role")
                                            with tag.th():
                                                text("Joined")
                                            with tag.th():
                                                text("Actions")

                                    # Table body
                                    with tag.tbody():
                                        for user in users:
                                            is_owner = (
                                                owner_user and user.id == owner_user.id
                                            )
                                            with tag.tr():
                                                # User info
                                                with tag.td():
                                                    with tag.div(
                                                        classes="flex items-center gap-3"
                                                    ):
                                                        # User details
                                                        with tag.div():
                                                            with tag.a(
                                                                href=str(
                                                                    request.url_for(
                                                                        "users:get",
                                                                        id=user.id,
                                                                    )
                                                                ),
                                                                classes="font-medium hover:text-primary",
                                                            ):
                                                                text(user.email)

                                                # Role
                                                with tag.td():
                                                    if is_owner:
                                                        with tag.span(
                                                            classes="badge badge-primary"
                                                        ):
                                                            text("Owner")
                                                    else:
                                                        with tag.span(
                                                            classes="badge badge-ghost"
                                                        ):
                                                            text("Member")

                                                # Joined date
                                                with tag.td():
                                                    with tag.span(
                                                        classes="text-sm text-gray-600"
                                                    ):
                                                        if (
                                                            hasattr(user, "created_at")
                                                            and user.created_at
                                                        ):
                                                            text(
                                                                user.created_at.strftime(
                                                                    "%b %d, %Y"
                                                                )
                                                            )
                                                        else:
                                                            text("—")

                                                # Actions
                                                with tag.td():
                                                    with tag.div(classes="flex gap-2"):
                                                        # Impersonate button (hidden on soft-deleted organizations)
                                                        if not organization.is_deleted:
                                                            with tag.button(
                                                                classes="btn btn-primary btn-sm",
                                                                hx_post=str(
                                                                    request.url_for(
                                                                        "backoffice:start_impersonation",
                                                                    )
                                                                ),
                                                                hx_vals=f'{{"user_id": "{user.id}", "organization_id": "{organization.id}"}}',
                                                                hx_confirm="Are you sure you want to impersonate this user?",
                                                            ):
                                                                text("Impersonate")

                        else:
                            # Empty state
                            with tag.div(classes="text-center py-8"):
                                with tag.div(classes="text-gray-400 mb-2"):
                                    text("👥")
                                with tag.p(classes="text-gray-600"):
                                    text("No team members yet")

                with tag.div(classes="card card-border w-full shadow-sm"):
                    with tag.div(classes="card-body"):
                        with tag.div(classes="flex justify-between items-center"):
                            with tag.h2(classes="card-title"):
                                text("Details")
                            with button(
                                hx_get=str(
                                    request.url_for(
                                        "organizations-classic:update_details",
                                        id=organization.id,
                                    )
                                ),
                                hx_target="#modal",
                                variant="secondary",
                            ):
                                text("Edit Details")

                        a = "organization-details-accordion"
                        with accordion.item(a, "About"):
                            with tag.p(classes="whitespace-pre-line"):
                                text(organization.details.get("about") or "—")
                        with accordion.item(a, "Product Description"):
                            with tag.p(classes="whitespace-pre-line"):
                                text(
                                    organization.details.get("product_description")
                                    or "—"
                                )
                        if organization.details.get("selling_categories"):
                            with accordion.item(a, "Selling Categories"):
                                with tag.ul(classes="list-disc list-inside"):
                                    for category in organization.details.get(
                                        "selling_categories", []
                                    ):
                                        with tag.li():
                                            text(category)
                        if organization.details.get("pricing_models"):
                            with accordion.item(a, "Pricing Models"):
                                with tag.ul(classes="list-disc list-inside"):
                                    for model in organization.details.get(
                                        "pricing_models", []
                                    ):
                                        with tag.li():
                                            text(model)
                        if organization.details.get("intended_use"):
                            with accordion.item(a, "Intended Use"):
                                with tag.p(classes="whitespace-pre-line"):
                                    text(
                                        organization.details.get("intended_use") or "—"
                                    )
                        if organization.details.get("customer_acquisition"):
                            with accordion.item(a, "Acquisition"):
                                with tag.ul(classes="list-disc list-inside"):
                                    for acquisition in organization.details.get(
                                        "customer_acquisition", []
                                    ):
                                        with tag.li():
                                            text(acquisition)
                        if organization.details.get("future_annual_revenue"):
                            with accordion.item(a, "Expected annual revenue"):
                                text(
                                    format_currency(
                                        organization.details["future_annual_revenue"],
                                        "usd",
                                    )
                                )
                        if organization.details.get("switching"):
                            with accordion.item(a, "Switching from"):
                                prev_rev = organization.details.get(
                                    "previous_annual_revenue"
                                )
                                prev_rev_str = (
                                    format_currency(prev_rev, "usd")
                                    if prev_rev is not None
                                    else "N/A"
                                )
                                text(
                                    f"{organization.details['switching_from']} ({prev_rev_str})"
                                )

            # Internal Notes Section
            with tag.div(classes="card card-border w-full shadow-sm"):
                with tag.div(classes="card-body"):
                    with tag.div(classes="flex justify-between items-center mb-4"):
                        with tag.h2(classes="card-title"):
                            text("Internal Notes")
                        with button(
                            hx_get=str(
                                request.url_for(
                                    "organizations-classic:update_internal_notes",
                                    id=organization.id,
                                )
                            ),
                            hx_target="#modal",
                            variant="secondary",
                        ):
                            text("Edit Notes")

                    if organization.internal_notes:
                        with tag.div(classes="prose max-w-none"):
                            with tag.p(classes="whitespace-pre-line text-sm"):
                                text(organization.internal_notes)
                    else:
                        with tag.div(classes="text-center py-4"):
                            with tag.p(classes="text-gray-400"):
                                text("No internal notes yet")


@router.get("/{id}/plain_search_url", name="organizations-classic:plain_search_url")
async def get_plain_search_url(
    request: Request,
    id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Get the Plain search URL for this organization's admin."""
    org_repo = OrganizationRepository.from_session(session)
    organization = await org_repo.get_by_id(id, include_deleted=True)
    if not organization:
        raise HTTPException(status_code=404)

    owner_user = await org_repo.get_owner_user(organization)
    if not owner_user:
        raise HTTPException(status_code=404, detail="No owner user found")

    search_url = f"https://app.plain.com/workspace/w_01JE9TRRX9KT61D8P2CH77XDQM/search/?q={owner_user.email}"

    return RedirectResponse(url=search_url, status_code=302)


@router.get(
    "/{id}/create_thread_modal", name="organizations-classic:create_thread_modal"
)
async def get_create_thread_modal(
    request: Request,
    id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Get the create thread modal HTML."""
    org_repo = OrganizationRepository.from_session(session)
    organization = await org_repo.get_by_id(id, include_deleted=True)
    if not organization:
        raise HTTPException(status_code=404)

    with document() as doc:
        with tag.div(id="modal"):
            with tag.dialog(classes="modal modal-open"):
                with tag.div(classes="modal-box"):
                    with tag.form(method="dialog"):
                        with tag.button(
                            classes="btn btn-sm btn-circle btn-ghost absolute right-2 top-2"
                        ):
                            text("✕")

                    with tag.h3(classes="font-bold text-lg"):
                        text("Create Plain Thread")

                    with tag.form(
                        id="create-thread-form",
                        hx_post=str(
                            request.url_for(
                                "organizations-classic:create_plain_thread",
                                id=organization.id,
                            )
                        ),
                        hx_target="#modal",
                    ):
                        with tag.div(classes="form-control w-full mt-4"):
                            with tag.label(classes="label"):
                                with tag.span(classes="label-text"):
                                    text("Thread Title")
                            with tag.input(
                                type="text",
                                name="title",
                                placeholder="Enter thread title...",
                                classes="input input-bordered w-full",
                                required=True,
                                autofocus=True,
                            ):
                                pass

                        with tag.div(classes="modal-action"):
                            with tag.button(
                                type="button",
                                classes="btn",
                                hx_get=str(
                                    request.url_for(
                                        "organizations-classic:clear_modal",
                                        id=organization.id,
                                    )
                                ),
                                hx_target="#modal",
                            ):
                                text("Cancel")
                            with tag.button(
                                type="submit",
                                classes="btn btn-primary",
                            ):
                                text("Create Thread")

                with tag.div(
                    classes="modal-backdrop",
                    hx_get=str(
                        request.url_for(
                            "organizations-classic:clear_modal", id=organization.id
                        )
                    ),
                    hx_target="#modal",
                ):
                    pass

    return HTMLResponse(str(doc))


@router.get("/{id}/clear_modal", name="organizations-classic:clear_modal")
async def clear_modal(id: UUID4) -> Any:
    """Clear the modal content."""
    return HTMLResponse('<div id="modal"></div>')


@router.api_route(
    "/{id}/import-orders",
    name="organizations-classic:import_orders",
    methods=["GET", "POST"],
)
async def import_orders(
    request: Request,
    id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    repository = OrganizationRepository.from_session(session)
    organization = await repository.get_by_id(id, include_deleted=True)

    if organization is None:
        raise HTTPException(status_code=404)

    validation_error: ValidationError | None = None
    if request.method == "POST":
        data = await request.form()
        try:
            form = OrganizationOrdersImportForm.model_validate_form(data)
            return EventSourceResponse(
                orders_import_sse(
                    session,
                    organization,
                    form.file,
                )
            )
        except ValidationError as e:
            validation_error = e

    with modal("Import Orders", open=True):
        with OrganizationOrdersImportForm.render(
            {},
            action=str(request.url),
            method="POST",
            classes="flex flex-col",
            validation_error=validation_error,
            _="on submit halt the event then call formPostSSE(me, '#import-progress')",
        ):
            with tag.div(id="import-progress"):
                pass
            with tag.div(classes="modal-action"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(
                    type="submit",
                    variant="primary",
                ):
                    text("Import")


@router.api_route(
    "/{id}/add-payment-method-domain",
    name="organizations-classic:add_payment_method_domain",
    methods=["GET", "POST"],
)
async def add_payment_method_domain(
    request: Request,
    id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    repository = OrganizationRepository.from_session(session)
    organization = await repository.get_by_id(id)

    if organization is None:
        raise HTTPException(status_code=404)

    validation_error: ValidationError | None = None
    if request.method == "POST":
        data = await request.form()
        try:
            form = AddPaymentMethodDomainForm.model_validate_form(data)

            await add_toast(
                request,
                "Payment method domain allowlist not available (Stripe removed).",
                variant="error",
            )
            return

        except ValidationError as e:
            validation_error = e
            error_message = (
                "Unable to add domain to allowlist. "
                "Please verify the domain and try again."
            )
            await add_toast(request, error_message, variant="error")

    with modal("Add Domain to Allowlist", open=True):
        with tag.p(classes="text-sm text-base-content-secondary mb-4"):
            text(
                "Add a custom domain to the Apple Pay / Google Pay allowlist. "
                "This allows these payment methods to appear in embeds on the specified domain."
            )

        with AddPaymentMethodDomainForm.render(
            {},
            hx_post=str(request.url),
            hx_target="#modal",
            classes="flex flex-col gap-4",
            validation_error=validation_error,
        ):
            with tag.div(classes="modal-action"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(
                    type="submit",
                    variant="primary",
                ):
                    text("Add Domain")
