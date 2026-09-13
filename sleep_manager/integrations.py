"""Metadata-only, conservative lifecycle adapters and explicit hook installation.

Stop hooks run before other hooks resolve. Even an empty Claude task registry is
only a completion candidate; callers must debounce and observe subsequent work.
Codex does not expose a background registry in the documented Stop payload.
PermissionRequest/Elicitation can auto-resolve, so only pending notifications
establish human waiting. No permissions or model output are modified here.
"""
from __future__ import annotations

import base64
import copy
import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import uuid
import time


_COMMON_EVENTS = ('SessionStart', 'SessionEnd', 'UserPromptSubmit', 'PreToolUse',
                  'PostToolUse', 'PermissionRequest', 'PreCompact', 'PostCompact',
                  'SubagentStart', 'SubagentStop', 'Stop')
_EVENTS = {'codex': _COMMON_EVENTS + ('Interrupt',),
           'claude': _COMMON_EVENTS + ('Notification', 'PostToolUseFailure',
                                       'StopFailure', 'Elicitation', 'ElicitationResult')}
_WAITING_NOTICES = {'permission_prompt', 'elicitation_dialog', 'elicitation_url_dialog',
                    'quota_auto_resume_stale'}
_RUNNING_NOTICES = {'elicitation_complete', 'elicitation_response', 'quota_auto_resume_fired'}


def _identifier(value):
    return value if isinstance(value, str) and 0 < len(value) <= 256 and not any(ord(c) < 32 for c in value) else ''


def normalize_hook(provider: str, payload: dict) -> dict | None:
    """Return safe state metadata, or None for irrelevant/invalid input.

    metadata_complete means this event carries its required evidence, not that
    every task in the session is observable. Missing evidence must fail closed.
    """
    if not isinstance(provider, str) or provider not in _EVENTS or not isinstance(payload, dict):
        return None
    session_id = _identifier(payload.get('session_id'))
    event = payload.get('hook_event_name')
    if not session_id or not isinstance(event, str) or event not in _EVENTS[provider]:
        return None
    kind = 'unknown'
    metadata_complete = False
    background_active = None
    task_id = ''
    if event == 'Notification':
        notice = payload.get('notification_type')
        if not isinstance(notice, str):
            return None
        if notice in _WAITING_NOTICES:
            kind, metadata_complete = 'waiting', True
        elif notice in _RUNNING_NOTICES:
            kind, metadata_complete = 'running', True
        else:
            return None
    elif event == 'UserPromptSubmit':
        kind, metadata_complete = 'start', True
    elif event in ('PreToolUse', 'PostToolUse', 'PostToolUseFailure', 'PreCompact', 'PostCompact', 'ElicitationResult'):
        kind, metadata_complete = 'running', True
    elif event == 'SessionStart':
        kind, metadata_complete = 'session_start', True
    elif event == 'SessionEnd':
        kind = 'session_end'
    elif event == 'Interrupt':
        kind = 'cancelled'
    elif event in ('SubagentStart', 'SubagentStop'):
        task_id = _identifier(payload.get('agent_id'))
        if task_id:
            kind = 'child_start' if event == 'SubagentStart' else 'child_stop'
            metadata_complete = event == 'SubagentStart'
            if event == 'SubagentStop' and payload.get('stop_hook_active') is not False:
                kind = 'unknown'
    elif event == 'Stop' and provider == 'claude':
        tasks, crons = payload.get('background_tasks'), payload.get('session_crons')
        registry_valid = (isinstance(tasks, list) and isinstance(crons, list)
                          and all(isinstance(item, dict) for item in tasks + crons))
        if registry_valid:
            background_active = bool(tasks)
            if tasks:
                kind, metadata_complete = 'running', True
            elif not crons and payload.get('stop_hook_active') is False:
                kind, metadata_complete = 'complete', True
    result = dict(provider=provider, session_id=session_id, kind=kind,
                  task_id=task_id, turn_id=_identifier(payload.get('turn_id')),
                  metadata_complete=metadata_complete, timestamp=time.time(), source='hook:'+event)
    if background_active is not None:
        result['background_active'] = background_active
    return result


def _handler(provider, command):
    argv = [*command, '--hook', provider]
    if provider == 'claude':
        return {'type': 'command', 'command': argv[0], 'args': argv[1:], 'timeout': 3}
    # Encoded PowerShell prevents interpolation of paths by either outer shell.
    # The outer invocation contains only fixed tokens and a base64 alphabet.
    script = '& ' + ' '.join("'" + arg.replace("'", "''") + "'" for arg in argv)
    script += '; exit $LASTEXITCODE'
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    windows = 'powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand ' + encoded
    return {'type': 'command', 'command': shlex.join(argv),
            'commandWindows': windows, 'timeout': 3}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key; original settings preserved')
        result[key] = value
    return result

