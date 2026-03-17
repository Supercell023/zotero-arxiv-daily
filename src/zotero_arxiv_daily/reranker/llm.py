from .base import BaseReranker, register_reranker
from openai import OpenAI
import numpy as np
from loguru import logger
import json

@register_reranker("llm")
class LLMReranker(BaseReranker):
    """
    LLM-based reranker that uses language models to judge relevance.
    More accurate but slower and more expensive than embedding-based methods.
    """

    def get_similarity_score(self, s1: list[str], s2: list[str]) -> np.ndarray:
        """
        Use LLM to judge relevance between candidate papers and corpus papers.
        """
        client = OpenAI(
            api_key=self.config.llm.api.key,
            base_url=self.config.llm.api.base_url
        )

        n_candidates = len(s1)
        n_corpus = len(s2)

        # Initialize similarity matrix
        sim = np.zeros((n_candidates, n_corpus))

        # Sample corpus papers for efficiency (use top weighted ones)
        max_corpus_samples = self.config.reranker.get('llm', {}).get('max_corpus_samples', 20)
        corpus_indices = list(range(min(n_corpus, max_corpus_samples)))

        logger.info(f"LLM Reranker: Comparing {n_candidates} candidates against {len(corpus_indices)} corpus papers")

        # Batch process candidates
        batch_size = self.config.reranker.get('llm', {}).get('batch_size', 5)

        for i in range(0, n_candidates, batch_size):
            batch_candidates = s1[i:i+batch_size]
            batch_indices = list(range(i, min(i+batch_size, n_candidates)))

            for j in corpus_indices:
                corpus_abstract = s2[j]

                # Create prompt for batch comparison
                prompt = self._create_comparison_prompt(batch_candidates, corpus_abstract)

                try:
                    response = client.chat.completions.create(
                        messages=[
                            {
                                "role": "system",
                                "content": """You are an expert research paper reviewer. Given a reference paper abstract and multiple candidate paper abstracts, rate how relevant each candidate is to the reference paper.

Consider:
- Topic similarity
- Methodological relevance
- Potential for cross-pollination of ideas
- Complementary approaches

Output ONLY a JSON array of scores from 0.0 to 1.0, one for each candidate.
Example: [0.8, 0.3, 0.6]"""
                            },
                            {"role": "user", "content": prompt}
                        ],
                        model=self.config.llm.generation_kwargs.model,
                        max_tokens=200,
                        temperature=0.3
                    )

                    # Parse scores
                    scores_text = response.choices[0].message.content.strip()
                    scores = json.loads(scores_text)

                    # Assign scores to similarity matrix
                    for k, score in enumerate(scores):
                        if i + k < n_candidates:
                            sim[i + k, j] = float(score)

                except Exception as e:
                    logger.warning(f"LLM reranking failed for batch {i}-{i+batch_size}, corpus {j}: {e}")
                    # Fallback to neutral score
                    for k in range(len(batch_candidates)):
                        if i + k < n_candidates:
                            sim[i + k, j] = 0.5

        # Fill remaining corpus papers with average scores (to save API calls)
        if n_corpus > max_corpus_samples:
            avg_scores = sim[:, :max_corpus_samples].mean(axis=1, keepdims=True)
            sim[:, max_corpus_samples:] = avg_scores * 0.8  # Slightly lower weight

        logger.info(f"LLM Reranker: Completed scoring")
        return sim

    def _create_comparison_prompt(self, candidate_abstracts: list[str], reference_abstract: str) -> str:
        """Create a prompt for batch comparison"""
        prompt = f"Reference paper abstract:\n{reference_abstract}\n\n"
        prompt += "Candidate papers:\n"
        for i, abstract in enumerate(candidate_abstracts):
            # Truncate long abstracts
            truncated = abstract[:500] + "..." if len(abstract) > 500 else abstract
            prompt += f"{i+1}. {truncated}\n\n"

        prompt += f"Rate the relevance of each of the {len(candidate_abstracts)} candidates to the reference (0.0-1.0):"
        return prompt
