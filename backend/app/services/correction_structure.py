"""Unverified model suggestions for editing only; never execution input."""
import json
from copy import deepcopy

from backend.app.services.templates import get_template
from backend.app.services.structure_report import structure_report


def correction_structure(events, template_id):
    result = None
    columns = get_template(template_id).columns
    allowed = {column.key for column in columns}
    for event in events:
        try:
            details = json.loads(event['details_json'])
            if event['event_type'] == 'STRUCTURE_RECOGNIZED':
                result = deepcopy(details)
                continue
            payload = details.get('payload', {})
            if 'message' in payload:
                content = payload['message'].get('content', '')
            else:
                content = payload.get('choices', [])[0]['message'].get('content', '')
            layout = json.loads(content).get('layout')
            if not isinstance(layout, dict):
                continue
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            continue
        # Keep nonsensical relationships (e.g. header overlapping data) visible.
        # Only scalar types/ranges needed to render safely are filtered here.
        def number(key):
            value = layout.get(key)
            return value if type(value) is int and 1 <= value <= 1048576 else None

        mapping = layout.get('column_mapping')
        if isinstance(mapping, dict):
            safe_mapping = {key: value if type(value) is int and 1 <= value <= 16384 else None
                            for key, value in mapping.items() if key in allowed}
            result = structure_report(
                target_columns=columns, column_mapping=safe_mapping,
                sheet_name=layout.get('sheet_name') if isinstance(layout.get('sheet_name'), str) else '',
                header_start_row=number('header_start_row'), header_end_row=number('header_end_row'),
                data_start_row=number('data_start_row'), data_end_row=number('data_end_row'),
                default_source='LLM', header_source='LLM',
            )
        if result is not None:
            end = number('last_data_row') or number('data_end_row')
            if end is not None:
                result['data_end_row'] = end
                result['footer_source'] = 'LLM'
            for key in ('control_rows', 'service_rows'):
                if isinstance(layout.get(key), list):
                    result[key] = [value for value in layout[key]
                                   if type(value) is int and 1 <= value <= 1048576]
    return result
