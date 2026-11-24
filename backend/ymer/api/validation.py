"""Request-body parsing with Pydantic. Invalid bodies become a 422 JSON error."""

from flask import request
from pydantic import BaseModel, ValidationError
from werkzeug.exceptions import UnprocessableEntity


class InvalidBody(UnprocessableEntity):
    def __init__(self, errors: list[dict]):
        super().__init__("The request body is invalid.")
        self.errors = errors


def parse_body[M: BaseModel](model: type[M]) -> M:
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        errors = [
            {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
            for e in exc.errors(include_url=False, include_input=False)
        ]
        raise InvalidBody(errors) from exc
