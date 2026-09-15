#!/usr/bin/env python3
"""Vectorize technical plumbing catalogs for RAG AI Sales Agent.

Reads PDF documents from a specified directory, splits them into chunks,
generates embeddings, and stores them in the technical_embeddings table.

Usage:
    python scripts/embed_catalogs.py --input-dir ./catalogs/
"""
import asyncio
import argparse
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from config import settings
from models.base import Base
from agents.vector_store import VectorStore

# Text chunking parameters
CHUNK_SIZE = 1000  # characters per chunk
CHUNK_OVERLAP = 200  # character overlap between chunks


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk.strip())
        start = end - overlap
    return chunks


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract text from a PDF file.
    
    Uses PyPDF2 if available, falls back to basic extraction.
    """
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        return text
    except ImportError:
        print("⚠️  PyPDF2 not installed. Install with: pip install PyPDF2")
        print("   Attempting to read as plain text...")
        with open(pdf_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()


async def process_document(
    file_path: Path,
    product_id: str,
    vector_store: VectorStore,
    session
) -> int:
    """Process a single document: extract text, chunk, embed."""
    print(f"\n  📄 Processing: {file_path.name}")
    
    text = extract_text_from_pdf(str(file_path))
    if not text.strip():
        print(f"    ⚠️  No text extracted from {file_path.name}")
        return 0
    
    chunks = chunk_text(text)
    print(f"    📊 {len(chunks)} chunks generated ({len(text)} chars total)")
    
    embedded = 0
    for i, chunk in enumerate(chunks):
        try:
            await vector_store.add_embedding(
                session=session,
                product_id=product_id,
                doc_chunk=chunk,
                source_document=file_path.name
            )
            embedded += 1
            if (i + 1) % 10 == 0:
                print(f"    ✅ {i + 1}/{len(chunks)} chunks embedded")
        except Exception as e:
            print(f"    ❌ Chunk {i+1} failed: {e}")
            continue
    
    print(f"    ✅ {embedded}/{len(chunks)} chunks successfully embedded")
    return embedded


async def main(input_dir: str, product_id: str = None):
    print("="*60)
    print("🧬 Векторизация технических каталогов сантехники")
    print(f"📁 Директория: {input_dir}")
    print("="*60)
    
    input_path = Path(input_dir)
    if not input_path.exists():
        print(f"❌ Директория {input_dir} не найдена")
        return
    
    pdf_files = list(input_path.glob("*.pdf")) + list(input_path.glob("*.PDF"))
    txt_files = list(input_path.glob("*.txt"))
    all_files = pdf_files + txt_files
    
    if not all_files:
        print("⚠️  Нет PDF/TXT файлов в указанной директории")
        return
    
    print(f"  📚 Найдено файлов: {len(all_files)}")
    
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    vector_store = VectorStore()
    
    # Create tables if needed
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    total_embedded = 0
    async with async_session() as session:
        for file_path in all_files:
            # Use a default product_id if not specified
            pid = product_id or "00000000-0000-0000-0000-000000000000"
            count = await process_document(file_path, pid, vector_store, session)
            total_embedded += count
    
    await engine.dispose()
    
    print(f"\n{'='*60}")
    print(f"✅ Векторизация завершена!")
    print(f"   Всего эмбеддингов: {total_embedded}")
    print(f"   Обработано файлов: {len(all_files)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vectorize technical catalogs")
    parser.add_argument("--input-dir", required=True, help="Directory containing PDF/TXT catalogs")
    parser.add_argument("--product-id", default=None, help="Product UUID to associate embeddings with")
    args = parser.parse_args()
    asyncio.run(main(args.input_dir, args.product_id))
