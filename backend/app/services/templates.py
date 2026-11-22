from backend.app.models.domain import DataType, TargetColumn, TemplateInfo


TRIAL_BALANCE_COLUMNS = [
    TargetColumn(key="account", label="Счет", required=True),
    TargetColumn(key="subaccount", label="Субсчет"),
    TargetColumn(key="account_name", label="Наименование счёта"),
    TargetColumn(key="opening_debit", label="Сальдо на начало Дт", kind="number"),
    TargetColumn(key="opening_credit", label="Сальдо на начало Кт", kind="number"),
    TargetColumn(key="turnover_debit", label="Оборот Дт", kind="number"),
    TargetColumn(key="turnover_credit", label="Оборот Кт", kind="number"),
    TargetColumn(key="closing_debit", label="Сальдо на конец Дт", kind="number"),
    TargetColumn(key="closing_credit", label="Сальдо на конец Кт", kind="number"),
]

JOURNAL_COLUMNS = [
    TargetColumn(key="date", label="Дата", kind="date", required=True),
    TargetColumn(key="document_number", label="Номер документа"),
    TargetColumn(key="debit_account", label="Счет Дт", required=True),
    TargetColumn(key="credit_account", label="Счет Кт", required=True),
    TargetColumn(key="debit_analytics", label="Аналитика Дт"),
    TargetColumn(key="credit_analytics", label="Аналитика Кт"),
    TargetColumn(key="amount", label="Сумма", kind="number", required=True),
    TargetColumn(key="description", label="Содержание операции"),
]

TEMPLATES = {
    "trial_balance_flat_v1": TemplateInfo(
        id="trial_balance_flat_v1",
        data_type=DataType.TRIAL_BALANCE,
        name="Плоская ОСВ",
        description="Счета, субсчета, наименования счетов, начальные и конечные сальдо, дебетовые и кредитовые обороты.",
        columns=TRIAL_BALANCE_COLUMNS,
    ),
    "journal_flat_v1": TemplateInfo(
        id="journal_flat_v1",
        data_type=DataType.JOURNAL,
        name="Плоский журнал проводок",
        description="Дата, документ, корреспонденция счетов, аналитика, сумма и содержание операции.",
        columns=JOURNAL_COLUMNS,
    ),
}


def get_template(template_id: str) -> TemplateInfo:
    return TEMPLATES[template_id]

