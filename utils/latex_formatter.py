"""
LaTeX Formatter
Converts scraped mathematical content into unified LaTeX format
"""

import re
from typing import Dict, List


class LaTeXFormatter:
    """Format mathematical content as LaTeX documents"""
    
    @staticmethod
    def format_stackexchange(item: Dict) -> str:
        """
        Convert Stack Exchange Q&A to LaTeX format
        
        Stack Exchange uses MathJax which is LaTeX-compatible
        """
        latex = []
        
        # Document header
        latex.append(r"\documentclass{article}")
        latex.append(r"\usepackage{amsmath, amssymb, amsthm}")
        latex.append(r"\usepackage[utf8]{inputenc}")
        latex.append(r"\title{" + LaTeXFormatter._escape_latex(item['title']) + "}")
        latex.append(r"\author{Stack Exchange}")
        latex.append(r"\date{}")
        latex.append(r"\begin{document}")
        latex.append(r"\maketitle")
        latex.append("")
        
        # Tags
        if item.get('tags'):
            tags_str = ", ".join(item['tags'])
            latex.append(r"\noindent\textbf{Tags:} " + tags_str)
            latex.append("")
        
        # Question
        latex.append(r"\section*{Question}")
        question_text = LaTeXFormatter._clean_html(item['question'])
        latex.append(question_text)
        latex.append("")
        
        # Answer
        if item.get('answer'):
            latex.append(r"\section*{Answer}")
            answer_text = LaTeXFormatter._clean_html(item['answer'])
            latex.append(answer_text)
            latex.append("")
        
        # Metadata
        latex.append(r"\vspace{1em}")
        latex.append(r"\noindent\textit{Score: " + str(item.get('score', 0)) + "}")
        
        latex.append(r"\end{document}")
        
        return "\n".join(latex)
    
    @staticmethod
    def format_proofwiki(item: Dict) -> str:
        """
        Convert ProofWiki theorem+proof to LaTeX format
        
        ProofWiki already uses LaTeX notation
        """
        latex = []
        
        # Document header
        latex.append(r"\documentclass{article}")
        latex.append(r"\usepackage{amsmath, amssymb, amsthm}")
        latex.append(r"\usepackage[utf8]{inputenc}")
        latex.append(r"\newtheorem{theorem}{Theorem}")
        latex.append(r"\title{" + LaTeXFormatter._escape_latex(item['title']) + "}")
        latex.append(r"\author{ProofWiki}")
        latex.append(r"\date{}")
        latex.append(r"\begin{document}")
        latex.append(r"\maketitle")
        latex.append("")
        
        # Tags
        if item.get('tags'):
            tags_str = ", ".join(item['tags'][:5])  # Limit to 5 tags
            latex.append(r"\noindent\textbf{Categories:} " + tags_str)
            latex.append("")
        
        # Theorem
        latex.append(r"\begin{theorem}")
        theorem_text = LaTeXFormatter._clean_proofwiki_latex(item['theorem'])
        latex.append(theorem_text)
        latex.append(r"\end{theorem}")
        latex.append("")
        
        # Proof
        latex.append(r"\begin{proof}")
        proof_text = LaTeXFormatter._clean_proofwiki_latex(item['proof'])
        if proof_text and proof_text.strip():
            latex.append(proof_text)
        else:
            latex.append("The proof is left as an exercise to the reader.")
        latex.append(r"\end{proof}")
        
        latex.append(r"\end{document}")
        
        return "\n".join(latex)
    
    @staticmethod
    def format_arxiv(item: Dict) -> str:
        """
        Convert ArXiv paper metadata to LaTeX format
        
        ArXiv abstracts often contain LaTeX
        """
        latex = []
        
        # Document header
        latex.append(r"\documentclass{article}")
        latex.append(r"\usepackage{amsmath, amssymb, amsthm}")
        latex.append(r"\usepackage[utf8]{inputenc}")
        latex.append(r"\usepackage{hyperref}")
        
        title = LaTeXFormatter._escape_latex(item['title'])
        latex.append(r"\title{" + title + "}")
        
        # Authors
        if item.get('authors'):
            authors_str = " \\and ".join([LaTeXFormatter._escape_latex(a) for a in item['authors'][:5]])
            latex.append(r"\author{" + authors_str + "}")
        
        latex.append(r"\date{" + item.get('published', '')[:10] + "}")
        latex.append(r"\begin{document}")
        latex.append(r"\maketitle")
        latex.append("")
        
        # ArXiv ID and categories
        if item.get('metadata', {}).get('arxiv_id'):
            arxiv_id = item['metadata']['arxiv_id']
            latex.append(r"\noindent\textbf{arXiv:} " + arxiv_id)
        
        if item.get('categories'):
            cats = ", ".join(item['categories'][:5])
            latex.append(r"\noindent\textbf{Categories:} " + cats)
        
        latex.append("")
        
        # Abstract
        latex.append(r"\begin{abstract}")
        abstract_text = LaTeXFormatter._clean_arxiv_abstract(item['abstract'])
        latex.append(abstract_text)
        latex.append(r"\end{abstract}")
        latex.append("")
        
        # PDF link
        if item.get('url'):
            latex.append(r"\noindent Full paper: \url{" + item['url'] + "}")
        
        latex.append(r"\end{document}")
        
        return "\n".join(latex)
    
    @staticmethod
    def _clean_html(text: str) -> str:
        """Remove HTML tags and convert to LaTeX"""
        if not text:
            return ""
        
        # Remove HTML tags but keep content
        text = re.sub(r'<code>(.*?)</code>', r'\\texttt{\1}', text)
        text = re.sub(r'<strong>(.*?)</strong>', r'\\textbf{\1}', text)
        text = re.sub(r'<em>(.*?)</em>', r'\\textit{\1}', text)
        text = re.sub(r'<[^>]+>', '', text)
        
        # HTML entities
        text = text.replace('&lt;', '<')
        text = text.replace('&gt;', '>')
        text = text.replace('&amp;', '&')
        text = text.replace('&quot;', '"')
        text = text.replace('&#39;', "'")
        
        # Clean up whitespace
        text = re.sub(r'\n\s*\n', '\n\n', text)
        text = text.strip()
        
        return text
    
    @staticmethod
    def _clean_proofwiki_latex(text: str) -> str:
        """Clean ProofWiki LaTeX (already mostly LaTeX)"""
        if not text:
            return ""
        
        # ProofWiki uses $ for inline math, already LaTeX
        # Just clean up some formatting
        text = text.strip()
        
        # Remove blacksquare if it's the only content
        if text == r'$\blacksquare$':
            return ""
        
        return text
    
    @staticmethod
    def _clean_arxiv_abstract(text: str) -> str:
        """Clean ArXiv abstract (already contains LaTeX)"""
        if not text:
            return ""
        
        # ArXiv abstracts use TeX notation
        # Replace \n with proper paragraph breaks
        text = text.strip()
        text = re.sub(r'\n\s*\n', '\n\n', text)
        
        return text
    
    @staticmethod
    def _escape_latex(text: str) -> str:
        """Escape special LaTeX characters in plain text"""
        if not text:
            return ""
        
        # Don't escape if already in math mode
        if '$' in text:
            return text
        
        # Escape special characters
        replacements = {
            '&': r'\&',
            '%': r'\%',
            '#': r'\#',
            '_': r'\_',
            '{': r'\{',
            '}': r'\}',
            '~': r'\textasciitilde{}',
            '^': r'\^{}',
            '\\': r'\textbackslash{}',
        }
        
        for char, replacement in replacements.items():
            text = text.replace(char, replacement)
        
        return text
    
    @staticmethod
    def format_item(item: Dict) -> str:
        """
        Auto-detect source and format accordingly
        
        Args:
            item: Dictionary with 'source' key
        
        Returns:
            LaTeX document as string
        """
        source = item.get('source', '')
        
        if source == 'stackexchange':
            return LaTeXFormatter.format_stackexchange(item)
        elif source == 'proofwiki':
            return LaTeXFormatter.format_proofwiki(item)
        elif source == 'arxiv':
            return LaTeXFormatter.format_arxiv(item)
        else:
            raise ValueError(f"Unknown source: {source}")
    
    @staticmethod
    def save_as_latex(item: Dict, output_path: str):
        """
        Save item as LaTeX file
        
        Args:
            item: Item dictionary
            output_path: Path to save .tex file
        """
        latex_content = LaTeXFormatter.format_item(item)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(latex_content)
    
    @staticmethod
    def format_batch_as_latex(items: List[Dict]) -> str:
        """
        Format multiple items into a single LaTeX document
        
        Args:
            items: List of item dictionaries
        
        Returns:
            Combined LaTeX document
        """
        latex = []
        
        # Document header
        latex.append(r"\documentclass{article}")
        latex.append(r"\usepackage{amsmath, amssymb, amsthm}")
        latex.append(r"\usepackage[utf8]{inputenc}")
        latex.append(r"\usepackage{hyperref}")
        latex.append(r"\newtheorem{theorem}{Theorem}")
        latex.append(r"\title{Mathematical Content Collection}")
        latex.append(r"\author{Compiled from Stack Exchange, ProofWiki, and ArXiv}")
        latex.append(r"\date{\today}")
        latex.append(r"\begin{document}")
        latex.append(r"\maketitle")
        latex.append(r"\tableofcontents")
        latex.append(r"\newpage")
        latex.append("")
        
        # Add each item as a section
        for i, item in enumerate(items, 1):
            source = item.get('source', 'unknown').title()
            title = LaTeXFormatter._escape_latex(item.get('title', f'Item {i}'))
            
            latex.append(f"\\section{{{title}}}")
            latex.append(f"\\textit{{Source: {source}}}")
            latex.append("")
            
            # Add content based on source
            if item.get('source') == 'stackexchange':
                if item.get('question'):
                    latex.append("\\subsection*{Question}")
                    latex.append(LaTeXFormatter._clean_html(item['question']))
                    latex.append("")
                if item.get('answer'):
                    latex.append("\\subsection*{Answer}")
                    latex.append(LaTeXFormatter._clean_html(item['answer']))
                    latex.append("")
            
            elif item.get('source') == 'proofwiki':
                if item.get('theorem'):
                    latex.append("\\begin{theorem}")
                    latex.append(LaTeXFormatter._clean_proofwiki_latex(item['theorem']))
                    latex.append("\\end{theorem}")
                    latex.append("")
                if item.get('proof'):
                    latex.append("\\begin{proof}")
                    proof = LaTeXFormatter._clean_proofwiki_latex(item['proof'])
                    latex.append(proof if proof else "Proof omitted.")
                    latex.append("\\end{proof}")
                    latex.append("")
            
            elif item.get('source') == 'arxiv':
                if item.get('authors'):
                    authors = ", ".join(item['authors'][:3])
                    latex.append(f"\\textbf{{Authors:}} {LaTeXFormatter._escape_latex(authors)}")
                    latex.append("")
                if item.get('abstract'):
                    latex.append("\\subsection*{Abstract}")
                    latex.append(LaTeXFormatter._clean_arxiv_abstract(item['abstract']))
                    latex.append("")
            
            latex.append("\\newpage")
            latex.append("")
        
        latex.append(r"\end{document}")
        
        return "\n".join(latex)


if __name__ == "__main__":
    # Test
    test_se = {
        'source': 'stackexchange',
        'title': 'Test Question',
        'question': 'What is $x^2 + y^2$?',
        'answer': 'This is a sum of squares.',
        'tags': ['algebra'],
        'score': 10
    }
    
    print(LaTeXFormatter.format_item(test_se))
