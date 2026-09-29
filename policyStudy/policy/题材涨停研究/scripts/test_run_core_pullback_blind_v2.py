"""Isolated synthetic v2 runner tests: no models, private maps or outcomes."""
from copy import deepcopy
import fcntl
import json
from pathlib import Path

import pytest

import build_core_blind_v2_packets as builder
import run_core_pullback_blind_v2 as subject
from test_run_core_pullback_blind import packet


def answer_for(packet):
    return {
        'schema_version': 'core_pullback_blind_result.v2', 'sample_id': packet['sample_id'],
        'as_of': {key: packet['decision'][key] for key in ('day', 'bar_end')},
        'observations': {key: {'value': value, 'evidence_ids': ['current_first_m15']}
                         for key, value in subject.expected_observations(packet).items()},
        'checks': {key: {'value': 'unknown', 'evidence_ids': [], 'reason': '缺少确认依据'}
                   for key in subject.CHECKS},
        'assessment': 'wait_more', 'main_counterevidence': {'evidence_ids': ['ANCHOR'], 'reason': '只见历史背景'},
        'next_confirmation': {'evidence_ids': ['current_last_m15'], 'condition': '等待后续结构确认'},
        'invalidation': {'evidence_ids': ['current_last_m15'], 'condition': '后续低点跌破已观察低点'},
        'limitations': ['仅供语法/流程合成测试，非事实引用审计样本'],
    }


@pytest.fixture
def environment(tmp_path, packet, monkeypatch):
    source = tmp_path / 'public_packets'; source.mkdir()
    root = tmp_path / 'v2'
    for i in range(1, 13):
        item = deepcopy(packet); item['sample_id'] = f'S{i:02}'
        (source / f'S{i:02}.json').write_bytes(subject.canonical(item))
    builder.run(source, root)
    (root / 'FACT_AUDIT_RUBRIC.md').write_text('Synthetic predeclared audit rubric.')
    prompt = tmp_path / 'prompt.txt'; prompt.write_text('Packet:\n{{PACKET_JSON}}')
    monkeypatch.setattr(subject, 'PROMPT', prompt)
    metadata = {'model': 'MiniMax-M3', 'transport': 'direct_http', 'tools_enabled': False,
                'history_message_count': 0}
    monkeypatch.setattr(subject.transport, 'configuration_public', lambda: deepcopy(metadata))
    def payload(prompt, max_tokens):
        return {'model': metadata['model'], 'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': max_tokens, 'stream': False}
    monkeypatch.setattr(subject.transport, 'request_payload', payload)
    calls = []
    def responder(prompt, timeout, max_tokens):
        packet = json.loads(prompt.split('Packet:\n', 1)[1]); calls.append(packet['sample_id'])
        text = json.dumps(answer_for(packet), ensure_ascii=False)
        return {'raw_response_bytes': subject.canonical({'model': 'MiniMax-M3', 'content': [{'type': 'text', 'text': text}]}),
                'raw_text': text, 'request_body_bytes': subject.canonical(payload(prompt, max_tokens)),
                'response_model': 'MiniMax-M3', 'usage': {'input_tokens': 10, 'output_tokens': 20},
                'elapsed': .01, 'stop_reason': 'end_turn'}
    monkeypatch.setattr(subject.transport, 'call_model', responder)
    return root, calls, responder


@pytest.mark.parametrize('text', ['{"a":1,"a":2}', '{"x":{"a":1,"a":2}}',
                                  '{"x":NaN}', '{"x":Infinity}', '[] []'])
def test_strict_json_rejects_ambiguous_or_nonfinite_text(text):
    with pytest.raises(ValueError): subject.strict_json(text)


