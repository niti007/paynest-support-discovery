"""Response schemas for the mock payroll API.

payroll_api_openapi.yaml gives descriptions only (no response schemas), so these JSON Schemas are
this project's reading of those descriptions. Tests validate every mock response against them and
check the mock's paths/methods against the YAML.
"""
TENANT = {
    "type": "object", "additionalProperties": False,
    "required": ["tenant_id", "company_name", "region", "contract_tier", "employee_count"],
    "properties": {"tenant_id": {"type": "string", "pattern": "^C-[0-9]{4}$"},
                   "company_name": {"type": "string"},
                   "region": {"enum": ["IN", "UAE"]},
                   "contract_tier": {"enum": ["Enterprise", "Business", "Standard"]},
                   "employee_count": {"type": "integer", "minimum": 0}},
}
RUN_STATES = ["draft", "computing", "awaiting_approval", "approved", "bank_file_generated", "paid", "failed"]
RUN = {
    "type": "object", "additionalProperties": False,
    "required": ["run_id", "tenant_id", "month", "state", "last_engine_message", "updated_at"],
    "properties": {"run_id": {"type": "string"}, "tenant_id": {"type": "string"},
                   "month": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}$"},
                   "state": {"enum": RUN_STATES},
                   "last_engine_message": {"type": "string"},
                   "updated_at": {"type": "string"}},
}
RUN_LIST = {
    "type": "object", "additionalProperties": False,
    "required": ["items", "page", "page_size", "total"],
    "properties": {"items": {"type": "array", "items": RUN}, "page": {"type": "integer"},
                   "page_size": {"type": "integer"}, "total": {"type": "integer"}},
}
PAYSLIP = {  # masked by design: no names, no amounts, no bank details
    "type": "object", "additionalProperties": False,
    "required": ["run_id", "employee_ref", "period", "status", "currency"],
    "properties": {"run_id": {"type": "string"}, "employee_ref": {"type": "string"},
                   "period": {"type": "string"}, "status": {"enum": ["generated", "pending", "withheld"]},
                   "currency": {"enum": ["INR", "AED"]}},
}
RATES = {
    "type": "object", "additionalProperties": False,
    "required": ["region", "as_of", "rules"],
    "properties": {"region": {"enum": ["IN", "UAE"]}, "as_of": {"type": "string", "format": "date"},
                   "rules": {"type": "array", "items": {
                       "type": "object", "additionalProperties": False,
                       "required": ["rule_id", "topic", "title", "value", "effective_from", "source"],
                       "properties": {"rule_id": {"type": ["string", "null"]}, "topic": {"type": "string"},
                                      "title": {"type": "string"}, "value": {"type": "string"},
                                      "effective_from": {"type": ["string", "null"]},
                                      "source": {"type": "string"}}}}},
}
ERROR = {"type": "object", "required": ["error"], "properties": {"error": {"type": "string"}}}
# openapi path -> schema of the 200 body
BY_PATH = {
    "/tenants/{tenant_id}": TENANT,
    "/tenants/{tenant_id}/payroll-runs": RUN_LIST,
    "/payroll-runs/{run_id}/status": RUN,
    "/payroll-runs/{run_id}/employees/{employee_id}/payslip": PAYSLIP,
    "/statutory/rates": RATES,
}
