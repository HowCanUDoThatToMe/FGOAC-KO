"""Plan two bounded first-start repairs without writing the installation.

prepare(target) returns a JSON-safe format-1 plan. Each change contains a fixed
relative path, before/after SHA-256 and base64 bytes (before=None means absent).
verify(target, plan) rechecks the plan immediately before a caller-owned locked
transaction. The caller owns backups, atomic publication, rollback and uninstall.
Never put the returned deck bytes in a public diagnostic report.
"""
import base64
import hashlib
import json
import ntpath
from pathlib import Path

from common import child, no_links

DECK = 'App/deck.json'
PLAYERS = 'Server/state/fgo-players.json'
MAX_DECK_BYTES = 4 * 1024 * 1024
RECOVERY_MARKERS = (
    'Server/state/account-import.pending.json',
    'Server/state/account-id-migration.pending.json',
    'Server/state/local-rooms/account-import.json',
)


def _fail(code):
    raise ValueError('[FIRST_START:' + code + '] 기존 설정을 보존했습니다. 정상 설정 또는 복구 기록을 확인해 주세요.')


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _present(path):
    # child/no_links has already refused dangling links and reparse points.
    return path.exists()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail('DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def _nonfinite(_value):
    _fail('NONFINITE_JSON')


def _value_spans(text):
    """Top-level value spans, retaining every unrelated original JSON byte."""
    decoder = json.JSONDecoder()
    index = len(text) - len(text.lstrip()) + 1  # opening object brace
    result = {}
    while True:
        while text[index].isspace():
            index += 1
        if text[index] == '}':
            return result
        key, index = decoder.raw_decode(text, index)
        while text[index].isspace():
            index += 1
        index += 1  # colon, already validated by the strict full parse
        while text[index].isspace():
            index += 1
        start = index
        _, index = decoder.raw_decode(text, index)
        result[key] = (start, index)
        while text[index].isspace():
            index += 1
        if text[index] == '}':
            return result
        index += 1  # comma


def _card_key(deck_path, cards_root, card):
    if not isinstance(card, str) or not card.strip() or '\x00' in card:
        _fail('INVALID_SELECTED_CARD')
    # Match the game's Windows GetFullPath/OrdinalIgnoreCase grouping without
    # accessing card files. Do not resolve symlinks or change saved path strings.
    for value in (cards_root, card):
        drive, tail = ntpath.splitdrive(value)
        if drive and not tail.startswith(('\\', '/')):
            _fail('DRIVE_RELATIVE_CARD')
    return ntpath.normcase(ntpath.normpath(ntpath.join(str(deck_path.parent), cards_root, card)))


def _deck_after(path, raw):
    if len(raw) > MAX_DECK_BYTES:
        _fail('DECK_TOO_LARGE')
    bom = raw.startswith(b'\xef\xbb\xbf')
    try:
        text = raw.decode('utf-8-sig')
        document = json.loads(text, object_pairs_hook=_unique, parse_constant=_nonfinite)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        _fail('INVALID_DECK_JSON')
    if not isinstance(document, dict):
        _fail('DECK_NOT_OBJECT')
    cards = document.get('SelectedCards')
    if not isinstance(cards, list):
        _fail('INVALID_SELECTED_CARDS')
    if len(cards) > 30:
        _fail('SELECTED_CARD_LIMIT')
    cards_root = document.get('CardsPath')
    if not isinstance(cards_root, str) or not cards_root.strip() or '\x00' in cards_root:
        _fail('INVALID_CARDS_PATH')
    keys = [_card_key(path, cards_root, card) for card in cards]
    copies = document.get('SelectedCardCopies')
    if copies is not None:
        if not isinstance(copies, list) or len(copies) != len(cards):
            _fail('COPY_COUNT_MISMATCH')
        seen = set()
        for key, copy in zip(keys, copies):
            if type(copy) is not int or not 1 <= copy <= 30 or (key, copy) in seen:
                _fail('INVALID_CARD_COPY')
            seen.add((key, copy))
        return None  # byte-exact no-op for every valid existing copy assignment
    counts = {}
    generated = []
    for key in keys:
        copy = counts.get(key, 0) + 1
        if copy > 30:
            _fail('TOO_MANY_IDENTICAL_CARDS')
        counts[key] = copy
        generated.append(copy)
    value = json.dumps(generated, separators=(',', ':'))
    spans = _value_spans(text)
    if 'SelectedCardCopies' in document:
        start, end = spans['SelectedCardCopies']
        output = text[:start] + value + text[end:]
    else:
        # Insert after the final property value, ahead of original whitespace.
        # Existing escapes, property order, numbers and non-ASCII text survive.
        index = max(end for _, end in spans.values()) if spans else text.index('{') + 1
        separator = ('\r\n' if '\r\n' in text else '\n') + '  ' if '\n' in text else ''
        addition = (',' if spans else '') + separator + '"SelectedCardCopies":' + value
        output = text[:index] + addition + text[index:]
    return (b'\xef\xbb\xbf' if bom else b'') + output.encode('utf-8')


def _clean_missing_players(target):
    for relative in RECOVERY_MARKERS:
        if _present(child(target, relative)):
            _fail('ACCOUNT_RECOVERY_PENDING')
    state = child(target, 'Server/state')
    if state.exists() and not state.is_dir():
        _fail('STATE_NOT_DIRECTORY')
    if state.exists():
        for item in state.iterdir():
            no_links(item)
            if item.name.casefold().startswith('fgo-players.json.'):
                _fail('ACCOUNT_RECOVERY_COPY_PRESENT')
    returns = child(target, 'Server/state/local-room-return')
    if returns.exists():
        if not returns.is_dir():
            _fail('ACCOUNT_RECOVERY_PENDING')
        for item in returns.iterdir():
            no_links(item)
            if item.suffix.casefold() == '.json':
                _fail('ACCOUNT_RECOVERY_PENDING')
    visitors = child(target, 'Server/state/room-visitors')
    if visitors.exists():
        if not visitors.is_dir():
            _fail('ACCOUNT_RECOVERY_PENDING')
        for folder in visitors.iterdir():
            no_links(folder)
            if folder.is_dir() and _present(child(target, folder.relative_to(target).as_posix() + '/migration.json')):
                _fail('ACCOUNT_RECOVERY_PENDING')


def _change(relative, before, after, reason):
    return dict(path=relative, reason=reason,
                before_sha256=None if before is None else _hash(before),
                before_base64=None if before is None else base64.b64encode(before).decode('ascii'),
                after_sha256=_hash(after), after_base64=base64.b64encode(after).decode('ascii'))


def prepare(target):
    """Read scoped inputs and return a no-write plan; never read player values."""
    target = Path(target).absolute()
    no_links(target)
    if not target.is_dir():
        _fail('TARGET_NOT_DIRECTORY')
    changes, skipped = [], []
    deck = child(target, DECK)
    if deck.exists():
        if not deck.is_file():
            _fail('DECK_NOT_FILE')
        with deck.open('rb') as stream:
            raw = stream.read(MAX_DECK_BYTES + 1)
        after = _deck_after(deck, raw)
        if after is None:
            skipped.append(dict(path=DECK, reason='valid-byte-exact'))
        else:
            changes.append(_change(DECK, raw, after, 'initialize-missing-card-copies'))
    else:
        skipped.append(dict(path=DECK, reason='missing-platform-defaults'))
    players = child(target, PLAYERS)
    if players.exists():
        if not players.is_file():
            _fail('PLAYERS_NOT_FILE')
        skipped.append(dict(path=PLAYERS, reason='existing-store-byte-exact-unread'))
    else:
        _clean_missing_players(target)
        changes.append(_change(PLAYERS, None, b'{}\n', 'initialize-clean-missing-account-store'))
    return dict(format=1, changes=changes, skipped=skipped)


def verify(target, plan):
    """Recheck under caller's maintenance lock immediately before publication."""
    if not isinstance(plan, dict) or plan.get('format') != 1:
        _fail('INVALID_PLAN')
    target = Path(target).absolute()
    no_links(target)
    if not target.is_dir():
        _fail('TARGET_NOT_DIRECTORY')
    seen = set()
    for row in plan.get('changes', []):
        relative = row.get('path')
        if relative not in (DECK, PLAYERS) or relative in seen:
            _fail('INVALID_PLAN')
        seen.add(relative)
        try:
            before = None if row['before_base64'] is None else base64.b64decode(row['before_base64'], validate=True)
            after = base64.b64decode(row['after_base64'], validate=True)
        except (KeyError, TypeError, ValueError):
            _fail('INVALID_PLAN')
        if ((None if before is None else _hash(before)) != row.get('before_sha256') or
                _hash(after) != row.get('after_sha256')):
            _fail('INVALID_PLAN')
        path = child(target, relative)
        if relative == PLAYERS:
            if before is not None or after != b'{}\n':
                _fail('INVALID_PLAN')
            _clean_missing_players(target)
        elif before is None or _deck_after(path, before) != after:
            _fail('INVALID_PLAN')
        if before is None:
            if path.exists():
                _fail('INPUT_CHANGED')
        else:
            if not path.is_file():
                _fail('INPUT_CHANGED')
            with path.open('rb') as stream:
                actual = stream.read(MAX_DECK_BYTES + 1)
            if _hash(actual) != row['before_sha256']:
                _fail('INPUT_CHANGED')
    return True
