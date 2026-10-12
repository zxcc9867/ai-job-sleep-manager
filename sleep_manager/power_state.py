"""Persisted power-action outcomes, separate from inferred AI job state."""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PowerActionState:
    phase: str = 'idle'
    reason: str = ''
    message: str = ''
    attempts: int = 0
    retry_at: float = 0
    request_started: bool = False
    at: float = 0
    run_id: str = ''

    def payload(self):
        return asdict(self)

    @classmethod
    def restore(cls, payload):
        fields = cls.__dataclass_fields__
        return cls(**{key: value for key, value in payload.items() if key in fields})


class PreflightTransientError(RuntimeError):
    """A known, retryable fault before any Windows transition call."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class PowerActionFailure(RuntimeError):
    def __init__(self, reason, message, *, request_started=False):
        self.reason = reason
        self.request_started = request_started
        super().__init__(message)


def action_message(state, now):
    if state.phase == 'deferred':
        seconds = max(0, int(state.retry_at-now+.999))
        return f'종료 준비를 다시 확인합니다 · {seconds}초 후 재확인 ({state.attempts}/10)'
    if state.phase == 'requested':
        return 'Windows에 종료를 요청했습니다 · 완료 여부는 아직 확인되지 않았습니다.'
    if state.phase in ('submitting', 'uncertain'):
        return '종료 요청 전달 여부를 확인해야 합니다 · 반복 요청을 막기 위해 자동 관리가 중지됐습니다.'
    if state.phase == 'simulated':
        return '관찰 모드에서 정상 종료 요청을 모의 실행했습니다 · 실제 종료하지 않았습니다.'
    if state.phase == 'failed':
        reasons = {'shutdown_privilege': '종료 권한을 확인해 주세요.',
                   'retry_limit': '재확인 10회가 끝났습니다. 연결과 저장소를 확인해 주세요.',
                   'storage': '설정과 상태를 저장하지 못했습니다. 저장소를 확인해 주세요.'}
        return '자동 관리가 중지됐습니다 · '+reasons.get(state.reason, '진단에서 오류를 확인해 주세요.')
    return ''
