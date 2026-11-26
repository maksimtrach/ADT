from __future__ import annotations

from pathlib import Path
from copy import deepcopy

from backend.app.core.errors import AppError
from backend.app.db.database import connection, json_dump, json_load, record_audit, utc_now
from backend.app.models.domain import DataType, ProcessingStage, TransformationSpec
from backend.app.services.exporter import export_result
from backend.app.services.file_profiler import profile_workbook
from backend.app.services.planner import LLMPlanner
from backend.app.services.processing_log import create_processing_run, record_run_event, set_run_status
from backend.app.services.hypothesis_planner import SourceStructure, PlanSelection
from backend.app.services.feedback_rules import explicit_column_feedback
from backend.app.services.explicit_layout import complete_mapping, plan_from_explicit_mapping
from backend.app.services.explicit_feedback import parse_feedback, apply_bounds
from backend.app.core.config import settings
from backend.app.services.document_profiles import profile_for
from backend.app.services.document_memory import MemoryStore, MemoryCase
from backend.app.services.memory_planner import capture_card, memory_plan
from backend.app.services.structure_report import RecognitionSource, report_from_spec

from backend.app.services.checked_result import CheckedResult, reuse_or_parse

def baseline_is_reliable(spec: TransformationSpec, validation: dict) -> bool:
    """Internal checks allow automation; absence of independent totals stays visible."""
    numeric = [column.key for column in spec.target_columns if column.kind == "number"]
    return (
        spec.confidence >= 0.9
        and validation["status"] in {"PASSED", "WARNINGS"}
        and not validation["unprocessed_row_numbers"]
        and bool(numeric)
        and all(spec.column_mapping.get(key) is not None for key in numeric)
        and (
            all(validation["control_comparisons"].get(key, {}).get("passed", False) for key in numeric)
            or (validation["row_balance"]["checked"] > 0 and validation["row_balance"]["failed"] == 0
                and not any(not item["passed"] for item in validation["control_comparisons"].values()))
        )
    )


class ProcessingCancelled(Exception):
    pass


