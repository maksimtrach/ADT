import json

from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.db.database import connection
from backend.app.services.correction_structure import correction_structure
from backend.app.services.processing_log import record_run_event
from backend.tests.test_reuse_and_llm_errors_api import prepare, upload


LAYOUT = {
    'sheet_name': 'ОСВ', 'header_start_row': 1, 'header_end_row': 4, 'data_start_row': 8,
    'column_mapping': {'account': 2, 'account_name': 3, 'opening_debit': 5,
                       'opening_credit': 6, 'turnover_debit': 7, 'turnover_credit': 8,
                       'closing_debit': None, 'closing_credit': None},
}


def response(layout, protocol='ollama'):
    message = {'content': json.dumps({'layout': layout})}
    payload = {'message': message} if protocol == 'ollama' else {'choices': [{'message': message}]}
    return {'event_type': 'LLM_RESPONSE', 'details_json': json.dumps({'payload': payload})}


def test_rejected_header_keeps_values_without_inventing_missing_bounds():
    result = correction_structure([response(LAYOUT)], 'trial_balance_flat_v1')
    assert result['header_end_row'] == 4
    assert result['data_start_row'] == 8
    assert result['data_end_row'] is None
    fields = {f['key']: f for f in result['fields']}
    assert fields['account']['column_letter'] == 'B'
    assert fields['closing_debit']['column'] is None


def test_footer_suggestion_merges_with_header_and_handles_openai():
    result = correction_structure([response(LAYOUT, 'openai'), response({
        'last_data_row': 201, 'control_rows': [202], 'service_rows': []})], 'trial_balance_flat_v1')
    assert result['data_start_row'] == 8
    assert result['data_end_row'] == 201
    assert result['control_rows'] == [202]


def test_bad_json_is_not_used_and_validation_supersedes_proposal():
    good = correction_structure([response(LAYOUT)], 'trial_balance_flat_v1')
    good['data_start_row'] = 9
    result = correction_structure([response(LAYOUT),
        {'event_type': 'STRUCTURE_RECOGNIZED', 'details_json': json.dumps(good)},
        {'event_type': 'LLM_RESPONSE', 'details_json': json.dumps({'payload': {'message': {'content': 'bad'}}})}],
        'trial_balance_flat_v1')
    assert result['data_start_row'] == 9


def test_existing_failed_run_exposes_suggestion_without_changing_recognized_structure(tmp_path, monkeypatch):
    path = prepare(tmp_path, monkeypatch)
    with TestClient(app) as client:
        tid = upload(client, path)
        before = client.get(f'/api/transformations/{tid}').json()['data']
        with connection() as db:
            run = db.execute('SELECT id FROM transformation_runs WHERE transformation_id=?', (tid,)).fetchone()
            db.execute("UPDATE transformations SET status='FAILED' WHERE id=?", (tid,))
        record_run_event(run['id'], tid, 'PLANNING', 'LLM_RESPONSE', json.loads(response(LAYOUT)['details_json']))
        detail = client.get(f'/api/transformations/{tid}').json()['data']
        assert detail['recognized_structure'] == before['recognized_structure']
        assert detail['correction_structure']['data_start_row'] == 8
        assert detail['correction_structure']['fields'][0]['column'] == 2
