#!/usr/bin/env python3
"""
HyDE (Hypothetical Document Embeddings) Query Expansion for MPEP RAG

Implements multiple backends:
1. Rule-based patent law expansion (no external dependencies, always works)
2. Local small model (optional, lightweight)
3. External API (optional, best quality)

Philosophy: Works great offline, even better with optional enhancements.
"""

import os
import re
import sys

DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# Model used for HyDE query expansion when the Qwen backend is active. Query
# expansion is one short generation per search, so the cheap flash tier is the
# default; HYDE_QWEN_MODEL can name a stronger model such as qwen3-coder-plus.
DEFAULT_QWEN_MODEL = "qwen3-coder-flash"

# Providers that speak the OpenAI protocol versus Anthropic's own SDK. Keys are
# read per provider only — an OpenAI key is never sent to a Qwen endpoint.
API_PROVIDERS = ("qwen", "openai", "anthropic")
PROVIDER_KEY_ENV = {
    "qwen": "QWEN_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


class HyDEQueryExpander:
    """Expands queries using Hypothetical Document Embeddings"""

    def __init__(self, backend: str = "auto"):
        """
        Initialize HyDE expander with specified backend

        Args:
            backend: "auto", "rule-based", "local", or "api"
        """
        self.backend = backend
        self.api_client = None
        self.local_model = None
        self.api_provider = ""
        self.strict = os.getenv("HYDE_STRICT", "").strip().lower() in ("1", "true", "yes", "on")

        if backend == "auto":
            self._detect_best_backend()
        elif backend == "api":
            self._init_api_backend()
        elif backend == "local":
            self._init_local_backend()
        elif backend == "rule-based":
            print("HyDE: Using rule-based expansion (always available)", file=sys.stderr)
        else:
            raise ValueError(f"Unknown backend: {backend}")

    def _detect_best_backend(self):
        """Auto-detect the best available backend.

        API backends require explicit opt-in via HYDE_BACKEND=api env var,
        because ANTHROPIC_API_KEY is often present in Claude Code environments
        where using it for HyDE would silently consume API quota.
        """
        # Only use API if explicitly opted in (ANTHROPIC_API_KEY alone is not enough)
        if os.getenv("HYDE_BACKEND", "").lower() == "api":
            self._init_api_backend()
            return

        # Try local model (good quality, slower)
        try:
            self._init_local_backend()
            return
        except Exception:
            pass

        # Fallback to rule-based (fast, always works)
        print(
            "HyDE: Using rule-based expansion (no API key or local model found)",
            file=sys.stderr,
        )
        self.backend = "rule-based"

    def _resolve_provider(self) -> str:
        """Pick the API provider for HyDE expansion.

        An explicit HYDE_API_PROVIDER wins. Otherwise the first provider that
        has its own key is used, in the order Qwen, OpenAI, Anthropic. Keys are
        never shared between providers: an OpenAI key is not forwarded to a Qwen
        endpoint, so `auto` with only OPENAI_API_KEY means the OpenAI API.
        """
        explicit = os.getenv("HYDE_API_PROVIDER", "").strip().lower()
        if explicit in API_PROVIDERS:
            return explicit
        if explicit and explicit != "auto":
            raise ValueError(f"Unknown HYDE_API_PROVIDER: {explicit}")

        for provider in API_PROVIDERS:
            if os.getenv(PROVIDER_KEY_ENV[provider]):
                return provider
        return ""

    def _init_api_backend(self):
        """Initialize API backend (Qwen, OpenAI or Anthropic)"""
        provider = self._resolve_provider()
        if not provider:
            try:
                from cli_command import render
            except ImportError:
                from mcp_server.cli_command import render
            raise ValueError(render(
                "HyDE API backend is unavailable. Set an API key and install its SDK: "
                "pip install anthropic openai"
            ))

        key = os.getenv(PROVIDER_KEY_ENV[provider])
        if not key:
            try:
                from cli_command import render
            except ImportError:
                from mcp_server.cli_command import render
            raise ValueError(render(
                f"HyDE_API_PROVIDER={provider} but {PROVIDER_KEY_ENV[provider]} is not set. "
                "Local OpenAI-compatible servers (vLLM/Ollama) accept any placeholder key, "
                "e.g. set it to 'local' and point HYDE_OPENAI_BASE_URL at the server."
            ))

        if provider == "anthropic":
            try:
                import anthropic  # type: ignore[import-not-found]
            except ImportError:
                raise ValueError("The Anthropic SDK is not installed: pip install anthropic")
            self.api_client = anthropic.Anthropic(api_key=key)
        else:
            try:
                import openai  # type: ignore[import-not-found]
            except ImportError:
                raise ValueError("The OpenAI SDK is not installed: pip install openai")
            # Qwen speaks the OpenAI protocol, so the OpenAI SDK is reused with a
            # base URL (DashScope compatible-mode by default for Qwen; set
            # HYDE_OPENAI_BASE_URL for a local vLLM/Ollama serve).
            # OPENAI_BASE_URL belongs to the OpenAI provider only: sending the Qwen
            # key to a base URL the user set for OpenAI would point at the wrong server.
            if provider == "qwen":
                base_url = os.getenv("HYDE_OPENAI_BASE_URL") or DEFAULT_QWEN_BASE_URL
            else:
                base_url = os.getenv("HYDE_OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL") or None
            self.api_client = openai.OpenAI(api_key=key, base_url=base_url)

        self.api_provider = provider
        # expand_query() dispatches on self.backend, so the backend has to name the
        # provider that was chosen; leaving it as "api" falls through to the no-op
        # branch and every API call silently returns the unexpanded query.
        self.backend = provider
        print(f"HyDE: Using {provider} API backend", file=sys.stderr)

    def _api_failure(self, exc: Exception, query: str, num_expansions: int) -> list[str]:
        """Report an API failure. With HYDE_STRICT the failure is raised instead
        of silently degrading to rule-based expansion."""
        if self.strict:
            raise ValueError(f"HyDE API call failed (HYDE_STRICT=true): {exc}") from exc
        print(f"HyDE API error: {exc}, falling back to rule-based", file=sys.stderr)
        return self._rule_based_expansion(query, num_expansions)

    def _init_local_backend(self):
        """Initialize local model backend (lightweight GPT-2 style)"""
        try:
            import torch
            from transformers import pipeline

            # Detect GPU
            device = 0 if torch.cuda.is_available() else -1  # 0 = first GPU, -1 = CPU
            device_name = "GPU" if device == 0 else "CPU"

            if device == 0:
                gpu_name = torch.cuda.get_device_name(0)
                print(
                    f"HyDE: Loading local text generation model on {gpu_name}...",
                    file=sys.stderr,
                )
            else:
                print(
                    "HyDE: Loading local text generation model on CPU...",
                    file=sys.stderr,
                )

            self.local_model = pipeline(
                "text-generation", model="distilgpt2", max_length=200, device=device
            )
            self.backend = "local"
            print(
                f"HyDE: Using local model backend (distilgpt2) on {device_name}",
                file=sys.stderr,
            )
        except Exception as e:
            raise ValueError(f"Failed to load local model: {e}")

    def expand_query(self, query: str, num_expansions: int = 3) -> list[str]:
        """
        Expand query into hypothetical documents

        Args:
            query: Original user query
            num_expansions: Number of hypothetical documents to generate

        Returns:
            List of hypothetical documents (including original query)
        """
        if self.backend == "rule-based":
            return self._rule_based_expansion(query, num_expansions)
        elif self.backend == "anthropic":
            return self._anthropic_expansion(query, num_expansions)
        elif self.backend == "openai":
            return self._openai_expansion(query, num_expansions)
        elif self.backend == "qwen":
            return self._openai_expansion(
                query,
                num_expansions,
                model_env="HYDE_QWEN_MODEL",
                default_model=DEFAULT_QWEN_MODEL,
            )
        elif self.backend == "local":
            return self._local_expansion(query, num_expansions)
        else:
            return [query]

    def _rule_based_expansion(self, query: str, num_expansions: int) -> list[str]:
        """
        Rule-based expansion using patent law domain knowledge
        Fast, offline, no dependencies
        """
        expansions = [query]  # Always include original

        # Patent law templates for common query types
        templates = self._get_patent_law_templates(query)

        # Add template-based expansions
        for template in templates[: num_expansions - 1]:
            expansions.append(template)

        return expansions

    def _get_patent_law_templates(self, query: str) -> list[str]:
        """Generate patent-law-specific expansions based on query patterns"""
        query_lower = query.lower()
        expansions = []

        # Claim-related queries
        if any(word in query_lower for word in ["claim", "claims"]):
            if "definite" in query_lower or "definiteness" in query_lower:
                expansions.append(
                    "Under 35 USC 112(b), patent claims must be definite and particularly point out "
                    "and distinctly claim the subject matter of the invention. The claim language must "
                    "be sufficiently clear and precise to inform those skilled in the art of the scope "
                    "of the claimed invention with reasonable certainty."
                )
            if "format" in query_lower or "structure" in query_lower:
                expansions.append(
                    "Patent claims must be in the form of a single sentence beginning with a capital "
                    "letter and ending with a period. Each claim should include a preamble stating the "
                    "general nature of the invention, a transitional phrase, and a body that describes "
                    "the specific elements and limitations of the claimed invention."
                )
            if "antecedent" in query_lower or "basis" in query_lower:
                expansions.append(
                    "Proper antecedent basis requires that each element referred to using 'the' or 'said' "
                    "must have been previously introduced in the claim using 'a' or 'an'. This ensures "
                    "clarity and definiteness in claim language and prevents ambiguity about which elements "
                    "are being referenced."
                )
            if "dependent" in query_lower:
                expansions.append(
                    "A dependent claim refers back to and further limits a previous claim. Dependent claims "
                    "incorporate all limitations of the claim to which they refer and must be construed to "
                    "include all those limitations. The doctrine of claim differentiation presumes different "
                    "scope between independent and dependent claims."
                )

        # Specification-related queries
        if any(word in query_lower for word in ["specification", "spec", "description"]):
            if "written description" in query_lower or "112(a)" in query_lower:
                expansions.append(
                    "The written description requirement under 35 USC 112(a) mandates that the specification "
                    "must describe the invention in sufficient detail to show that the inventor possessed the "
                    "claimed invention at the time of filing. The description must convey with reasonable "
                    "clarity to those skilled in the art that the inventor possessed the claimed invention."
                )
            if "enable" in query_lower or "enablement" in query_lower:
                expansions.append(
                    "Under 35 USC 112(a), the specification must enable a person skilled in the art to make "
                    "and use the full scope of the claimed invention without undue experimentation. The "
                    "enablement requirement ensures that the public receives meaningful disclosure in exchange "
                    "for the patent monopoly."
                )
            if "best mode" in query_lower:
                expansions.append(
                    "The best mode requirement under 35 USC 112(a) required disclosure of the best way the "
                    "inventor knew to practice the invention at the time of filing. Under the America Invents "
                    "Act, failure to disclose best mode is no longer a basis for invalidity, though the "
                    "requirement to disclose still exists."
                )

        # Formality queries
        if any(word in query_lower for word in ["abstract", "drawing", "formality", "formal"]):
            if "abstract" in query_lower:
                expansions.append(
                    "The abstract must be a brief summary of the technical disclosure, preferably 150 words "
                    "or less. It should enable the USPTO and the public to quickly determine the nature and "
                    "gist of the technical disclosure. The abstract is not used for interpreting the scope "
                    "of claim protection."
                )
            if "drawing" in query_lower:
                expansions.append(
                    "Patent drawings must show every feature of the invention specified in the claims. "
                    "Drawings must be in a particular form and follow specific rules regarding margins, "
                    "views, symbols, legends, and arrangement. Design patent drawings are subject to "
                    "additional requirements regarding shading and surface characteristics."
                )

        # USC/statute queries
        if (
            re.search(r"35\s*u\.?s\.?c\.?\s*Section ?\s*\d+", query_lower)
            or "statute" in query_lower
        ):
            if "101" in query_lower or "eligible" in query_lower:
                expansions.append(
                    "35 USC 101 defines patent-eligible subject matter: processes, machines, manufactures, "
                    "and compositions of matter. Abstract ideas, laws of nature, and natural phenomena are "
                    "not patentable. The Alice/Mayo framework evaluates whether claims are directed to patent-"
                    "eligible subject matter or merely abstract ideas with conventional implementation."
                )
            if "102" in query_lower or "novelty" in query_lower:
                expansions.append(
                    "35 USC 102 defines conditions for patentability relating to novelty. A patent may not "
                    "be obtained if the invention was known or used by others, patented, described in a "
                    "printed publication, or otherwise available to the public before the effective filing "
                    "date of the claimed invention."
                )
            if "103" in query_lower or "obvious" in query_lower:
                expansions.append(
                    "35 USC 103 prohibits patents on inventions that would have been obvious to a person "
                    "having ordinary skill in the art at the time of invention. The Graham factors consider "
                    "the scope and content of prior art, differences between prior art and claims, level of "
                    "ordinary skill, and secondary considerations like commercial success."
                )

        # If no specific templates matched, use general patent law expansion
        if not expansions:
            expansions.append(
                f"In patent law and the MPEP manual, regarding {query}, the relevant statutory and "
                f"regulatory provisions establish specific requirements and procedures that must be followed "
                f"for patent prosecution and examination."
            )

        return expansions

    def _anthropic_expansion(self, query: str, num_expansions: int) -> list[str]:
        """Generate hypothetical documents using Anthropic API"""
        try:
            prompt = f"""You are a patent law expert. Given the query below, write a concise hypothetical answer as it would appear in the USPTO Manual of Patent Examining Procedure (MPEP).

Query: {query}

Write a clear, technical answer (2-3 sentences) that would help find relevant MPEP sections:"""

            message = self.api_client.messages.create(  # type: ignore[union-attr]
                model=os.environ.get("HYDE_ANTHROPIC_MODEL", "claude-haiku-4-5"),  # Fast, cheap model
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )

            hypothetical_doc = message.content[0].text.strip()
            return [query, hypothetical_doc]

        except Exception as e:
            return self._api_failure(e, query, num_expansions)

    def _openai_expansion(
        self,
        query: str,
        num_expansions: int,
        model_env: str = "HYDE_OPENAI_MODEL",
        default_model: str = "gpt-4o-mini",
    ) -> list[str]:
        """Generate hypothetical documents via an OpenAI-protocol API (OpenAI or Qwen)"""
        try:
            prompt = f"""You are a patent law expert. Given the query below, write a concise hypothetical answer as it would appear in the USPTO Manual of Patent Examining Procedure (MPEP).

Query: {query}

Write a clear, technical answer (2-3 sentences) that would help find relevant MPEP sections:"""

            response = self.api_client.chat.completions.create(  # type: ignore[union-attr]
                model=os.environ.get(model_env, default_model),  # Fast, cheap model
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )

            hypothetical_doc = response.choices[0].message.content.strip()
            return [query, hypothetical_doc]

        except Exception as e:
            return self._api_failure(e, query, num_expansions)

    def _local_expansion(self, query: str, num_expansions: int) -> list[str]:
        """Generate hypothetical documents using local model"""
        try:
            prompt = f"In patent law, {query}. The MPEP states that"

            result = self.local_model(  # type: ignore[misc]
                prompt, max_length=150, num_return_sequences=1, temperature=0.7
            )

            hypothetical_doc = result[0]["generated_text"]
            return [query, hypothetical_doc]

        except Exception as e:
            print(
                f"HyDE local model error: {e}, falling back to rule-based",
                file=sys.stderr,
            )
            return self._rule_based_expansion(query, num_expansions)


def test_hyde():
    """Test HyDE expansion with various queries"""
    expander = HyDEQueryExpander(backend="rule-based")

    test_queries = [
        "What are the claim definiteness requirements?",
        "written description requirement 35 USC 112(a)",
        "dependent claim format",
        "patent abstract requirements",
        "35 USC 103 obviousness",
    ]

    print("\n=== HyDE Query Expansion Test ===\n")
    for query in test_queries:
        print(f"Query: {query}")
        expansions = expander.expand_query(query, num_expansions=2)
        for i, exp in enumerate(expansions, 1):
            print(f"  [{i}] {exp[:100]}...")
        print()


if __name__ == "__main__":
    test_hyde()
