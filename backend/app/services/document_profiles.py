"""Document families are independent of source layouts and output templates.

Legacy API values remain stable. Planned families cannot enter a parser until
their extraction and validation strategy is implemented.
"""
from dataclasses import asdict, dataclass

from backend.app.core.errors import AppError
from backend.app.models.domain import DataType


@dataclass(frozen=True)
class DocumentProfile:
    id: str
    name: str
    data_type: DataType | None
    template_ids: tuple[str, ...]
    available: bool = False
    schema_version: int = 1
    memory_enabled: bool = False

    def public(self):
        return asdict(self)

    def require_available(self):
        if not self.available or self.data_type is None:
            raise AppError(422, "Обработчик этого типа документа пока не реализован", "PROFILE_UNAVAILABLE")

    def plan(self, path, template_id):
        from backend.app.services.hypothesis_planner import choose_plan
        self.require_available()
        if template_id not in self.template_ids:
            raise AppError(400, "Несовместимый целевой формат", "INVALID_TEMPLATE")
        return choose_plan(path, self.data_type, template_id)

    def parse(self, path, spec):
        from backend.app.services.parser import parse_workbook
        self.require_available()
        return parse_workbook(path, spec, self.data_type)

    def validate(self, parsed, spec):
        from backend.app.services.validator import validate_result
        self.require_available()
        return validate_result(parsed, spec)


PROFILES = (
    DocumentProfile("trial-balance-general", "Общая ОСВ", DataType.TRIAL_BALANCE,
                    ("trial_balance_flat_v1",), True, memory_enabled=True),
    DocumentProfile("trial-balance-account", "ОСВ по отдельному счёту", None, ()),
    DocumentProfile("journal", "Журнал проводок", DataType.JOURNAL, ("journal_flat_v1",), True),
    DocumentProfile("balance-sheet", "Баланс", None, ()),
    DocumentProfile("profit-and-loss", "Отчёт о прибылях и убытках", None, ()),
)


def profile_for(data_type: DataType, template_id: str) -> DocumentProfile:
    for profile in PROFILES:
        if profile.available and profile.data_type == data_type and template_id in profile.template_ids:
            return profile
    raise AppError(400, "Выбран несовместимый или неподдерживаемый профиль документа", "INVALID_TEMPLATE")
