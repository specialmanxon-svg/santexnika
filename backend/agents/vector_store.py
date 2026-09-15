"""Vector store wrapper for pgvector-based similarity search."""
import structlog
from openai import AsyncOpenAI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from pgvector.sqlalchemy import Vector

from config import settings
from models.embedding import TechnicalEmbedding

logger = structlog.get_logger()


class VectorStore:
    """Wrapper for pgvector similarity search operations."""
    
    def __init__(self):
        self.openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.embedding_model = settings.openai_embedding_model
    
    async def generate_embedding(self, text: str) -> list[float]:
        """Generate embedding vector using OpenAI API."""
        response = await self.openai_client.embeddings.create(
            model=self.embedding_model,
            input=text
        )
        return response.data[0].embedding
    
    async def similarity_search(
        self, 
        query: str, 
        session: AsyncSession, 
        k: int = 4
    ) -> list[dict]:
        """Search for similar documents using cosine distance."""
        query_embedding = await self.generate_embedding(query)
        
        # Use pgvector cosine distance operator <=> 
        stmt = text("""
            SELECT id, product_id, doc_chunk, source_document,
                   1 - (embedding <=> :query_vec::vector) AS similarity
            FROM technical_embeddings
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> :query_vec::vector
            LIMIT :k
        """)
        
        result = await session.execute(
            stmt, 
            {"query_vec": str(query_embedding), "k": k}
        )
        rows = result.fetchall()
        
        return [
            {
                "id": str(row.id),
                "product_id": str(row.product_id),
                "content": row.doc_chunk,
                "source": row.source_document,
                "similarity": float(row.similarity)
            }
            for row in rows
        ]
    
    async def add_embedding(
        self,
        session: AsyncSession,
        product_id: str,
        doc_chunk: str,
        source_document: str
    ) -> str:
        """Add a new document chunk with its embedding."""
        embedding_vector = await self.generate_embedding(doc_chunk)
        
        new_embedding = TechnicalEmbedding(
            product_id=product_id,
            doc_chunk=doc_chunk,
            source_document=source_document,
            embedding=embedding_vector
        )
        session.add(new_embedding)
        await session.commit()
        await session.refresh(new_embedding)
        
        logger.info("embedding_added", 
                    product_id=product_id, 
                    source=source_document,
                    chunk_length=len(doc_chunk))
        
        return str(new_embedding.id)
