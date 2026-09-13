"""Pure, clock-injected policy. No OS calls and no task contents."""
from dataclasses import dataclass, field
import math

@dataclass
class Job:
    provider: str
    session_id: str
    state: str = 'idle'
    turn_id: str = ''
    children: set = field(default_factory=set)
    background: bool = False
    unknown_children: dict = field(default_factory=dict)
    child_timestamps: dict = field(default_factory=dict)
    completed_children: set = field(default_factory=set)
    final_log_turn: str | None = None
    last_timestamp: float = 0
    unknown_since: float | None = None
    updated: float = 0

    @property
    def active(self):
        return self.state == 'running' or bool(self.children) or self.background

@dataclass(frozen=True)
class Decision:
    mode: str
    required: bool
    sleep_due: bool = False
    remaining: int | None = None
    running: int = 0
    waiting: int = 0
    unknown: int = 0

class Engine:
    def __init__(self, delay=600, recovery=1800, enabled=True):
        self.jobs = {}
        self.delay = self._delay(delay)
        self.recovery = recovery
        self.enabled = enabled
        self.seen_work = False
        self.baseline = None

    @staticmethod
    def _delay(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 60 <= value <= 7200:
            raise ValueError('대기 시간은 1~120분이어야 합니다.')
        return value

    def ingest(self, event, now):
        provider, session = event.get('provider'), event.get('session_id')
        if provider not in ('codex', 'claude') or not isinstance(session, str) or not session:
            raise ValueError('Invalid event identity')
        kind = event.get('kind')
        if kind not in {'start', 'running', 'waiting', 'complete', 'failed', 'cancelled', 'session_end', 'session_start', 'child_start', 'child_stop', 'unknown'}:
            raise ValueError('Invalid event kind')
        key = provider + ':' + session
        job = self.jobs.setdefault(key, Job(provider, session))
        timestamp = event.get('timestamp', 0)
        final_log = event.get('source') == 'codex-local-log' and kind in ('complete','cancelled') and bool(event.get('turn_id')) and event.get('turn_id') == job.turn_id
        child_event = kind in ('child_start','child_stop')
        child_id = event.get('task_id') or 'unidentified'
        last_timestamp = job.child_timestamps.get(child_id,0) if child_event else job.last_timestamp
        if timestamp and timestamp < last_timestamp and not final_log:
            return
        turn = event.get('turn_id', '')
        if kind not in ('start','child_start','child_stop','session_start') and turn and job.turn_id and turn != job.turn_id:
            return
        if kind == 'unknown' and event.get('source') == 'hook:Stop' and job.final_log_turn is not None and turn == job.final_log_turn:
            return
        if final_log:
            job.final_log_turn = turn
        if child_event:
            job.child_timestamps[child_id] = max(last_timestamp,timestamp)
        else:
            job.last_timestamp = max(job.last_timestamp, timestamp)
        job.updated = now
        if kind == 'session_start':
            return
        self.seen_work = True
        if kind in ('start', 'running'):
            job.state = 'running'
            job.final_log_turn = None
            if turn:
                job.turn_id = turn
            job.unknown_since = None
            self.baseline = None
        elif kind == 'child_start':
            child = event.get('task_id') or 'unidentified'
            job.completed_children.discard(child)
            job.children.add(child)
            job.unknown_children.pop(child,None)
            self.baseline = None
        elif kind == 'child_stop':
            child = event.get('task_id') or 'unidentified'
            job.children.discard(child)
            if event.get('metadata_complete') is False and child not in job.completed_children:
                job.unknown_children.setdefault(child,now)
                self.baseline = None
            else:
                job.completed_children.add(child)
                job.unknown_children.pop(child,None)
        elif kind == 'unknown':
            job.state = 'unknown'
            if job.unknown_since is None:
                job.unknown_since = now
            self.baseline = None
        else:
            job.state = 'waiting' if kind == 'waiting' else 'complete'
            job.unknown_since = None
        if kind == 'complete' and event.get('metadata_complete') is True and event.get('background_active') is False:
            job.completed_children.update(job.unknown_children)
            job.unknown_children.clear()
        if 'background_active' in event:
            job.background = bool(event['background_active'])

    def set_enabled(self, enabled, now):
        self.enabled = bool(enabled)
        self.baseline = None

    def set_delay(self, seconds, now):
        self.delay = self._delay(seconds)
        self.baseline = now if self.baseline is not None else None

    def on_resume(self, now):
        self.baseline = None

    def tick(self, now, idle_seconds):
        running = sum(j.active for j in self.jobs.values())
        waiting = sum(j.state == 'waiting' and not j.active for j in self.jobs.values())
        lost = [j for j in self.jobs.values() if j.state == 'unknown']
        lost_since = [j.unknown_since for j in lost] + [t for j in self.jobs.values() for t in j.unknown_children.values()]
        counts = dict(running=running, waiting=waiting, unknown=len(lost_since))
        if not self.enabled:
            self.baseline = None
            return Decision('paused', False, **counts)
        if running:
            self.baseline = None
            return Decision('running', True, **counts)
        if lost_since:
            self.baseline = None
            protected = any(now - t < self.recovery for t in lost_since)
            return Decision('unknown' if protected else 'fault', protected, **counts)
        if not self.seen_work:
            return Decision('idle', False, **counts)
        if self.baseline is None:
            self.baseline = now
        self.baseline = max(self.baseline, now - max(0, idle_seconds))
        remaining = max(0, math.ceil(self.baseline + self.delay - now))
        return Decision('countdown', True, remaining == 0, remaining, **counts)




