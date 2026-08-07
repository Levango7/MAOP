"""Memory subpackage.

三层认知记忆、向量存储、语义缓存、知识图谱与抽取。

Modules:
    three_layer_memory, three_layer_memory_types, three_layer_memory_utils,
    vector, semantic_cache, bloom_filter, hybrid_search,
    knowledge_graph, knowledge_extractor
"""
from __future__ import annotations

import importlib

__all__ = [
    "logger",
    "BloomFilter",
    "logger",
    "HybridSearchResult",
    "HybridSearchStats",
    "rrf_fuse",
    "HybridSearch",
    "logger",
    "Entity",
    "Relation",
    "Fact",
    "ExtractionResult",
    "KnowledgeExtractor",
    "logger",
    "GraphNode",
    "GraphEdge",
    "Subgraph",
    "KnowledgeGraph",
    "logger",
    "SemanticCacheEntry",
    "SemanticCacheStats",
    "SemanticCache",
    "logger",
    "LAYER_NAME_MAP",
    "ThreeLayerMemory",
    "QualityDimensions",
    "EpisodicEntry",
    "EpisodicSearchResult",
    "ConsolidationReport",
    "FocusMode",
    "ContextHead",
    "HeadResult",
    "MultiHeadResult",
    "FocusConfig",
    "TransformResult",
    "ContextItem",
    "DECAY_TIERS",
    "decay_weight",
    "logger",
    "logger",
    "VectorEntry",
    "VectorSearchResult",
    "cosine_similarity",
    "EmbeddingProvider",
    "HashEmbedding",
    "SentenceTransformerEmbedding",
    "VectorStore",
]

# 符号 → 子模块名映射（惰性加载用，含私有符号）
_SYMBOL_TO_MODULE: dict[str, str] = {
    # 注: 多个子模块均导出同名符号（如 logger），
    # 按字典构造语义仅最后一个映射生效，与重构前运行时行为一致。
    "_mmh3_hash32": "bloom_filter",
    "_hash_i": "bloom_filter",
    "_BitArray": "bloom_filter",
    "BloomFilter": "bloom_filter",
    "HybridSearchResult": "hybrid_search",
    "HybridSearchStats": "hybrid_search",
    "_RRF_K": "hybrid_search",
    "rrf_fuse": "hybrid_search",
    "HybridSearch": "hybrid_search",
    "Entity": "knowledge_extractor",
    "Relation": "knowledge_extractor",
    "Fact": "knowledge_extractor",
    "ExtractionResult": "knowledge_extractor",
    "KnowledgeExtractor": "knowledge_extractor",
    "GraphNode": "knowledge_graph",
    "GraphEdge": "knowledge_graph",
    "Subgraph": "knowledge_graph",
    "KnowledgeGraph": "knowledge_graph",
    "SemanticCacheEntry": "semantic_cache",
    "SemanticCacheStats": "semantic_cache",
    "SemanticCache": "semantic_cache",
    "LAYER_NAME_MAP": "three_layer_memory",
    "_EPISODIC_DDL": "three_layer_memory",
    "ThreeLayerMemory": "three_layer_memory",
    "QualityDimensions": "three_layer_memory_types",
    "EpisodicEntry": "three_layer_memory_types",
    "EpisodicSearchResult": "three_layer_memory_types",
    "ConsolidationReport": "three_layer_memory_types",
    "FocusMode": "three_layer_memory_types",
    "ContextHead": "three_layer_memory_types",
    "HeadResult": "three_layer_memory_types",
    "MultiHeadResult": "three_layer_memory_types",
    "FocusConfig": "three_layer_memory_types",
    "TransformResult": "three_layer_memory_types",
    "ContextItem": "three_layer_memory_types",
    "DECAY_TIERS": "three_layer_memory_types",
    "decay_weight": "three_layer_memory_types",
    "_text_relevance": "three_layer_memory_utils",
    "_item_to_text": "three_layer_memory_utils",
    "_compress_text": "three_layer_memory_utils",
    "_DEFAULT_FOCUS_CONFIGS": "three_layer_memory_utils",
    "_NEGATIVE_KEYWORDS": "three_layer_memory_utils",
    "_is_negative_feedback": "three_layer_memory_utils",
    "logger": "vector",
    "VectorEntry": "vector",
    "VectorSearchResult": "vector",
    "cosine_similarity": "vector",
    "EmbeddingProvider": "vector",
    "HashEmbedding": "vector",
    "SentenceTransformerEmbedding": "vector",
    "_VECTOR_DDL": "vector",
    "VectorStore": "vector",
}


def __getattr__(name: str):
    """惰性加载子模块符号，避免循环导入。"""
    if name in _SYMBOL_TO_MODULE:
        mod_name = _SYMBOL_TO_MODULE[name]
        mod = importlib.import_module(f".{mod_name}", __name__)
        value = getattr(mod, name)
        globals()[name] = value  # 缓存，下次直接访问
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
