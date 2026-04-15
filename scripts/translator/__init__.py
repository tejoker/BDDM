"""Translator package — re-exports statement translation API."""
from translator._translate import (  # noqa: F401
    TranslationResult,
    translate_statement,
    generate_decomposition_stubs,
    _validate_signature,
)
