"""Shared semantic colors for the desktop view, including dialogs and states."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    bg: str
    card: str
    text: str
    muted: str
    accent: str
    border: str
    inset: str
    amber: str
    error: str
    success: str
    selected: str
    secondary_hover: str
    primary: str = '#245dcb'
    primary_hover: str = '#1d4fae'
    primary_text: str = '#ffffff'


PALETTES = {
    'light': Palette(bg='#f3f5f8', card='#ffffff', text='#172231', muted='#536174',
        accent='#245dcb', border='#d8dfe9', inset='#eef2f7', amber='#976212',
        error='#b23b43', success='#1b775e', selected='#e6eefc', secondary_hover='#e6eefc'),
    'dark': Palette(bg='#101722', card='#192331', text='#e8edf5', muted='#afbdcf',
        accent='#9bbeff', border='#37465a', inset='#223044', amber='#edc179',
        error='#ffa6aa', success='#82d7ba', selected='#31466c', secondary_hover='#293c57'),
}
THEME_LABELS = {'밝은 모드': 'light', '다크 모드': 'dark'}
