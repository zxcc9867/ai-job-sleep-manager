"""Display identities only; adding an identity does not add lifecycle detection."""
from dataclasses import dataclass


TERMINAL = (
    '1111111111111', '1000000000001', '1000000000001',
    '1010000000001', '1001000000001', '1000100000001',
    '1001000000001', '1010001111001', '1000000000001',
    '1000000000001', '1111111111111',
)
SPARK = (
    '0000001000000', '0100001000010', '0010001000100',
    '0001001001000', '0000101010000', '0000011100000',
    '1111111111111', '0000011100000', '0000101010000',
    '0001001001000', '0010001000100', '0100001000010',
    '0000001000000',
)
GENERIC = (
    '0000000000000', '0011100111110', '0100010001000',
    '0100010001000', '0111110001000', '0100010001000',
    '0100010111110', '0000000000000',
)


@dataclass(frozen=True)
class ProviderIdentity:
    name: str
    glyph: tuple
    light: str
    dark: str


# New display identities belong here. Detection still needs its own adapter.
PROVIDERS = {
    'codex': ProviderIdentity('Codex', TERMINAL, '#245dcb', '#9bbeff'),
    'claude': ProviderIdentity('Claude Code', SPARK, '#a84820', '#efb18e'),
}


def provider_identity(provider):
    key = str(provider or '').strip()
    if key in PROVIDERS:
        return PROVIDERS[key]
    name = ' '.join(''.join(c for c in key if c.isprintable()).split())[:40]
    return ProviderIdentity('AI · '+name if name else '알 수 없는 AI', GENERIC, '#536174', '#afbdcf')
