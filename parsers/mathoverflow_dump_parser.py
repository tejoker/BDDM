"""
MathOverflow dump parser (uses Stack Exchange parser as base)
Download from: https://archive.org/download/stackexchange
File: mathoverflow.net.7z
"""

from .stackexchange_dump_parser import StackExchangeDumpParser


class MathOverflowDumpParser(StackExchangeDumpParser):
    """Parser for MathOverflow dumps (same format as Stack Exchange)"""

    def __init__(self, dump_dir: str, skip_ids=None, min_score: int = 3):
        super().__init__(dump_dir, skip_ids, min_score)
        self.source_name = 'mathoverflow'

    def _parse_question(self, elem):
        """Parse question with MathOverflow-specific handling"""
        item = super()._parse_question(elem)
        if item:
            # Update source and ID prefix
            item['source'] = 'mathoverflow'
            item['id'] = item['id'].replace('se_', 'mo_')
            item['url'] = item['url'].replace('math.stackexchange.com', 'mathoverflow.net')
        return item
