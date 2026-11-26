"""Reuse a full check only when its exact specification has not changed."""
from copy import deepcopy


class CheckedResult:
    def __init__(self, spec, parsed, validation):
        self.spec_snapshot = spec.model_dump(mode="json")
        self.parsed = parsed
        self.validation = validation

    def get(self, spec):
        if spec.model_dump(mode="json") == self.spec_snapshot:
            return deepcopy(self.parsed), deepcopy(self.validation)
        return None


def reuse_or_parse(profile, path, spec, checks):
    for checked in checks:
        result = checked.get(spec) if checked is not None else None
        if result is not None:
            return *result, True
    parsed = profile.parse(path, spec)
    return parsed, profile.validate(parsed, spec), False
