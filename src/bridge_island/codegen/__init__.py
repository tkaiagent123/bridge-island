"""Build-time code generation: YAML mapping spec to a generated Python module (G5)."""

from bridge_island.codegen.generate import (
    CodegenError,
    Dictionary,
    dictionary_for_spec,
    generate,
    generate_file,
    load_module,
)

__all__ = [
    "CodegenError",
    "Dictionary",
    "dictionary_for_spec",
    "generate",
    "generate_file",
    "load_module",
]
