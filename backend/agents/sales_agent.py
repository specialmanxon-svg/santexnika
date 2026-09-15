"""AI Sales Agent for two-factor compatibility validation."""
import json
import structlog
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from agents.vector_store import VectorStore
from models.compatibility import CompatibilityRule
from schemas.compatibility import CompatibilityCheckResponse

logger = structlog.get_logger()

class SalesAgent:
    def __init__(self):
        self.openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.vector_store = VectorStore()
        self.llm_model = settings.openai_model
        
    async def check_compatibility(
        self, 
        primary_sku: str, 
        target_sku: str, 
        session: AsyncSession
    ) -> CompatibilityCheckResponse:
        """Check compatibility using direct matrix match or RAG."""
        logger.info("checking_compatibility", primary=primary_sku, target=target_sku)
        
        # Step 1: Direct match in matrix
        stmt = select(CompatibilityRule).where(
            ((CompatibilityRule.base_sku == primary_sku) & (CompatibilityRule.compatible_sku == target_sku)) |
            ((CompatibilityRule.base_sku == target_sku) & (CompatibilityRule.compatible_sku == primary_sku))
        )
        result = await session.execute(stmt)
        rule = result.scalars().first()
        
        if rule:
            logger.info("compatibility_direct_match", rule_id=rule.id)
            return CompatibilityCheckResponse(
                is_compatible=True,
                confidence=1.0,
                method="static_matrix",
                reasoning="Found direct match in compatibility matrix."
            )
            
        # Step 2: RAG search
        primary_docs = await self.vector_store.similarity_search(primary_sku, session, k=2)
        target_docs = await self.vector_store.similarity_search(target_sku, session, k=2)
        
        context = "Technical Context for Primary Product:\n"
        context += "\n".join([doc["content"] for doc in primary_docs])
        context += "\n\nTechnical Context for Target Product:\n"
        context += "\n".join([doc["content"] for doc in target_docs])
        
        # Step 3: LLM evaluation
        prompt = f"""Evaluate plumbing component compatibility based on the following technical documentation chunks.
Primary SKU: {primary_sku}
Target SKU: {target_sku}

{context}

Return ONLY a JSON object with the following schema:
{{
    "confidence": float (0.0 to 1.0),
    "reasoning": string
}}
"""
        response = await self.openai_client.chat.completions.create(
            model=self.llm_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        
        try:
            content = response.choices[0].message.content
            data = json.loads(content)
            confidence = float(data.get("confidence", 0.0))
            reasoning = data.get("reasoning", "No reasoning provided.")
        except Exception as e:
            logger.error("llm_evaluation_failed", error=str(e))
            confidence = 0.0
            reasoning = "Failed to parse LLM response."
            
        is_approved = confidence >= 0.99
        status = "APPROVED" if is_approved else "ESCALATED"
        
        logger.info("compatibility_rag_evaluated", status=status, confidence=confidence)
        
        return CompatibilityCheckResponse(
            is_compatible=is_approved,
            confidence=confidence,
            method="rag_llm",
            reasoning=reasoning
        )
