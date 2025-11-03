"""
Dump-based parsers for mathematical datasets
These parsers process downloaded data dumps instead of web scraping
"""

from .wikipedia_dump_parser import WikipediaDumpParser
from .stackexchange_dump_parser import StackExchangeDumpParser
from .mathoverflow_dump_parser import MathOverflowDumpParser
from .arxiv_kaggle_parser import ArxivKaggleParser
from .oeis_parser import OEISParser
from .proofpile_parser import ProofPileParser
from .lean_mathlib_parser import LeanMathlibParser
from .metamath_parser import MetamathParser
from .isabelle_afp_parser import IsabelleAFPParser
from .coq_parser import CoqParser
from .zbmath_parser import ZbMATHParser

__all__ = [
    'WikipediaDumpParser',
    'StackExchangeDumpParser',
    'MathOverflowDumpParser',
    'ArxivKaggleParser',
    'OEISParser',
    'ProofPileParser',
    'LeanMathlibParser',
    'MetamathParser',
    'IsabelleAFPParser',
    'CoqParser',
    'ZbMATHParser',
]
