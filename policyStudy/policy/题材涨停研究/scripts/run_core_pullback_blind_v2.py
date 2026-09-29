"""Versioned development rerun: one call per sample, fact fidelity, no outcomes."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path

import core_blind_model_transport as transport
import build_core_blind_v2_packets as builder
import derive_core_blind_facts as derivation
from run_core_pullback_blind import CHECKS, canonical
from build_core_blind_v2_packets import validate_compact_packet

ROOT = Path(__file__).resolve().parents[4]
DEFAULT = ROOT / 'outputs/core_pullback_blind_v2_2025'
PROMPT = Path(__file__).resolve().parents[1] / 'prompts/core_pullback_blind_v2.txt'
OBS = ('first_low_relation', 'session_low_relation', 'last_close_vs_prior4',
       'last_bar_sameclock_volume_multiple', 'cumulative_sameclock_volume_multiple', 'opening_gap_direction')
MAX_TOKENS = 3072


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(canonical(value)); tmp.replace(path)


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError('nonfinite JSON constant')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)


def validate_result(result, packet):
    expected = {'schema_version', 'sample_id', 'as_of', 'observations', 'checks', 'assessment',
                'main_counterevidence', 'next_confirmation', 'invalidation', 'limitations'}
    if not isinstance(result, dict) or set(result) != expected:
        raise ValueError('wrong result fields')
    if (result['schema_version'] != 'core_pullback_blind_result.v2' or result['sample_id'] != packet['sample_id']
            or result['as_of'] != {k: packet['decision'][k] for k in ('day', 'bar_end')}):
        raise ValueError('wrong identity/cutoff')
    allowed = {x['id'] for key in ('daily_bars', 'm15_bars', 'facts') for x in packet[key]} | {'ANCHOR', 'THEME_D-1'}
    def refs(obj, required=True):
        value = obj.get('evidence_ids')
        if (not isinstance(value, list) or any(not isinstance(x, str) or x not in allowed for x in value)
                or (required and not value)):
            raise ValueError('invalid or hidden evidence IDs')
    if not isinstance(result['observations'], dict) or set(result['observations']) != set(OBS):
        raise ValueError('wrong observation fields')
    for key, obj in result['observations'].items():
        if not isinstance(obj, dict) or set(obj) != {'value', 'evidence_ids'}:
            raise ValueError('wrong observation object')
        refs(obj)
        value = obj['value']
        if key.endswith('_multiple'):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value < 0):
                raise ValueError('invalid volume multiple')
        else:
            options = {'above', 'below', 'equal', 'unknown'} if key.endswith('_relation') else (
                {'above', 'not_above', 'unknown'} if key == 'last_close_vs_prior4' else {'up', 'down', 'flat', 'unknown'})
            if not isinstance(value, str) or value not in options: raise ValueError('invalid observation enum')
    if not isinstance(result['checks'], dict) or set(result['checks']) != set(CHECKS):
        raise ValueError('wrong static checks')
    for obj in result['checks'].values():
        if (not isinstance(obj, dict) or set(obj) != {'value', 'evidence_ids', 'reason'}
                or obj['value'] not in ('yes', 'no', 'unknown') or not isinstance(obj['reason'], str) or not obj['reason'].strip()):
            raise ValueError('invalid static check')
        refs(obj, required=obj['value'] != 'unknown')
    for key, wording in [('main_counterevidence', 'reason'), ('next_confirmation', 'condition'), ('invalidation', 'condition')]:
        obj = result[key]
        if not isinstance(obj, dict) or set(obj) != {'evidence_ids', wording} or not isinstance(obj[wording], str) or not obj[wording].strip():
            raise ValueError('invalid explanatory field')
        refs(obj)
    if result['assessment'] not in ('support_research', 'wait_more', 'avoid_now'):
        raise ValueError('invalid assessment')
    if not isinstance(result['limitations'], list) or any(not isinstance(x, str) for x in result['limitations']):
        raise ValueError('invalid limitations')


def expected_observations(packet):
    facts = {x['id']: x for x in packet['facts']}
    def val(key): return facts[key]['value']
    a = val('first_m15_low_vs_previous_day_low'); b = val('session_low_vs_previous_day_low')
    c = val('close_vs_prior_4_observed_m15_high')
    first = val('current_first_m15'); previous = val('previous_completed_day')
    opening = first.get('open') if first else None; close = previous.get('close') if previous else None
    gap = 'unknown' if opening is None or close is None else 'up' if opening > close else 'down' if opening < close else 'flat'
    return dict(zip(OBS, [a['relation'] if a else 'unknown', b['relation'] if b else 'unknown',
        'above' if c and c['close_above_prior_high'] else 'not_above' if c else 'unknown',
        val('last_m15_volume_vs_five_prior_same_clock_median'), val('matched_clock_cumulative_volume_ratio'), gap]))


def audit_observations(result, packet):
    expected = expected_observations(packet)
    rows = []
    for key in OBS:
        truth = expected[key]
        reported = result['observations'][key]['value'] if result else None
        matches = result is not None and (abs(reported-truth) <= .000050000001
            if isinstance(truth, (int, float)) and isinstance(reported, (int, float))
            and not isinstance(reported, bool) else reported == truth)
        label = 'invalid_or_missing' if result is None else 'correct_unknown' if matches and truth in (None, 'unknown') else 'correct_known' if matches else 'mismatch'
        rows.append({'id': key, 'expected': truth, 'reported': reported, 'classification': label})
    return rows


def files_under(root):
    return {str(p.relative_to(root)): digest(p.read_bytes()) for p in sorted(root.rglob('*'))
            if p.is_file() and not p.name.endswith('.tmp')}


def verify_hashes(root, hashes):
    if not isinstance(hashes, dict): raise ValueError('missing artifact hashes')
    for name, value in hashes.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('artifact path leaves run directory')
        target = root / relative
        if not target.is_file() or digest(target.read_bytes()) != value:
            raise ValueError('changed or missing recorded artifact: ' + name)


def run(directory=DEFAULT):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.run.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock.seek(0); lock.truncate()
        lock.write(json.dumps({'pid': os.getpid(), 'locked_at': now()})); lock.flush()
        return run_locked(directory)


def run_locked(directory):
    paths = sorted((directory / 'packets').glob('S*.json'))
    if {p.stem for p in paths} != {f'S{i:02}' for i in range(1, 13)}: raise ValueError('need all 12 original development samples')
    packets = {p.stem: strict_json(p.read_text()) for p in paths}
    for sid, p in packets.items():
        validate_compact_packet(p)
        if p['sample_id'] != sid: raise ValueError('packet filename/identity differs')
    packet_manifest_path = directory / 'PACKET_MANIFEST.json'
    packet_manifest = strict_json(packet_manifest_path.read_text())
    builder_hash = digest(Path(builder.__file__).read_bytes())
    derive_hash = digest(Path(derivation.__file__).read_bytes())
    if (packet_manifest.get('samples') != 12 or packet_manifest.get('independent_holdout') is not False
            or packet_manifest.get('builder_sha256') != builder_hash
            or packet_manifest.get('derive_code_sha256') != derive_hash):
        raise ValueError('packet manifest is not from the frozen development builder')
    entries = packet_manifest.get('packets', [])
    if len(entries) != 12 or {x.get('sample_id') for x in entries} != set(packets):
        raise ValueError('packet manifest cohort differs')
    for entry in entries:
        sid = entry['sample_id']
        if (entry.get('packet_file') != f'packets/{sid}.json'
                or entry.get('input_sha256') != digest((directory / 'packets' / f'{sid}.json').read_bytes())
                or entry['input_sha256'] != digest(canonical(packets[sid]))):
            raise ValueError('packet manifest/input differs')
    metadata = transport.configuration_public()
    protocol = {'version': 'v2_development', 'model': metadata, 'script_sha256': digest(Path(__file__).read_bytes()),
        'transport_sha256': digest(Path(transport.__file__).read_bytes()), 'prompt_sha256': digest(PROMPT.read_bytes()),
        'builder_sha256': builder_hash, 'derive_sha256': derive_hash,
        'rubric_sha256': digest((directory / 'FACT_AUDIT_RUBRIC.md').read_bytes()),
        'packet_manifest_sha256': digest(packet_manifest_path.read_bytes()),
        'packet_sha256': {k: digest(canonical(v)) for k, v in packets.items()},
        'max_tokens': MAX_TOKENS, 'timeout': 180, 'attempts_per_sample': 1,
        'atom_absolute_tolerance': .00005, 'evaluation': '72 numerical observations separately from 72 subjective checks; no outcome scoring',
        'development_only': True, 'changes': ['same previously revealed development identities, no future fields in model input',
            'daily arrays become named objects', 'm15 displayed window reduced to D-1,D0', 'add deterministic prefix facts',
            'simpler schema, reject duplicate keys, single attempt only'], 'news': 'no Qwen requests or news changes'}
    protocol_path = directory / 'RUN_PROTOCOL.json'
    if protocol_path.exists() and strict_json(protocol_path.read_text()) != protocol: raise ValueError('protocol changed; use a new directory/version')
    if not protocol_path.exists(): save(protocol_path, protocol)
    protocol_hash = digest(canonical(protocol))
    seal = directory / 'FROZEN_OUTPUTS.json'
    if seal.exists():
        sealed = strict_json(seal.read_text())
        if sealed.get('protocol_sha256') != protocol_hash: raise ValueError('seal/protocol differs')
        verify_hashes(directory, sealed['file_sha256'])
        return sealed
    results = {}
    for sid, packet in packets.items():
        target = directory / 'runs/minimax' / sid; target.mkdir(parents=True, exist_ok=True)
        finished = target / 'RESULT_MANIFEST.json'
        binding = {'sample_id': sid, 'input_sha256': protocol['packet_sha256'][sid],
                   'protocol_sha256': protocol_hash}
        prompt = PROMPT.read_text().replace('{{PACKET_JSON}}', canonical(packet).decode())
        request = transport.request_payload(prompt, max_tokens=MAX_TOKENS)
        request_bytes = canonical(request)
        started = target / 'STARTED.json'
        if started.exists():
            checkpoint = strict_json(started.read_text())
            if any(checkpoint.get(key) != value for key, value in binding.items()):
                raise ValueError('started call input/protocol identity differs')
            if (checkpoint.get('request_sha256') != digest(request_bytes)
                    or not (target / 'request.json').exists()
                    or (target / 'request.json').read_bytes() != request_bytes):
                raise ValueError('started request differs from frozen input')
        if finished.exists():
            record = strict_json(finished.read_text())
            if not started.exists() or any(record.get(key) != value for key, value in binding.items()):
                raise ValueError('recorded sample input/protocol identity differs')
            verify_hashes(target, record['file_sha256'])
            current_files = files_under(target)
            current_files.pop('RESULT_MANIFEST.json', None)
            if current_files != record['file_sha256']: raise ValueError('unrecorded sample artifact')
            results[sid] = record; continue
        record = {**binding, 'status': 'interrupted_unknown'}
        if not started.exists():
            if files_under(target): raise ValueError('sample artifacts exist without started checkpoint; refusing possible duplicate call')
            if transport.configuration_public() != metadata: raise ValueError('model configuration changed')
            (target / 'request.json').write_bytes(request_bytes)
            save(started, {**binding, 'started_at': now(),
                 'request_sha256': digest(request_bytes), 'no_automatic_retry': True})
            try:
                response = transport.call_model(prompt, timeout=180, max_tokens=MAX_TOKENS)
                (target / 'actual_request.json').write_bytes(response['request_body_bytes'])
                (target / 'raw_response.json').write_bytes(response['raw_response_bytes'])
                (target / 'raw_text.txt').write_text(response['raw_text'])
                record.update({'response_model': response['response_model'], 'usage': response['usage'],
                    'elapsed_seconds': response['elapsed'], 'stop_reason': response['stop_reason']})
                if response['request_body_bytes'] != request_bytes or response['response_model'] != metadata['model']:
                    raise ValueError('transport/model differs from frozen request')
                parsed = strict_json(response['raw_text']); validate_result(parsed, packet)
                save(target / 'parsed.json', parsed)
                record.update({'status': 'valid', 'assessment': parsed['assessment']})
            except Exception as exc:
                record.update({'status': 'invalid_output' if (target / 'raw_text.txt').exists() else 'transport_error',
                    'error_type': type(exc).__name__, 'validation_error': str(exc)[:300] if isinstance(exc, (ValueError, json.JSONDecodeError)) else type(exc).__name__})
        else:
            record['reason'] = 'Existing started call without final record; service completion unknown, never resent.'
        record.update({'finished_at': now(), 'missing_raw_response': not (target / 'raw_response.json').exists(),
                       'file_sha256': files_under(target)})
        save(finished, record); results[sid] = record
        save(directory / 'batch_status.json', {'status': 'running', 'current': sid, 'completed': len(results),
             'counts': dict(Counter(x['status'] for x in results.values())), 'updated_at': now()})
        print(sid, record['status'], flush=True)
    observations = {}; counts = Counter()
    for sid, packet in packets.items():
        output = strict_json((directory / 'runs/minimax' / sid / 'parsed.json').read_text()) if results[sid]['status'] == 'valid' else None
        observations[sid] = audit_observations(output, packet)
        counts.update(x['classification'] for x in observations[sid])
    summary = {'full_sample_denominator': 12, 'observation_denominator': 72, 'counts': dict(counts),
               'model_status_counts': dict(Counter(x['status'] for x in results.values())), 'observations': observations,
               'limits': 'Reading/copying computed facts only; not static-structure accuracy, return prediction or independent holdout.'}
    save(directory / 'OBSERVATION_AUDIT.json', summary)
    save(directory / 'batch_status.json', {'status': 'finished', 'completed': 12, 'counts': summary['model_status_counts'], 'updated_at': now()})
    names = ['RUN_PROTOCOL.json', 'OBSERVATION_AUDIT.json', 'batch_status.json',
             'PACKET_MANIFEST.json', 'FACT_AUDIT_RUBRIC.md']
    file_hashes = {name: digest((directory/name).read_bytes()) for name in names}
    for sub in ['packets', 'runs']:
        file_hashes.update({f'{sub}/{k}': v for k, v in files_under(directory/sub).items()})
    frozen = {'frozen_at': now(), 'full_denominator': 12, 'protocol_sha256': protocol_hash,
              'results': results, 'file_sha256': file_hashes}
    save(seal, frozen)
    return frozen


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--directory', type=Path, default=DEFAULT)
    run(parser.parse_args().directory)
