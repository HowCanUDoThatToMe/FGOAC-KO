"""Select translation deltas for the installed runtime, without executing it."""
import copy
from common import child

MODERN_ID = 'three-colors-psi-v1'
MODERN_FILES = ('App/fgoassets.dll', 'App/fgoroom.dll')


def documents(source):
    variants = source.get('runtime_variants', [])
    if not isinstance(variants, list) or len(variants) > 1:
        raise ValueError('Unsupported runtime variant catalogue')
    base = copy.deepcopy(source)
    base.pop('runtime_variants', None)
    if not variants:
        return [base]
    entries = {asset['name']: {} for asset in base['assets']}
    for row in base['files']:
        entries[row['asset']][row['entry']] = row['size']
    outputs = [base]
    for variant in variants:
        if variant.get('id') != MODERN_ID:
            raise ValueError('Unsupported runtime variant')
        modern = copy.deepcopy(base)
        modern['runtime_family'] = MODERN_ID
        modern['base_files'].update(variant['base_files'])
        overrides = {row['path']: row for row in variant['files']}
        if len(overrides) != len(variant['files']) or not set(overrides) <= {r['path'] for r in base['files']}:
            raise ValueError('Invalid variant output scope')
        modern['files'] = [copy.deepcopy(overrides.get(r['path'], r)) for r in base['files']]
        for row in variant['files']:
            if row['asset'] not in entries:
                raise ValueError('Unknown variant asset')
            existing = entries[row['asset']].get(row['entry'])
            if existing is not None and existing != row['size']:
                raise ValueError('Conflicting variant archive entry')
            entries[row['asset']][row['entry']] = row['size']
        outputs.append(modern)
    for doc in outputs:
        doc['archive_entries'] = entries
    return outputs


def select(source, target=None):
    choices = documents(source)
    if target is not None and any(child(target, p).exists() for p in MODERN_FILES):
        if len(choices) != 2 or not all(child(target, p).is_file() for p in MODERN_FILES):
            raise ValueError('Three Colors 실행 모듈이 불완전하거나 이 패치에서 지원되지 않습니다.')
        return choices[1]
    return choices[0]