def _failure_details(error, stage: str | None, validation=None, spec=None) -> dict:
    code = error.code if isinstance(error, AppError) else "PROCESSING_ERROR"
    if isinstance(error, AppError):
        reason = error.message
    elif isinstance(error, FileNotFoundError):
        reason = "Исходный файл не найден"
    elif isinstance(error, PermissionError):
        reason = "Нет доступа к исходному или результирующему файлу"
    elif isinstance(error, OSError):
        reason = "Не удалось прочитать или сохранить файл"
    elif isinstance(error, ValueError):
        reason = "Данные файла имеют неподдерживаемый формат"
    else:
        reason = "Внутренняя ошибка приложения на этапе обработки"
    recommendations = {
        "STRUCTURE_AMBIGUOUS": "Уточните в комментарии строки шапки и номера колонок счёта и денежных показателей.",
        "STRUCTURE_NOT_RECOGNIZED": "Укажите строки шапки, начало данных и назначение основных колонок.",
        "INVALID_COLUMN_MAPPING": "Проверьте номера колонок в комментарии и запустите повторную обработку.",
        "INVALID_LLM_STRUCTURE": "Уточните структуру таблицы вручную: строки шапки, данных и номера колонок.",
        "INVALID_EXPLICIT_LAYOUT": "Исправьте указанные поля комментария. Пример: «Данные: 8–201», «Итоги: 202», «Шапка: отсутствует».",
        "INVALID_LLM_SPEC": "Уточните спорные колонки и границы данных, затем повторите обработку.",
        "LLM_UNAVAILABLE": "Проверьте подключение к модели на странице настроек или повторите позже.",
        "LLM_TIMEOUT": "Проверьте загрузку сервера модели и общий бюджет ожидания LLM в настройках; затем повторите обработку.",
        "INVALID_LLM_RESPONSE": "Проверьте настройки ответа и режим thinking модели на сервере LLM, затем повторите обработку.",
        "LLM_RESPONSE_TRUNCATED": "Увеличьте лимит токенов ответа для этой операции на странице настроек LLM и повторите обработку.",
        "PROCESSING_ERROR": "Повторите обработку. Если ошибка сохранится, передайте администратору служебное мемо.",
    }
    details: list[str] = []
    validation = validation or {}
    labels = {column.key: column.label for column in spec.target_columns} if spec else {}
    for key, count in list((validation.get("missing_required") or {}).items())[:5]:
        details.append(f"Поле «{labels.get(key, key)}»: пустых значений — {count}")
    numeric_errors = validation.get("numeric_errors") or []
    if numeric_errors:
        rows = sorted({item.get("row") for item in numeric_errors if item.get("row") is not None})[:10]
        details.append(f"Не удалось распознать числа в строках: {', '.join(map(str, rows))}")
    unprocessed = validation.get("unprocessed_row_numbers") or []
    if unprocessed:
        details.append(f"Не обработаны строки: {', '.join(map(str, unprocessed[:10]))}")
    balance = validation.get("row_balance") or {}
    if balance.get("failed"):
        rows = [str(item.get("row")) for item in balance.get("examples", [])[:10]]
        details.append(f"Нарушено равенство остатков и оборотов: {balance['failed']}; строки: {', '.join(rows)}")
    failed_controls = [key for key, item in (validation.get("control_comparisons") or {}).items()
                       if not item.get("passed", False)]
    if failed_controls:
        details.append("Не сошлись итоговые суммы: " + ", ".join(labels.get(key, key) for key in failed_controls))
    if not details and not isinstance(error, AppError):
        details.append(f"Тип технической ошибки: {type(error).__name__}")
    return {"stage": stage, "code": code, "reason": reason, "details": details,
            "recommendation": recommendations.get(
                code, "Уточните структуру файла в комментарии и повторите обработку.")}


def _set_stage(transformation_id: str, stage: ProcessingStage) -> None:
    with connection() as db:
        changed = db.execute(
            """
            UPDATE transformations SET status = 'RUNNING', current_stage = ?, updated_at = ?
            WHERE id = ? AND status IN ('QUEUED', 'RUNNING')
            """,
            (stage.value, utc_now(), transformation_id),
        )
        if not changed.rowcount:
            status = db.execute("SELECT status FROM transformations WHERE id = ?", (transformation_id,)).fetchone()
            if status and status["status"] == "CANCELLED":
                raise ProcessingCancelled
            raise RuntimeError("Преобразование не находится в активном состоянии")


def _without_legacy_subaccount(spec: TransformationSpec) -> TransformationSpec:
    if not any(column.key == "subaccount" for column in spec.target_columns):
        return spec
    normalized = spec.model_copy(deep=True)
    detail_column = normalized.column_mapping.pop("subaccount", None)
    if detail_column is not None:
        normalized.column_mapping["_account_detail"] = detail_column
    normalized.target_columns = [column for column in normalized.target_columns if column.key != "subaccount"]
    normalized.row_rules.inherit_fields = [key for key in normalized.row_rules.inherit_fields if key != "subaccount"]
    normalized.value_overrides = [item for item in normalized.value_overrides if item.target_field != "subaccount"]
    return normalized


