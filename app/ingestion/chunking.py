"""
Chunking: split each product into retrievable text pieces.

Two strategies are combined — this is a good discussion talking point:

1. STRUCTURE-AWARE chunking (by section). A product has natural sections:
     - "profile":     a synthetic summary of the structured attributes
                      ("Northline Shift — work boot, olive, waterproof, steel toe, $244.99")
     - "description": marketing copy
     - "reviews":     customer voice ("kept my feet dry all day")
   Keeping sections separate means a review mention can't be diluted by marketing copy,
   and we can tell the user WHY a boot matched (the evidence snippet).

2. RECURSIVE CHARACTER chunking within a section, with overlap — the same algorithm as
   LangChain's RecursiveCharacterTextSplitter: try to split on paragraph, then sentence,
   then word boundaries, so chunks don't cut words or sentences in half. Overlap keeps
   context that would otherwise be lost at a boundary.

Why 320 chars? Product text is short and specific. Small chunks give precise matches;
the cost (more vectors) is trivial at catalog scale. For long documents (manuals,
policies) you'd use ~800-1200 tokens. The right size is something you TUNE with the
eval harness (app/evaluation), not guess.
"""
from __future__ import annotations

from app.core.models import Boot, Chunk
from langchain_text_splitters import RecursiveCharacterTextSplitter

SEPARATORS = ["\n\n", "\n", ". ", "; ", ", ", " "]

def recursive_split(
    text: str,
    chunk_size: int,
    overlap: int,
    separators: list[str] | None = None,
) -> list[str]:
    splitters = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=[*(SEPARATORS if separators is None else separators), ""],
        length_function=len,
        keep_separator=True,
    )
    return splitters.split_text(text.strip())


# Profile text into a natural language sentence for embedding and BM25 indexing.
# This function takes a Boot object and converts its structured attributes into a single descriptive sentence.
# Returns:
# - A string containing the natural language description of the boot.
def profile_text(boot: Boot) -> str:
    """Turn structured attributes into a sentence. Embedding models and BM25 both
    benefit from seeing attributes as natural language."""
    parts = [
        f"{boot.name} by {boot.brand} (SKU {boot.id})",
        f"{boot.category} boot",
        f"color {boot.color}",
        boot.material,
        "waterproof" if boot.waterproof else "water-resistant only",
        f"{boot.safety_toe} safety toe" if boot.safety_toe != "none" else "soft toe",
        f"price ${boot.price:.2f}",
        "features: " + ", ".join(boot.features),
    ]
    return ". ".join(parts)

# Chunk a boot into smaller pieces for indexing or processing
# Returns a list of Chunk objects representing the boot's profile, description, and reviews split into manageable pieces.
# Parameters:
# - boot: The Boot object to be chunked.
# - chunk_size: The maximum size of each chunk.
# - overlap: The number of characters to overlap between consecutive chunks.
# Returns:
# - A list of Chunk objects containing the split text of the boot's profile, description, and reviews.
def chunk_boot(boot: Boot, chunk_size: int = 320, overlap: int = 60) -> list[Chunk]:
    chunks: list[Chunk] = []
    sections = {
        "profile": profile_text(boot),
        "description": boot.description,
        "reviews": "\n".join(boot.reviews),
    }
    for section, text in sections.items():
        for i, piece in enumerate(recursive_split(text, chunk_size, overlap)):
            # Create a Chunk object for each piece of text and add it to the list of chunks.
            # The chunk_id is constructed using the boot's ID, the section name, and the index of the piece.
            # The text of the chunk is the piece obtained from recursive_split.
            chunks.append(Chunk(chunk_id=f"{boot.id}:{section}:{i}", boot_id=boot.id, section=section, text=piece))
    return chunks
