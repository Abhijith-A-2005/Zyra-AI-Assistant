import sqlite3
import chromadb
from datetime import datetime
from config import CHROMA_PATH, SQLITE_PATH
import os
import logging

logger = logging.getLogger(__name__)

# ── Ensure memory directories exist ───────────────
os.makedirs(os.path.dirname(CHROMA_PATH), exist_ok=True)
os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)


class MemoryEngine:
    def __init__(self):
        self._init_chromadb()
        self._init_sqlite()
        logger.info("Memory engine ready")

    def _init_chromadb(self):
        """Semantic memory — facts, context, preferences"""
        from chromadb.utils import embedding_functions

        self.chroma_client = chromadb.PersistentClient(
            path=CHROMA_PATH
        )

        # Use local sentence transformer instead of
        # downloading from S3 — avoids timeout issues
        embedding_fn = embedding_functions.DefaultEmbeddingFunction()

        self.memory_collection = self.chroma_client.get_or_create_collection(
            name="zyra_memory",
            embedding_function=embedding_fn,
            metadata={"hnsw:space": "cosine"}
        )
        logger.info("ChromaDB ready")

    def _init_sqlite(self):
        """Structured memory — reminders, alarms, preferences"""
        self.db = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
        cursor = self.db.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                text        TEXT NOT NULL,
                remind_at   TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                fired       INTEGER DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS preferences (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversation_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                timestamp   TEXT NOT NULL
            )
        """)

        self.db.commit()
        logger.info("SQLite ready")

    def store_memory(self, text: str, metadata: dict = {}):
        """Store a fact or preference in semantic memory"""
        try:
            self.memory_collection.add(
                documents=[text],
                metadatas=[{**metadata,
                           "timestamp": datetime.now().isoformat()}],
                ids=[f"mem_{datetime.now().timestamp()}"]
            )
        except Exception as e:
            logger.error(f"Memory store error: {e}")

    def recall_memory(self, query: str, n=3, distance_threshold=0.7) -> list:
        try:
            count = self.memory_collection.count()
            if count == 0:
                return []
            results = self.memory_collection.query(
                query_texts=[query],
                n_results=min(n, count),
                include=["documents", "distances"]
            )
            if not results or not results["documents"]:
                return []
            docs = results["documents"][0]
            dists = results["distances"][0]
            # Only return memories that are actually relevant
            return [doc for doc, dist in zip(docs, dists) if dist < distance_threshold]
        except Exception as e:
            logger.error(f"Memory recall error: {e}")
            return []

    def log_conversation(self, role: str, content: str):
        """Log conversation turn to SQLite"""
        cursor = self.db.cursor()
        cursor.execute(
            "INSERT INTO conversation_log (role, content, timestamp)"
            " VALUES (?, ?, ?)",
            (role, content, datetime.now().isoformat())
        )
        self.db.commit()

    def set_preference(self, key: str, value: str):
        """Store a user preference"""
        cursor = self.db.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO preferences (key, value, updated_at)
            VALUES (?, ?, ?)
        """, (key, value, datetime.now().isoformat()))
        self.db.commit()

    def get_preference(self, key: str) -> str:
        """Retrieve a user preference"""
        cursor = self.db.cursor()
        cursor.execute(
            "SELECT value FROM preferences WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        return row[0] if row else None