def test_full_run_freezes_all_artifacts_and_resume_does_not_call(environment):
    root, calls, _ = environment
    frozen = subject.run(root)
    assert calls == [f'S{i:02}' for i in range(1, 13)]
    assert frozen['full_denominator'] == 12
    assert all(x['status'] == 'valid' for x in frozen['results'].values())
    protocol = json.loads((root / 'RUN_PROTOCOL.json').read_text())
    assert all(protocol[k] for k in ['builder_sha256', 'derive_sha256', 'rubric_sha256', 'packet_manifest_sha256'])
    assert protocol['attempts_per_sample'] == 1
    for sid, record in frozen['results'].items():
        assert record['protocol_sha256'] == frozen['protocol_sha256']
        assert record['input_sha256'] == protocol['packet_sha256'][sid]
        for name in ['request.json', 'actual_request.json', 'STARTED.json', 'raw_response.json', 'raw_text.txt', 'parsed.json', 'RESULT_MANIFEST.json']:
            assert f'runs/minimax/{sid}/{name}' in frozen['file_sha256']
    assert {'FACT_AUDIT_RUBRIC.md', 'PACKET_MANIFEST.json'} <= set(frozen['file_sha256'])
    audit = json.loads((root / 'OBSERVATION_AUDIT.json').read_text())
    assert audit['observation_denominator'] == 72 and sum(audit['counts'].values()) == 72
    assert subject.run(root) == frozen and len(calls) == 12
    (root / 'runs/minimax/S01/raw_text.txt').write_text('modified')
    with pytest.raises(ValueError, match='changed or missing'): subject.run(root)
    assert len(calls) == 12


def test_wrong_atomic_value_is_valid_schema_and_counted_mismatch(environment, monkeypatch):
    root, calls, responder = environment
    def wrong(*args, **kwargs):
        response = responder(*args, **kwargs)
        parsed = json.loads(response['raw_text'])
        old = parsed['observations']['first_low_relation']['value']
        parsed['observations']['first_low_relation']['value'] = 'below' if old != 'below' else 'above'
        response['raw_text'] = json.dumps(parsed)
        response['raw_response_bytes'] = subject.canonical({'model': 'MiniMax-M3', 'content': [{'type': 'text', 'text': response['raw_text']}]})
        return response
    monkeypatch.setattr(subject.transport, 'call_model', wrong)
    frozen = subject.run(root)
    assert all(x['status'] == 'valid' for x in frozen['results'].values())
    audit = json.loads((root / 'OBSERVATION_AUDIT.json').read_text())
    assert audit['counts']['mismatch'] == 12 and audit['observation_denominator'] == 72
    assert len(calls) == 12


def test_duplicate_model_keys_fail_once_and_invalid_raw_is_sealed(environment, monkeypatch):
    root, calls, responder = environment
    def duplicate(*args, **kwargs):
        response = responder(*args, **kwargs)
        response['raw_text'] = response['raw_text'][:-1] + ',"assessment":"wait_more"}'
        response['raw_response_bytes'] = subject.canonical({'model': 'MiniMax-M3', 'content': [{'type': 'text', 'text': response['raw_text']}]})
        return response
    monkeypatch.setattr(subject.transport, 'call_model', duplicate)
    frozen = subject.run(root)
    assert len(calls) == 12
    assert all(r['status'] == 'invalid_output' and 'duplicate' in r['validation_error'] for r in frozen['results'].values())
    assert not list((root / 'runs').rglob('parsed.json'))
    for sid in frozen['results']:
        assert f'runs/minimax/{sid}/raw_text.txt' in frozen['file_sha256']
    assert json.loads((root / 'OBSERVATION_AUDIT.json').read_text())['counts'] == {'invalid_or_missing': 72}
    subject.run(root)
    assert len(calls) == 12


def interrupt_first_call(environment, monkeypatch):
    root, _, _ = environment
    attempted = []
    def interrupted(prompt, **kwargs):
        attempted.append(prompt)
        assert (root / 'runs/minimax/S01/STARTED.json').is_file()
        raise KeyboardInterrupt('synthetic crash after call started')
    monkeypatch.setattr(subject.transport, 'call_model', interrupted)
    with pytest.raises(KeyboardInterrupt): subject.run(root)
    assert len(attempted) == 1


def test_interrupted_call_is_never_resent_on_resume(environment, monkeypatch):
    root, calls, responder = environment
    interrupt_first_call(environment, monkeypatch)
    monkeypatch.setattr(subject.transport, 'call_model', responder)
    frozen = subject.run(root)
    assert calls == [f'S{i:02}' for i in range(2, 13)]
    assert frozen['results']['S01']['status'] == 'interrupted_unknown'
    assert frozen['results']['S01']['missing_raw_response']
    assert 'runs/minimax/S01/RESULT_MANIFEST.json' in frozen['file_sha256']


