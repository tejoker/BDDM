#!/usr/bin/env python3
"""
Export collected samples to LaTeX format
"""

import json
import os
import sys
from pathlib import Path
from utils.latex_formatter import LaTeXFormatter


def export_to_latex(input_dir='samples_en', output_dir='latex_output'):
    """
    Export all collected samples to LaTeX files
    
    Args:
        input_dir: Directory containing collected samples
        output_dir: Directory to save LaTeX files
    """
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    
    # Subdirectories for each source
    (output_path / 'stackexchange').mkdir(exist_ok=True)
    (output_path / 'proofwiki').mkdir(exist_ok=True)
    (output_path / 'arxiv').mkdir(exist_ok=True)
    
    all_items = []
    stats = {'stackexchange': 0, 'proofwiki': 0, 'arxiv': 0}
    
    print("="*70)
    print("EXPORTING TO LATEX")
    print("="*70)
    
    # Read all JSON files
    raw_dir = Path(input_dir) / 'raw'
    if not raw_dir.exists():
        print(f"Error: {raw_dir} not found")
        return
    
    for source_dir in raw_dir.iterdir():
        if not source_dir.is_dir():
            continue
        
        source_name = source_dir.name
        print(f"\n📄 Processing {source_name}...")
        
        for json_file in source_dir.glob('*.json'):
            with open(json_file, 'r', encoding='utf-8') as f:
                items = json.load(f)
            
            for i, item in enumerate(items):
                try:
                    # Generate filename
                    item_id = item.get('id', f'{source_name}_{i}')
                    safe_id = item_id.replace('/', '_').replace(':', '_')
                    output_file = output_path / source_name / f'{safe_id}.tex'
                    
                    # Convert to LaTeX
                    latex_content = LaTeXFormatter.format_item(item)
                    
                    # Save
                    with open(output_file, 'w', encoding='utf-8') as f:
                        f.write(latex_content)
                    
                    stats[source_name] += 1
                    all_items.append(item)
                
                except Exception as e:
                    print(f"  ⚠ Error processing item {i}: {e}")
                    continue
        
        print(f"  ✓ Exported {stats[source_name]} items")
    
    # Create combined document
    if all_items:
        print(f"\n📚 Creating combined document...")
        combined_latex = LaTeXFormatter.format_batch_as_latex(all_items)
        combined_file = output_path / 'all_combined.tex'
        with open(combined_file, 'w', encoding='utf-8') as f:
            f.write(combined_latex)
        print(f"  ✓ Saved to {combined_file}")
    
    # Summary
    total = sum(stats.values())
    print("\n" + "="*70)
    print("EXPORT COMPLETE")
    print("="*70)
    print(f"\n✅ Exported {total} items total:")
    for source, count in stats.items():
        if count > 0:
            print(f"   - {count:3d} {source}")
    
    print(f"\n📁 LaTeX files saved to: {output_dir}/")
    print(f"   - Individual files: {output_dir}/<source>/*.tex")
    print(f"   - Combined: {output_dir}/all_combined.tex")
    
    print(f"\n💡 To compile LaTeX:")
    print(f"   cd {output_dir}")
    print(f"   pdflatex all_combined.tex")
    
    return total


if __name__ == "__main__":
    input_dir = sys.argv[1] if len(sys.argv) > 1 else 'samples_en'
    output_dir = sys.argv[2] if len(sys.argv) > 2 else 'latex_output'
    
    total = export_to_latex(input_dir, output_dir)
    
    if total > 0:
        print(f"\n🎉 Export successful! {total} LaTeX documents ready.")
    else:
        print(f"\n⚠ No items found to export.")
