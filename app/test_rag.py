from pathlib import Path
from llama_index.core import VectorStoreIndex, SimpleDirectoryReader, Settings
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.readers.database import DatabaseReader


# Initialize the embedding model
embed_model = OllamaEmbedding(
    model_name="nomic-embed-text",
    request_timeout=300.0,  # Increased timeout for large documents
)

# Initialize the LLM with optimized settings
llm = Ollama(
 #   model="llama3.1:latest",  # Confirm with `ollama list`
    model="llama3.2:3b",  # Confirm with `ollama list`
    request_timeout=300.0,
    temperature=0.1,          # Lower temperature for more factual responses
)

# Set global configurations
Settings.embed_model = embed_model
Settings.llm = llm

def load_and_index_documents(data_dir="data"):
    """Load documents and create vector index"""

    # Check if data directory exists
    if not Path(data_dir).exists():
        raise FileNotFoundError(f"Data directory '{data_dir}' not found. Please create it and add your PDF files.")

    # Load documents from the data folder
    docs = SimpleDirectoryReader(data_dir).load_data()

    if not docs:
        raise ValueError(f"No documents found in {data_dir}")


    # Build vector index from documents
    index = VectorStoreIndex.from_documents(docs, embed_model=embed_model)

    return index


def load_and_index_from_db():
    """Ανάγνωση δεδομένων από PostgreSQL και δημιουργία index"""
    
    # 1. Σύνδεση στη βάση (χρησιμοποίησε τις μεταβλητές που ήδη έχεις)
    # Μορφή: postgresql://user:password@host:port/db_name
  #  db_uri = f"postgresql://{user}:{password}@{host}:{port}/{db_name}"
    db_uri = f"postgresql://admin:31.12.1969@192.168.4.20:11434/FinanceDB"
    
    db_reader = DatabaseReader(uri=db_uri)

    # 2. Ορισμός του Query που θα φέρει τα δεδομένα για το RAG
    # Παράδειγμα: Φέρνουμε τα ονόματα των μετοχών και τα ιστορικά τους σχόλια ή τιμές
    query = """
        SELECT 
            concat('Security: ', s.security_name, ' - Price: ', h.price_close, ' - Date: ', h.price_date) as text
        FROM securities s
        JOIN historical_prices h ON s.id = h.security_id
        --LIMIT 500;
    """

    # 3. Φόρτωση δεδομένων (το DatabaseReader τα μετατρέπει αυτόματα σε Documents)
    docs = db_reader.load_data(query=query)

    if not docs:
        raise ValueError("Δεν βρέθηκαν δεδομένα στη βάση δεδομένων.")

    # 4. Δημιουργία Index
    index = VectorStoreIndex.from_documents(docs)

    return index

def create_query_engine(index, similarity_top_k=3):
    """Create query engine with specified retrieval parameters"""

    query_engine = index.as_query_engine(
        llm=llm,
        similarity_top_k=similarity_top_k,  # Number of relevant chunks to retrieve
        response_mode="compact"             # Compact response generation
    )

    return query_engine

def test_rag_system():
    """Test the RAG system with sample queries"""

    try:
        # Load documents and create index
        #index = load_and_index_documents()
        index = load_and_index_from_db()

        # Create query engine
        query_engine = create_query_engine(index)

        # Sample test queries
        test_queries = [
            "Summarize this document in 3 lines",
            "What are the main topics covered in these documents?",
        ]

        print("RAG System Test Results")
        print("=" * 50)

        for i, query in enumerate(test_queries, 1):
            print(f"\nTest {i}: {query}")
            print("-" * 40)

            try:
                response = query_engine.query(query)
                print(f"Response: {response}")
                print(f"Status: SUCCESS")
            except Exception as e:
                print(f"Error: {str(e)}")
                print(f"Status: FAILED")

            print("-" * 40)

        return True

    except Exception as e:
        print(f"System Error: {str(e)}")
        return False

# Main execution
if __name__ == "__main__":

    print("Starting RAG Pipeline Test...")

    # Test the complete system
    success = test_rag_system()

    if success:
        print("\nRAG system is working correctly!")
        print("You can now use the query_engine to ask questions about your documents.")
    else:
        print("\nRAG system test failed. Check the error messages above.")