def _validate_config(data, path):
    if not isinstance(data, dict):
        raise ValueError(f'{path}: expected a JSON object')
    hooks = data.get('hooks', {})
    if not isinstance(hooks, dict):
        raise ValueError(f'{path}: hooks must be an object')
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            raise ValueError(f'{path}: {event} must contain a list of matcher groups')
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get('hooks'), list):
                raise ValueError(f'{path}: invalid matcher group')
            if 'matcher' in group and not isinstance(group['matcher'], str):
                raise ValueError(f'{path}: matcher must be a string')
            for handler in group['hooks']:
                if not isinstance(handler, dict) or not isinstance(handler.get('type'), str):
                    raise ValueError(f'{path}: invalid hook handler')
                if handler['type'] == 'command':
                    if not isinstance(handler.get('command'), str):
                        raise ValueError(f'{path}: command must be a string')
                    if 'args' in handler and (not isinstance(handler['args'], list) or not all(isinstance(arg, str) for arg in handler['args'])):
                        raise ValueError(f'{path}: args must contain strings')


def _private_file(path, content):
    """Create exclusively, restrict access before writing any settings content."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if os.name == 'nt':
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            identity = subprocess.run(['whoami.exe', '/user', '/fo', 'csv', '/nh'],
                                      check=True, capture_output=True, text=True, creationflags=flags)
            sid = next(csv.reader(io.StringIO(identity.stdout)))[1]
            if not sid.startswith('S-1-'):
                raise OSError('Cannot establish backup owner identity')
            subprocess.run(['icacls.exe', str(path), '/inheritance:r', '/grant:r', f'*{sid}:(F)'],
                           check=True, capture_output=True, creationflags=flags)
        with os.fdopen(descriptor, 'wb') as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        path.unlink(missing_ok=True)
        raise


def install_hooks(home: Path, command: list[str], remove: bool = False) -> list[str]:
    """Merge/remove only exact handlers for this launch prefix; return backups.

    Both documents are validated before writes. Existing files are backed up
    privately, and each JSON replacement is atomic. Cross-file replacement is
    not a transaction: an I/O failure can leave one provider updated, with its
    backup available. Custom CODEX_HOME/CLAUDE_CONFIG_DIR require manual setup.
    """
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) and arg and '\0' not in arg and '\n' not in arg and '\r' not in arg for arg in command):
        raise ValueError('command must be a nonempty list of literal arguments')
    home = Path(home)
    changes = []
    for provider, relative in [('claude', '.claude/settings.json'), ('codex', '.codex/hooks.json')]:
        path = home / relative
        if path.is_symlink():
            raise ValueError(f'{path}: refusing to replace a symbolic link')
        original = path.read_bytes() if path.exists() else None
        try:
            data = json.loads(original.decode('utf-8-sig'), object_pairs_hook=_unique_object) if original is not None else {}
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f'{path}: invalid JSON; original preserved') from error
        _validate_config(data, path)
        updated = copy.deepcopy(data)
        handler = _handler(provider, command)
        hooks = updated.setdefault('hooks', {})
        for event in _EVENTS[provider]:
            groups = hooks.get(event, [])
            if remove:
                kept_groups = []
                for group in groups:
                    kept = [item for item in group['hooks'] if item != handler]
                    if kept == group['hooks']:
                        kept_groups.append(group)
                    elif kept:
                        kept_groups.append({**group, 'hooks': kept})
                if kept_groups:
                    hooks[event] = kept_groups
                elif event in hooks:
                    del hooks[event]
            elif not any(handler == item for group in groups for item in group['hooks']):
                hooks[event] = [*groups, {'hooks': [copy.deepcopy(handler)]}]
        if not hooks and 'hooks' not in data:
            updated.pop('hooks')
        if updated != data:
            changes.append((path, original, updated))
    backups = []
    # Back up every existing document before replacing either document.
    for path, original, _ in changes:
        path.parent.mkdir(parents=True, exist_ok=True)
        if original is not None:
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            backup = path.with_name(path.name + '.ai-job-sleep-manager.backup-' + stamp)
            _private_file(backup, original)
            backups.append(str(backup))
    for path, original, data in changes:
        if (path.read_bytes() if path.exists() else None) != original:
            raise OSError(f'{path}: settings changed concurrently; retry after reviewing')
        temporary = path.with_name(path.name + '.ai-job-sleep-manager-' + uuid.uuid4().hex + '.tmp')
        try:
            _private_file(temporary, (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return backups


