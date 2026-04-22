"""
Minimal i18n bootstrap for AxiDraw runtime messages.
"""

import gettext
import locale
import os

_ACTIVE_LANGUAGE = "en"


def _normalize_language(raw_value):
    if not raw_value:
        return "auto"
    value = str(raw_value).strip()
    if not value:
        return "auto"
    return value


def choose_language(options=None, params=None):
    """Resolve preferred language from env/options/config/system."""
    env_lang = _normalize_language(os.environ.get("AXIDRAW_LANG"))
    if env_lang != "auto":
        return env_lang

    if options is not None:
        opt_lang = _normalize_language(getattr(options, "language", None))
        if opt_lang != "auto":
            return opt_lang

    if params is not None:
        cfg_lang = _normalize_language(getattr(params, "language", None))
        if cfg_lang != "auto":
            return cfg_lang

    system_lang = locale.getdefaultlocale()[0] if locale.getdefaultlocale() else None
    if system_lang and system_lang.lower().startswith("zh"):
        return "zh_CN"
    return "en"


def init_gettext(options=None, params=None):
    """
    Initialize gettext translation with fallback.
    Keeps behavior stable when locale files are absent.
    """
    global _ACTIVE_LANGUAGE
    language = choose_language(options=options, params=params)
    _ACTIVE_LANGUAGE = language
    locale_root = os.path.join(os.path.dirname(__file__), "locale")
    translator = gettext.translation(
        "axidraw",
        localedir=locale_root,
        languages=[language],
        fallback=True)
    translator.install()
    return translator.gettext


def active_language():
    """Return active language code for diagnostics."""
    return _ACTIVE_LANGUAGE