def process_transformation(
    transformation_id: str,
    feedback: str | None = None,
    run_id: str | None = None,
    accepted_spec: TransformationSpec | None = None,
) -> None:
    with connection() as db:
        transformation = db.execute("SELECT * FROM transformations WHERE id = ?", (transformation_id,)).fetchone()
        if not transformation:
            return
        source_path = Path(transformation["source_path"])
        data_type = DataType(transformation["data_type"])
        template_id = transformation["template_id"]
        user_id = transformation["user_id"]
        previous_version = transformation["current_version"]
        if transformation["status"] == "CANCELLED":
            return

    run_id = run_id or create_processing_run(transformation_id, previous_version + 1, feedback)
    set_run_status(run_id, "RUNNING")
    record_run_event(
        run_id,
        transformation_id,
        ProcessingStage.QUEUED.value,
        "FILE_OPENED",
        {
            "source_filename": transformation["source_filename"],
            "size_bytes": source_path.stat().st_size,
            "data_type": data_type.value,
            "template_id": template_id,
            "target_version": previous_version + 1,
        },
    )
    llm_used = False
    structure_recovered = False
    memory_used = False
    diagnostic_spec: TransformationSpec | None = None
    diagnostic_validation: dict | None = None
    document_profile = profile_for(data_type, template_id)

    field_sources: dict[str, RecognitionSource] = {}
    recognition_default: RecognitionSource = "RULES"
    header_source: RecognitionSource = "RULES"
    footer_source: RecognitionSource = "RULES"
    service_rows: list[int] = []

    def apply_plan_recognition(plan, default_source: RecognitionSource) -> None:
        nonlocal field_sources, recognition_default, header_source, footer_source, service_rows
        recognition_default = default_source
        field_sources = dict(getattr(plan, "field_sources", {}))
        for key, column in plan.spec.column_mapping.items():
            if column is not None:
                field_sources.setdefault(key, default_source)
        header_source = getattr(plan, "header_source", default_source)
        footer_source = getattr(plan, "footer_source", "RULES")
        service_rows = list(getattr(plan, "service_rows", []))

    def record_recognized_structure(
        spec: TransformationSpec,
        stage: ProcessingStage = ProcessingStage.PLANNING,
    ) -> None:
        record_run_event(
            run_id,
            transformation_id,
            stage.value,
            "STRUCTURE_RECOGNIZED",
            report_from_spec(
                spec,
                field_sources=field_sources,
                default_source=recognition_default,
                header_source=header_source,
                footer_source=footer_source,
                service_rows=service_rows,
            ),
        )

    def remember(outcome, reason, spec=None, validation=None, row_count=0):
        if not settings.document_memory_enabled or not document_profile.memory_enabled:
            return
        try:
            card = capture_card(source_path, spec, validation, row_count) if spec else None
            case = MemoryCase(owner_id=user_id, transformation_id=transformation_id, run_id=run_id,
                              outcome="unsupported" if spec and card is None else outcome, reason=reason)
            saved = MemoryStore().save_case(case, card)
            record_run_event(run_id, transformation_id, ProcessingStage.PLANNING.value,
                             "MEMORY_CASE_SAVED", {"case_id": str(case.id), "outcome": case.outcome,
                             "card_id": str(saved.id) if saved else None,
                             "revision": saved.revision if saved else None})
        except Exception as error:
            record_run_event(run_id, transformation_id, ProcessingStage.PLANNING.value,
                             "MEMORY_UNAVAILABLE", {"operation": "save", "error_type": type(error).__name__},
                             level="WARNING")

    extra_warnings: list[str] = []
    result_path: Path | None = None
    try:
        _set_stage(transformation_id, ProcessingStage.PROFILING)
        profile = profile_workbook(source_path)
        record_run_event(
            run_id,
            transformation_id,
            ProcessingStage.PROFILING.value,
            "PROFILE_CREATED",
            {
                "sheet_count": profile["sheet_count"],
                "sheets": [
                    {
                        "name": sheet["name"],
                        "min_row": sheet["min_row"],
                        "max_row": sheet["max_row"],
                        "max_column": sheet["max_column"],
                        "nonempty_rows": sheet["nonempty_rows"],
                        "nonempty_cells": sheet["nonempty_cells"],
                        "formulas": sheet["formulas"],
                        "merged_ranges": sheet["merged_ranges"],
                    }
                    for sheet in profile["sheets"]
                ],
            },
        )
        record_run_event(
            run_id,
            transformation_id,
            ProcessingStage.PROFILING.value,
            "SOURCE_SAMPLE",
            {
                "sheets": [
                    {"name": sheet["name"], "rows": sheet["sample_rows"][:8]}
                    for sheet in profile["sheets"]
                ]
            },
        )

        _set_stage(transformation_id, ProcessingStage.PLANNING)
        record_run_event(run_id, transformation_id, ProcessingStage.PLANNING.value,
                         "DOCUMENT_PROFILE_SELECTED", document_profile.public())
        planner = LLMPlanner()
        trace = lambda event_type, details: record_run_event(
            run_id, transformation_id, ProcessingStage.PLANNING.value, event_type, details,
            level="ERROR" if "FAILED" in event_type or "REJECTED" in event_type else "INFO",
        )

        def recover_structure(selection):
            try:
                return planner.recognize_structure(source_path, data_type, template_id, selection,
                                                   trace=trace, feedback=feedback)
            except AppError as error:
                if error.code.startswith(("LLM_", "INVALID_LLM_")):
                    raise
                trace("STRUCTURE_UNRESOLVED", {"code": error.code, "message": error.message})
                raise AppError(422, "Не удалось подтвердить структуру. Уточните строки шапки, "
                               "денежные колонки и назначение неподписанных строк. " + error.message,
                               "STRUCTURE_AMBIGUOUS") from error

        try:
            instructions = parse_feedback(feedback)
            explicit_mapping = instructions.mapping or None
            selection = (plan_from_explicit_mapping(source_path, data_type, template_id, explicit_mapping, instructions)
                         if data_type == DataType.TRIAL_BALANCE and complete_mapping(explicit_mapping)
                         else document_profile.plan(source_path, template_id, trace=trace)) if accepted_spec is None else None
            if accepted_spec is not None:
                from backend.app.services.accept_errors import assess_for_acceptance
                accepted = assess_for_acceptance(source_path, accepted_spec, data_type)
                selection = PlanSelection([accepted], True, 100)
                extra_warnings.append("Результат загружен с ошибками по решению пользователя")
                trace("ERRORS_ACCEPTED", {"spec": accepted.spec.model_dump(mode="json")})
        except AppError as error:
            if error.code != "STRUCTURE_NOT_RECOGNIZED":
                raise
            selection = PlanSelection([], False, 0)
        record_run_event(run_id, transformation_id, ProcessingStage.PLANNING.value,
                         "HYPOTHESES_EVALUATED", selection.summary())
        if (settings.document_memory_enabled and document_profile.memory_enabled
                and not selection.confident and not feedback):
            try:
                remembered = memory_plan(source_path, user_id, template_id, trace)
                if remembered:
                    selection = PlanSelection([remembered], True, 100)
                    memory_used = True
            except Exception as error:
                trace("MEMORY_UNAVAILABLE", {"operation": "search", "error_type": type(error).__name__})
        if instructions.has_bounds and not complete_mapping(explicit_mapping):
            if not selection.candidates:
                raise AppError(422, "Границы заданы, но колонки не распознаны. Заполните назначения всех колонок в шаблоне.", "INVALID_EXPLICIT_LAYOUT")
            source = SourceStructure(source_path)
            try:
                constrained = []
                for candidate in selection.candidates:
                    spec = candidate.spec.model_copy(deep=True)
                    spec.column_mapping.update(instructions.mapping)
                    spec = apply_bounds(spec, instructions, source.values[spec.sheet_name])
                    constrained.append(source.evaluate(spec, data_type, preserve_bounds=True))
                selection = PlanSelection(constrained, selection.confident, selection.margin)
            finally:
                source.close()
        recovered_initially = not selection.candidates
        if recovered_initially:
            recovered = recover_structure(selection)
            selection = PlanSelection([recovered], True, 100)
            llm_used = bool(getattr(recovered, "llm_used", True))
            structure_recovered = True
        explicit_mapping = explicit_column_feedback(feedback)
        baseline: TransformationSpec
        baseline = selection.best.spec.model_copy(deep=True)
        plan_default: RecognitionSource = (
            "MEMORY" if memory_used else "LLM" if structure_recovered and llm_used else "RULES"
        )
        apply_plan_recognition(selection.best, plan_default)
        if previous_version and feedback and not recovered_initially:
            with connection() as db:
                latest = db.execute(
                    "SELECT spec_json FROM transformation_versions WHERE transformation_id = ? AND version = ?",
                    (transformation_id, previous_version),
                ).fetchone()
            previous_spec = (TransformationSpec.model_validate(json_load(latest["spec_json"], {}))
                             if latest else baseline)
            previous_spec = _without_legacy_subaccount(previous_spec)
            if explicit_mapping:
                baseline.value_overrides = [
                    item for item in previous_spec.value_overrides
                    if item.target_field in baseline.column_mapping and item.target_field not in explicit_mapping
                ]
            else:
                baseline = previous_spec
        if instructions.has_bounds:
            source = SourceStructure(source_path)
            try:
                baseline = apply_bounds(baseline, instructions, source.values[baseline.sheet_name])
            finally:
                source.close()
            if instructions.header_set or instructions.data:
                header_source = "USER"
            footer_source = "USER"
        record_run_event(
            run_id,
            transformation_id,
            ProcessingStage.PLANNING.value,
            "BASELINE_SPEC_CREATED",
            {"spec": baseline.model_dump(mode="json")},
        )
        record_recognized_structure(baseline)
        baseline_field_sources = dict(field_sources)
        baseline_recognition_default = recognition_default
        baseline_header_source = header_source
        baseline_footer_source = footer_source
        baseline_service_rows = list(service_rows)
        checked_plans = [getattr(selection.best, "checked_result", None)]
        baseline_parsed, baseline_validation, baseline_reused = reuse_or_parse(
            document_profile, source_path, baseline, checked_plans)
        baseline_check = CheckedResult(baseline, baseline_parsed, baseline_validation)
        diagnostic_spec = baseline
        diagnostic_validation = baseline_validation
        reliable_baseline = selection.confident and (
            baseline_is_reliable(baseline, baseline_validation) or data_type == DataType.JOURNAL
        )
        record_run_event(
            run_id, transformation_id, ProcessingStage.PLANNING.value,
            "BASELINE_CHECKED",
            {"reliable": reliable_baseline, "validation": baseline_validation, "reused": baseline_reused},
        )

        spec = baseline
        explicit_applied = False
        if explicit_mapping or instructions.has_bounds:
            sheet = next(item for item in profile["sheets"] if item["name"] == baseline.sheet_name)
            if any(key not in baseline.column_mapping or column > sheet["max_column"]
                   for key, column in (explicit_mapping or {}).items()):
                raise AppError(422, "Указанная колонка или поле отсутствует в таблице", "INVALID_COLUMN_MAPPING")
            spec = baseline.model_copy(deep=True)
            spec.column_mapping.update(explicit_mapping or {})
            for key in explicit_mapping or {}:
                field_sources[key] = "USER"
            spec.value_overrides = [item for item in spec.value_overrides if item.target_field not in (explicit_mapping or {})]
            explicit_applied = True
            record_run_event(run_id, transformation_id, ProcessingStage.PLANNING.value,
                             "USER_MAPPING_APPLIED", {"mapping": explicit_mapping, "scope": "all_rows"})
        elif feedback and not recovered_initially and (previous_version or any(c.eligible for c in selection.candidates)):
            spec = planner.refine(profile, baseline, data_type, feedback, trace=trace, source_path=source_path)
            for key, column in spec.column_mapping.items():
                if column is not None and column != baseline.column_mapping.get(key):
                    field_sources[key] = "LLM"
            llm_used = True
        elif reliable_baseline or recovered_initially or accepted_spec is not None:
            if not llm_used:
                record_run_event(
                    run_id, transformation_id, ProcessingStage.PLANNING.value, "LLM_SKIPPED",
                    {"reason": ("Пользователь принял арифметические расхождения; применены сохранённые правила"
                                if accepted_spec is not None else
                                "Выбран однозначный вариант по структуре, значениям и контрольным соотношениям"),
                     "independent_totals_available": baseline_validation["independent_totals_available"]},
                )
        elif not any(c.eligible for c in selection.candidates):
            recovered = recover_structure(selection)
            spec = recovered.spec
            llm_used = bool(getattr(recovered, "llm_used", True))
            checked_plans.append(getattr(recovered, "checked_result", None))
            structure_recovered = True
            apply_plan_recognition(recovered, "LLM" if llm_used else "RULES")
        else:
            try:
                chosen = planner.choose_candidate(selection, trace=trace, source_path=source_path)
                spec = chosen.spec
                apply_plan_recognition(chosen, "RULES")
                checked_plans.append(getattr(chosen, "checked_result", None))
                llm_used = True
            except AppError as error:
                if error.code.startswith(("LLM_", "INVALID_LLM_")):
                    raise
                record_run_event(
                    run_id,
                    transformation_id,
                    ProcessingStage.PLANNING.value,
                    "STRUCTURE_UNRESOLVED",
                    {"code": error.code, "message": error.message},
                    level="WARNING",
                )
                raise AppError(422, "Структура неоднозначна или не прошла проверки. "
                               "Уточните источник поля, например: «Брать счета из колонки C». "
                               + error.message, "STRUCTURE_AMBIGUOUS") from error

        _set_stage(transformation_id, ProcessingStage.TRANSFORMING)
        parsed, candidate_validation, candidate_reused = reuse_or_parse(
            document_profile, source_path, spec, [baseline_check, *checked_plans])
        record_run_event(run_id, transformation_id, ProcessingStage.TRANSFORMING.value,
                         "CANDIDATE_VALIDATED", {"spec": spec.model_dump(mode="json"),
                                                 "validation": candidate_validation, "reused": candidate_reused})
        diagnostic_spec = spec
        diagnostic_validation = candidate_validation
        bad_candidate = (
            not parsed.rows
            or candidate_validation["status"] == "FAILED"
            or bool(parsed.unprocessed_rows)
        )
        if (llm_used or explicit_applied) and bad_candidate and feedback:
            raise AppError(422, "Исправления не прошли проверку данных и итоговых сумм", "INVALID_LLM_SPEC")
        if explicit_applied or (llm_used and feedback):
            source = SourceStructure(source_path)
            try:
                assessment = source.evaluate(spec, data_type, preserve_bounds=instructions.has_bounds)
            finally:
                source.close()
            if (assessment.evidence["identity_ratio"] < 1 or assessment.evidence["constant_zero_identity"]
                    or assessment.evidence["missing_formula_cache"]):
                raise AppError(422, "Указанный источник не содержит достоверных значений поля", "INVALID_COLUMN_MAPPING")
            if assessment.evidence["numeric_coverage"] < 1:
                raise AppError(422, "Не определены все денежные колонки; уточнение только счёта недостаточно", "INVALID_COLUMN_MAPPING")
            if assessment.validation["row_balance"]["failed"]:
                raise AppError(422, "После исправления нарушено равенство начального остатка, оборотов и конечного остатка",
                               "INVALID_LLM_SPEC")
        if llm_used and bad_candidate:
            if not selection.best.eligible:
                raise AppError(422, "Нет проверенной структуры для безопасного преобразования", "STRUCTURE_AMBIGUOUS")
            spec = baseline
            parsed = baseline_parsed
            llm_used = False
            field_sources = dict(baseline_field_sources)
            recognition_default = baseline_recognition_default
            header_source = baseline_header_source
            footer_source = baseline_footer_source
            service_rows = list(baseline_service_rows)
            extra_warnings.append("Спецификация LLM не прошла проверку результата; использована базовая спецификация")
            record_run_event(
                run_id,
                transformation_id,
                ProcessingStage.TRANSFORMING.value,
                "LLM_RESULT_FALLBACK",
                {"reason": "Результат LLM не прошёл проверку", "validation": candidate_validation},
                level="WARNING",
            )
            candidate_validation = baseline_validation
        record_recognized_structure(spec, ProcessingStage.TRANSFORMING)
        record_run_event(
            run_id,
            transformation_id,
            ProcessingStage.TRANSFORMING.value,
            "FINAL_SPEC_APPLIED",
            {"spec": spec.model_dump(mode="json"), "llm_used": llm_used},
        )
        record_run_event(
            run_id,
            transformation_id,
            ProcessingStage.TRANSFORMING.value,
            "PARSING_COMPLETED",
            {
                "processed_rows": len(parsed.rows),
                "skipped_rows": parsed.skipped_rows[:200],
                "unprocessed_rows": parsed.unprocessed_rows[:200],
                "numeric_errors": parsed.numeric_errors[:100],
                "declared_totals": parsed.declared_totals,
                "control_rows": parsed.control_rows,
            },
        )

        _set_stage(transformation_id, ProcessingStage.VALIDATING)
        validation = deepcopy(candidate_validation)
        diagnostic_spec = spec
        diagnostic_validation = validation
        validation["warnings"] = list(dict.fromkeys(validation["warnings"] + extra_warnings))
        validation["accepted_with_errors"] = accepted_spec is not None
        if accepted_spec is not None:
            validation["status"] = "FAILED"
        record_run_event(
            run_id,
            transformation_id,
            ProcessingStage.VALIDATING.value,
            "VALIDATION_COMPLETED",
            validation,
            level="WARNING" if validation["status"] in {"WARNINGS", "FAILED"} else "INFO",
        )

        _set_stage(transformation_id, ProcessingStage.EXPORTING)
        version = previous_version + 1
        result_path = source_path.parent / f"result-v{version}.xlsx"
        export_result(result_path, parsed.rows, spec, validation)
        _set_stage(transformation_id, ProcessingStage.EXPORTING)
        record_run_event(
            run_id,
            transformation_id,
            ProcessingStage.EXPORTING.value,
            "RESULT_EXPORTED",
            {
                "filename": result_path.name,
                "size_bytes": result_path.stat().st_size,
                "version": version,
            },
        )
        preview = [{key: value for key, value in row.items() if key != "_source_refs"} for row in parsed.rows[:100]]

        with connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT status FROM transformations WHERE id = ?", (transformation_id,)).fetchone()
            if not state or state["status"] == "CANCELLED":
                raise ProcessingCancelled
            if state["status"] not in ("QUEUED", "RUNNING"):
                raise RuntimeError(f"Недопустимый статус перед сохранением результата: {state['status']}")
            db.execute(
                """
                INSERT INTO transformation_versions
                  (transformation_id, version, feedback, profile_json, spec_json, result_path,
                   preview_json, validation_json, processed_rows, unprocessed_rows, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transformation_id,
                    version,
                    feedback,
                    json_dump(profile),
                    spec.model_dump_json(),
                    str(result_path),
                    json_dump(preview),
                    json_dump(validation),
                    len(parsed.rows),
                    len(parsed.unprocessed_rows),
                    utc_now(),
                ),
            )
            db.execute(
                """
                UPDATE transformations
                SET status = 'COMPLETED', current_stage = 'COMPLETED', processed_rows = ?,
                    unprocessed_rows = ?, control_status = ?, controls_json = ?, warnings_json = ?,
                    error_message = NULL, current_version = ?, llm_used = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    len(parsed.rows),
                    len(parsed.unprocessed_rows),
                    validation["status"],
                    json_dump({
                        "calculated_sums": validation["calculated_sums"],
                        "control_comparisons": validation["control_comparisons"],
                        "missing_required": validation["missing_required"],
                        "row_balance": validation["row_balance"],
                        "independent_totals_available": validation["independent_totals_available"],
                        "duplicate_accounts": validation["duplicate_accounts"],
                        "row_classification": validation["row_classification"],
                        "ambiguous_account_rows": validation["ambiguous_account_rows"],
                        "aggregation_available": validation["aggregation_available"],
                        "aggregation": validation["aggregation"],
                        "control_rows": validation["control_rows"],
                        "scope_excluded_rows": validation["scope_excluded_rows"],
                    }),
                    json_dump(validation["warnings"]),
                    version,
                    int(llm_used),
                    utc_now(),
                    transformation_id,
                ),
            )
        record_audit(
            "TRANSFORMATION_COMPLETED",
            user_id=user_id,
            entity_type="TRANSFORMATION",
            entity_id=transformation_id,
            details={"version": version, "processedRows": len(parsed.rows), "llmUsed": llm_used},
        )
        record_run_event(
            run_id,
            transformation_id,
            ProcessingStage.COMPLETED.value,
            "PROCESSING_COMPLETED",
            {
                "version": version,
                "processed_rows": len(parsed.rows),
                "unprocessed_rows": len(parsed.unprocessed_rows),
                "llm_used": llm_used,
            },
        )
        set_run_status(run_id, "COMPLETED")
        nontrivial_deterministic = (
            spec.row_rules.account_tree and not spec.row_rules.classify_account_rows
        ) or bool(validation.get("scope_excluded_rows"))
        if (llm_used or feedback or nontrivial_deterministic) and not memory_used and accepted_spec is None:
            reason = (
                "Сложный детерминированный подход: дерево кодов или отдельная область сверки"
                if nontrivial_deterministic and not (llm_used or feedback)
                else "Решение после LLM или уточнения пользователя"
            )
            remember("resolved", reason, spec, validation, len(parsed.rows))
    except ProcessingCancelled:
        if result_path and result_path.is_file():
            result_path.unlink()
        set_run_status(run_id, "CANCELLED")
        return
    except Exception as error:
        with connection() as db:
            active = db.execute(
                "SELECT status, current_stage FROM transformations WHERE id = ?", (transformation_id,),
            ).fetchone()
            stage = active["current_stage"] if active else None
            failure_details = _failure_details(error, stage, diagnostic_validation, diagnostic_spec)
            message = failure_details["reason"]
            failed = db.execute(
                """
                UPDATE transformations SET status = 'FAILED', current_stage = 'FAILED',
                    error_message = ?, updated_at = ?
                WHERE id = ? AND status IN ('QUEUED', 'RUNNING')
                """,
                (message, utc_now(), transformation_id),
            )
            state = db.execute("SELECT status FROM transformations WHERE id = ?", (transformation_id,)).fetchone()
        if not failed.rowcount and state and state["status"] == "CANCELLED":
            if result_path and result_path.is_file():
                result_path.unlink()
            set_run_status(run_id, "CANCELLED")
            return
        record_audit(
            "TRANSFORMATION_FAILED",
            user_id=user_id,
            entity_type="TRANSFORMATION",
            entity_id=transformation_id,
            outcome="FAILURE",
            details={"message": message},
        )
        record_run_event(
            run_id,
            transformation_id,
            ProcessingStage.FAILED.value,
            "PROCESSING_FAILED",
            {**failure_details, "message": message, "error_type": type(error).__name__,
             "technical_message": str(error)[:1000]},
            level="ERROR",
        )
        set_run_status(run_id, "FAILED")
        remember("failed", error.code if isinstance(error, AppError) else type(error).__name__)
