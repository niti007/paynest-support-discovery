"""RAGAS 0.4.3 imports langchain_community.chat_models.vertexai (removed in langchain-community 0.4.x, which langchain-core>=1
forces). We never use Vertex, so register inert stubs BEFORE importing ragas. Import this module first, then `import ragas`."""
import sys
import types


def install() -> None:
    try:
        from langchain_community.chat_models.vertexai import ChatVertexAI  # noqa: F401
        return
    except Exception:  # noqa: BLE001
        pass
    mod = types.ModuleType("langchain_community.chat_models.vertexai")
    mod.ChatVertexAI = type("ChatVertexAI", (), {})
    sys.modules["langchain_community.chat_models.vertexai"] = mod
    import langchain_community.llms as llms
    if not hasattr(llms, "VertexAI"):
        llms.VertexAI = type("VertexAI", (), {})  # type: ignore[attr-defined]


install()
