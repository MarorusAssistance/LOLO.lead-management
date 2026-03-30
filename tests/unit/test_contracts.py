import pytest
from pydantic import ValidationError

from lolo_lead_management.domain.models import LeadSearchStartRequest


def test_request_contract_fails_fast_on_invalid_json_shape() -> None:
    with pytest.raises(ValidationError):
        LeadSearchStartRequest.model_validate({"unexpected": "value"})
