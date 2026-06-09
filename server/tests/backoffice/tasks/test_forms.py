import inspect
from typing import NotRequired, Required, TypedDict, Unpack
from unittest.mock import MagicMock

from polar.backoffice.tasks.forms import (
    _get_function_arguments,
    build_enqueue_task_form_class,
)


def _request() -> MagicMock:
    request = MagicMock()
    request.url_for.return_value = "http://test/backoffice/tasks/enqueue"
    return request


class _AllOptional(TypedDict, total=False):
    first_name: str
    last_name: str


class _AllRequired(TypedDict):
    user_id: str
    email: str


class _Mixed(TypedDict):
    user_id: Required[str]
    first_name: NotRequired[str]
    last_name: NotRequired[str]


def test_typeddict_total_false_yields_none_default() -> None:
    def task(**kwargs: Unpack[_AllOptional]) -> None: ...

    args = dict(((k, default) for k, _, default in _get_function_arguments(task)))

    assert args == {"first_name": None, "last_name": None}


def test_typeddict_total_true_yields_empty_default() -> None:
    def task(**kwargs: Unpack[_AllRequired]) -> None: ...

    args = {k: default for k, _, default in _get_function_arguments(task)}

    assert args == {
        "user_id": inspect.Parameter.empty,
        "email": inspect.Parameter.empty,
    }


def test_typeddict_mixed_required_and_not_required() -> None:
    def task(**kwargs: Unpack[_Mixed]) -> None: ...

    args = {k: default for k, _, default in _get_function_arguments(task)}

    assert args == {
        "user_id": inspect.Parameter.empty,
        "first_name": None,
        "last_name": None,
    }


def test_no_parameters_field_when_no_task_selected() -> None:
    form_class = build_enqueue_task_form_class(_request(), None)

    assert "task" in form_class.model_fields
    assert "parameters" not in form_class.model_fields


def test_no_parameters_field_when_selected_task_has_no_arguments() -> None:
    # `auth.delete_expired` is a registered actor that takes no arguments.
    form_class = build_enqueue_task_form_class(_request(), "auth.delete_expired")

    assert "task" in form_class.model_fields
    assert "parameters" not in form_class.model_fields
