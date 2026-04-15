"""Centralized configuration loader for DESol pipeline.

Replaces direct os.environ.get() calls scattered across 22 scripts with
a single source of truth for all configuration parameters.

Configuration sources (in priority order):
  1. Environment variables (override everything)
  2. config.toml (if exists in project root)
  3. Hardcoded defaults (last resort)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ProofSearchConfig:
    """Configuration for MCTS proof search."""
    lean_timeout: int = 120
    max_repair_rounds: int = 5
    mcts_iterations: int = 50
    mcts_exploration_c: float = 1.4
    mcts_branch_min: int = 3
    mcts_branch_max: int = 6
    ponder_rounds: int = 6
    max_tactic_length: int = 10_000  # P1: Cap tactic strings to prevent DoS
    value_model: str = "mistral-medium"
    policy_model: str = "mistral-small"
    top_k_premises: int = 5


@dataclass
class CacheConfig:
    """Configuration for proof cache."""
    db_path: str | Path = "output/cache/proof.db"
    ttl_seconds: int = 86400 * 30  # 30 days
    schema_version: int = 2
    max_size_mb: int = 1024


@dataclass
class BackendConfig:
    """Configuration for proof backend."""
    mode: str = "auto"  # auto | leandojo | repldojo
    force_repl_dojo: bool = False
    timeout: int = 120


@dataclass
class APIConfig:
    """Configuration for KG API and external services."""
    kg_db_path: str | Path = "output/kg/kg_index.db"
    project_root: str | Path = "."
    server_host: str = "0.0.0.0"
    server_port: int = 8000


@dataclass
class PipelineConfig:
    """Configuration for arxiv-to-lean pipeline."""
    arxiv_queue_db: str | Path = "data/arxiv_queue.db"
    output_dir: Path = Path("output")
    max_parallel_workers: int = 2
    batch_size: int = 10
    retry_on_failure: bool = True
    max_retries: int = 3


@dataclass
class DESolConfig:
    """Root configuration aggregating all subsystems."""
    proof_search: ProofSearchConfig
    cache: CacheConfig
    backend: BackendConfig
    api: APIConfig
    pipeline: PipelineConfig

    @classmethod
    def from_env(cls) -> DESolConfig:
        """Load configuration from environment variables and config file."""
        config = cls(
            proof_search=ProofSearchConfig(),
            cache=CacheConfig(),
            backend=BackendConfig(),
            api=APIConfig(),
            pipeline=PipelineConfig(),
        )
        
        # Override with environment variables
        config._apply_env_overrides()
        
        # Override with config file if present
        config_file = Path(".") / "desol.toml"
        if config_file.exists():
            config._apply_toml_overrides(config_file)
        
        logger.info("Configuration loaded: %s", config)
        return config

    def _apply_env_overrides(self) -> None:
        """Override config with environment variables."""
        # Proof search
        if os.environ.get("DESOL_LEAN_TIMEOUT"):
            self.proof_search.lean_timeout = int(os.environ["DESOL_LEAN_TIMEOUT"])
        if os.environ.get("DESOL_MAX_REPAIR_ROUNDS"):
            self.proof_search.max_repair_rounds = int(os.environ["DESOL_MAX_REPAIR_ROUNDS"])
        if os.environ.get("DESOL_MCTS_ITERATIONS"):
            self.proof_search.mcts_iterations = int(os.environ["DESOL_MCTS_ITERATIONS"])
        if os.environ.get("DESOL_MCTS_EXPLORATION_C"):
            self.proof_search.mcts_exploration_c = float(os.environ["DESOL_MCTS_EXPLORATION_C"])
        if os.environ.get("DESOL_PONDER_ROUNDS"):
            self.proof_search.ponder_rounds = int(os.environ["DESOL_PONDER_ROUNDS"])
        if os.environ.get("DESOL_MAX_TACTIC_LEN"):
            self.proof_search.max_tactic_length = int(os.environ["DESOL_MAX_TACTIC_LEN"])
        
        # Cache
        if os.environ.get("DESOL_CACHE_DB"):
            self.cache.db_path = Path(os.environ["DESOL_CACHE_DB"])
        if os.environ.get("DESOL_CACHE_TTL"):
            self.cache.ttl_seconds = int(os.environ["DESOL_CACHE_TTL"])
        
        # Backend
        if os.environ.get("DESOL_BACKEND_MODE"):
            self.backend.mode = os.environ["DESOL_BACKEND_MODE"]
        if os.environ.get("DESOL_FORCE_REPL_DOJO"):
            self.backend.force_repl_dojo = os.environ["DESOL_FORCE_REPL_DOJO"].lower() in ("1", "true", "yes")
        
        # API
        if os.environ.get("DESOL_KG_DB"):
            self.api.kg_db_path = Path(os.environ["DESOL_KG_DB"])
        if os.environ.get("DESOL_PROJECT_ROOT"):
            self.api.project_root = Path(os.environ["DESOL_PROJECT_ROOT"])
        
        # Pipeline
        if os.environ.get("DESOL_MAX_WORKERS"):
            self.pipeline.max_parallel_workers = int(os.environ["DESOL_MAX_WORKERS"])
        if os.environ.get("DESOL_BATCH_SIZE"):
            self.pipeline.batch_size = int(os.environ["DESOL_BATCH_SIZE"])

    def _apply_toml_overrides(self, config_file: Path) -> None:
        """Override config with TOML file (requires tomllib/tomli)."""
        try:
            try:
                import tomllib  # Python 3.11+
            except ImportError:
                import tomli as tomllib  # type: ignore[import]
            
            with open(config_file, "rb") as f:
                data = tomllib.load(f)
            
            # Apply proof_search overrides
            if "proof_search" in data:
                for key, val in data["proof_search"].items():
                    if hasattr(self.proof_search, key):
                        setattr(self.proof_search, key, val)
            
            # Apply cache overrides
            if "cache" in data:
                for key, val in data["cache"].items():
                    if hasattr(self.cache, key) and key != "db_path":
                        setattr(self.cache, key, val)
                    elif key == "db_path":
                        self.cache.db_path = Path(val)
            
            # Apply backend overrides
            if "backend" in data:
                for key, val in data["backend"].items():
                    if hasattr(self.backend, key):
                        setattr(self.backend, key, val)
            
            # Apply api overrides
            if "api" in data:
                for key, val in data["api"].items():
                    if hasattr(self.api, key):
                        if key in ("kg_db_path", "project_root"):
                            setattr(self.api, key, Path(val))
                        else:
                            setattr(self.api, key, val)
            
            # Apply pipeline overrides
            if "pipeline" in data:
                for key, val in data["pipeline"].items():
                    if hasattr(self.pipeline, key):
                        if key in ("arxiv_queue_db", "output_dir"):
                            setattr(self.pipeline, key, Path(val))
                        else:
                            setattr(self.pipeline, key, val)
            
            logger.info("Configuration overrides loaded from %s", config_file)
        except ImportError:
            logger.warning("TOML support not available (install tomli for Python <3.11)")
        except Exception as exc:
            logger.warning("Failed to load config from %s: %s", config_file, exc)

    def to_dict(self) -> dict[str, Any]:
        """Export configuration as flat dictionary."""
        return {
            # Proof search
            "lean_timeout": self.proof_search.lean_timeout,
            "max_repair_rounds": self.proof_search.max_repair_rounds,
            "mcts_iterations": self.proof_search.mcts_iterations,
            "mcts_exploration_c": self.proof_search.mcts_exploration_c,
            "mcts_branch_min": self.proof_search.mcts_branch_min,
            "mcts_branch_max": self.proof_search.mcts_branch_max,
            "ponder_rounds": self.proof_search.ponder_rounds,
            "value_model": self.proof_search.value_model,
            "policy_model": self.proof_search.policy_model,
            # Cache
            "cache_db": str(self.cache.db_path),
            "cache_ttl": self.cache.ttl_seconds,
            # Backend
            "backend_mode": self.backend.mode,
            # API
            "kg_db": str(self.api.kg_db_path),
            "project_root": str(self.api.project_root),
        }


# Global singleton — load once at module import
_GLOBAL_CONFIG: DESolConfig | None = None


def get_config() -> DESolConfig:
    """Get the singleton configuration instance."""
    global _GLOBAL_CONFIG
    if _GLOBAL_CONFIG is None:
        _GLOBAL_CONFIG = DESolConfig.from_env()
    return _GLOBAL_CONFIG


def reload_config() -> DESolConfig:
    """Force reload configuration from environment (mainly for testing)."""
    global _GLOBAL_CONFIG
    _GLOBAL_CONFIG = DESolConfig.from_env()
    return _GLOBAL_CONFIG
