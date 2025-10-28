"""
Configuration pour scraping en production
Collecte maximale de données (~700k items)
"""

import asyncio
from main import MathDataScraper


async def production_scraping():
    """
    Scraping complet pour production
    Durée estimée: 5-20 heures selon connexion
    """
    
    print("🚀 DÉMARRAGE SCRAPING PRODUCTION")
    print("=" * 60)
    print("\nConfiguration:")
    print("  - Toutes les sources activées")
    print("  - Pas de limite d'items")
    print("  - Sauvegarde incrémentale")
    print("\n⚠ Ce processus peut prendre plusieurs heures")
    print("  Interrompre avec Ctrl+C (données déjà collectées seront conservées)")
    print("\n" + "=" * 60 + "\n")
    
    # Initialiser scraper
    scraper = MathDataScraper(output_dir="./math_dataset_production")
    
    # Phase 1: Sources rapides et riches
    print("\n📦 PHASE 1: Sources rapides")
    print("  - Stack Exchange")
    print("  - ProofWiki")
    
    await scraper.scrape_all(
        sources=['stackexchange', 'proofwiki'],
        max_per_source=None  # Pas de limite
    )
    
    print("\n✓ Phase 1 terminée")
    print(f"  Items collectés: {scraper.stats['total_scraped']}")
    
    # Phase 2: arXiv (plus lent)
    print("\n📚 PHASE 2: arXiv (peut prendre plusieurs heures)")
    
    await scraper.scrape_source('arxiv', max_items=50000)  # Limiter pour éviter timeout
    
    print("\n✓ Phase 2 terminée")
    
    # Phase 3: Cours français
    print("\n🇫🇷 PHASE 3: Cours français")
    
    await scraper.scrape_source('french_courses', max_items=None)
    
    print("\n✓ Phase 3 terminée")
    
    # Résumé final
    print("\n" + "=" * 60)
    print("SCRAPING PRODUCTION TERMINÉ")
    print("=" * 60)
    
    summary = scraper.get_summary()
    print(f"\n📊 Statistiques finales:")
    print(f"  Total items: {summary['total_items']}")
    print(f"\n  Par source:")
    for source, count in summary['by_source'].items():
        print(f"    - {source}: {count:,} items")
    
    print(f"\n  Données sauvegardées: {summary['output_directory']}")
    
    # Post-processing
    print("\n⚙️  Post-processing...")
    storage = scraper.storage
    
    print("  - Fusion des batches...")
    storage.merge_to_single_file("complete_dataset.json")
    
    print("  - Création des splits train/val/test...")
    storage.export_by_format()
    
    print("\n✅ TOUT EST PRÊT POUR L'ENTRAÎNEMENT!")
    print(f"  Dataset complet: {summary['output_directory']}/complete_dataset.json")
    print(f"  Splits: {summary['output_directory']}/processed/")


async def quick_sample_scraping():
    """
    Version rapide pour tests/démo
    ~10 minutes, ~5k items
    """
    print("⚡ SCRAPING RAPIDE (ÉCHANTILLON)")
    
    scraper = MathDataScraper(output_dir="./math_dataset_sample")
    
    await scraper.scrape_all(
        sources=['stackexchange', 'proofwiki'],
        max_per_source=2500  # 5k total
    )
    
    summary = scraper.get_summary()
    print(f"\n✓ Échantillon collecté: {summary['total_items']} items")


async def custom_scraping():
    """
    Configuration personnalisée
    Adapter selon besoins
    """
    scraper = MathDataScraper(output_dir="./math_dataset_custom")
    
    # Exemple: seulement Stack Exchange, filtré par tags spécifiques
    # (nécessite modification du scraper pour accepter des paramètres)
    
    await scraper.scrape_source('stackexchange', max_items=10000)
    
    summary = scraper.get_summary()
    print(f"\n✓ Custom scraping: {summary['total_items']} items")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        mode = sys.argv[1]
    else:
        print("Usage:")
        print("  python production_scraping.py [production|sample|custom]")
        print("\nModes:")
        print("  production - Scraping complet (700k+ items, plusieurs heures)")
        print("  sample     - Échantillon rapide (5k items, ~10 min)")
        print("  custom     - Configuration personnalisée")
        sys.exit(1)
    
    if mode == "production":
        asyncio.run(production_scraping())
    elif mode == "sample":
        asyncio.run(quick_sample_scraping())
    elif mode == "custom":
        asyncio.run(custom_scraping())
    else:
        print(f"Mode inconnu: {mode}")
        sys.exit(1)