@pytest.mark.parametrize('field', ['input_sha256', 'protocol_sha256', 'sample_id', 'request_sha256'])
def test_started_checkpoint_binding_cannot_be_reused_for_other_input(environment, monkeypatch, field):
    root, calls, responder = environment
    interrupt_first_call(environment, monkeypatch)
    path = root / 'runs/minimax/S01/STARTED.json'; value = json.loads(path.read_text())
    value[field] = 'changed'; subject.save(path, value)
    monkeypatch.setattr(subject.transport, 'call_model', responder)
    with pytest.raises(ValueError, match='started'): subject.run(root)
    assert not calls


def test_finished_record_binding_checked_before_resume(environment, monkeypatch):
    root, calls, responder = environment
    def one_then_interrupt(prompt, **kwargs):
        if len(calls): raise KeyboardInterrupt()
        return responder(prompt, **kwargs)
    monkeypatch.setattr(subject.transport, 'call_model', one_then_interrupt)
    with pytest.raises(KeyboardInterrupt): subject.run(root)
    path = root / 'runs/minimax/S01/RESULT_MANIFEST.json'; record = json.loads(path.read_text())
    record['protocol_sha256'] = 'changed'; subject.save(path, record)
    monkeypatch.setattr(subject.transport, 'call_model', responder)
    with pytest.raises(ValueError, match='recorded sample'): subject.run(root)
    assert calls == ['S01']


def test_ambiguous_orphan_artifacts_do_not_trigger_a_call(environment):
    root, calls, _ = environment
    target = root / 'runs/minimax/S01'; target.mkdir(parents=True)
    (target / 'raw_text.txt').write_text('completion without recorded request')
    with pytest.raises(ValueError, match='without started'): subject.run(root)
    assert not calls


def test_transport_failure_is_terminal_and_missing_response_explicit(environment, monkeypatch):
    root, calls, _ = environment
    def failed(*args, **kwargs):
        calls.append('network_attempt'); raise subject.transport.ModelTransportError('synthetic network failure')
    monkeypatch.setattr(subject.transport, 'call_model', failed)
    frozen = subject.run(root)
    assert len(calls) == 12
    assert all(r['status'] == 'transport_error' and r['missing_raw_response'] for r in frozen['results'].values())
    assert all(f'runs/minimax/{sid}/RESULT_MANIFEST.json' in frozen['file_sha256'] for sid in frozen['results'])
    subject.run(root)
    assert len(calls) == 12


def test_file_lock_prevents_duplicate_process(environment):
    root, calls, _ = environment
    with (root / '.run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError): subject.run(root)
    assert not calls


def test_protocol_changes_before_resume_are_rejected(environment):
    root, calls, _ = environment
    subject.run(root)
    (root / 'FACT_AUDIT_RUBRIC.md').write_text('changed after answers')
    with pytest.raises(ValueError, match='protocol changed'): subject.run(root)
    assert len(calls) == 12


@pytest.mark.parametrize('mismatch', ['request_body_bytes', 'response_model'])
def test_actual_request_or_response_model_drift_is_invalid_and_preserved(environment, monkeypatch, mismatch):
    root, calls, responder = environment
    def changed(*args, **kwargs):
        response = responder(*args, **kwargs)
        response[mismatch] = b'{"unexpected":"actual outbound body"}' if mismatch == 'request_body_bytes' else 'DifferentModel'
        return response
    monkeypatch.setattr(subject.transport, 'call_model', changed)
    frozen = subject.run(root)
    assert len(calls) == 12
    assert all(r['status'] == 'invalid_output' and 'transport/model' in r['validation_error'] for r in frozen['results'].values())
    assert 'runs/minimax/S01/actual_request.json' in frozen['file_sha256']
    if mismatch == 'request_body_bytes':
        assert (root / 'runs/minimax/S01/actual_request.json').read_bytes() != (root / 'runs/minimax/S01/request.json').read_bytes()


def test_hidden_history_ids_cannot_be_direct_output_evidence(environment):
    root, _, _ = environment
    packet = json.loads((root / 'packets/S01.json').read_text())
    result = answer_for(packet)
    result['observations']['first_low_relation']['evidence_ids'] = ['M_D-5_0945']
    with pytest.raises(ValueError, match='invalid or hidden'): subject.validate_result(result, packet)


def test_missing_or_invalid_packet_manifest_stops_before_calls(environment):
    root, calls, _ = environment
    path = root / 'PACKET_MANIFEST.json'; manifest = json.loads(path.read_text())
    manifest['derive_code_sha256'] = 'unrecorded new formula'; subject.save(path, manifest)
    with pytest.raises(ValueError, match='frozen development builder'): subject.run(root)
    assert not calls
