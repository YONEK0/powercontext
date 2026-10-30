import json
import threading
import time
import uuid
from datetime import datetime, timedelta

import pyseekdb
from pyseekdb import FulltextIndexConfig, HNSWConfiguration, Schema, VectorIndexConfig

from text_utils import VECTOR_SIZE, chunk_text, embed, make_cards, make_snippet


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


class StudyStore:
    def __init__(self, path):
        self.client = pyseekdb.Client(path=path, database="test")
        schema = Schema(
            vector_index=VectorIndexConfig(
                hnsw=HNSWConfiguration(dimension=VECTOR_SIZE, distance="cosine"),
                embedding_function=None,
            ),
            fulltext_index=FulltextIndexConfig(
                analyzer="ngram", properties={"ngram_token_size": 2}
            ),
        )
        self.collection = self.client.get_or_create_collection("zhishu_v1", schema=schema)
        self._lock = threading.RLock()

    @staticmethod
    def _where(kind, **values):
        conditions = [{"kind": kind}]
        conditions.extend({key: value} for key, value in values.items() if value)
        return conditions[0] if len(conditions) == 1 else {"$and": conditions}

    def _add_event_locked(self, event_type, summary, detail="", note_id=""):
        event_id = f"event:{uuid.uuid4().hex}"
        metadata = {
            "kind": "event",
            "event_id": event_id,
            "event_type": event_type,
            "summary": summary,
            "detail": detail,
            "created_at": now_iso(),
        }
        if note_id:
            metadata["note_id"] = note_id
        document = f"{summary}\n{detail}"
        self.collection.add(
            ids=event_id,
            documents=document,
            embeddings=embed(document),
            metadatas=metadata,
        )
        return event_id

    def add_note(self, title, content, subject="未分类", source="手动录入"):
        with self._lock:
            note_id = uuid.uuid4().hex
            created_at = now_iso()
            chunks = chunk_text(content)
            ids = [f"note:{note_id}:{index}" for index in range(len(chunks))]
            metadatas = [
                {
                    "kind": "note",
                    "note_id": note_id,
                    "title": title,
                    "subject": subject,
                    "source": source,
                    "chunk_index": index,
                    "created_at": created_at,
                }
                for index in range(len(chunks))
            ]
            self.collection.add(
                ids=ids,
                documents=chunks,
                embeddings=[embed(f"{title}\n{chunk}") for chunk in chunks],
                metadatas=metadatas,
            )
            self._add_event_locked(
                "import", f"导入《{title}》", f"{len(chunks)} 个笔记片段已写入 seekdb", note_id
            )
            self.collection.refresh_index()
            return self.get_note(note_id)

    def list_notes(self):
        with self._lock:
            result = self.collection.get(
                where=self._where("note"), limit=5000, include=["documents", "metadatas"]
            )
            notes = {}
            for document, metadata in zip(result.get("documents", []), result.get("metadatas", [])):
                note_id = metadata["note_id"]
                note = notes.setdefault(
                    note_id,
                    {
                        "note_id": note_id,
                        "title": metadata["title"],
                        "subject": metadata["subject"],
                        "source": metadata.get("source", ""),
                        "created_at": metadata["created_at"],
                        "chunks": 0,
                        "preview": document,
                    },
                )
                note["chunks"] += 1
            return sorted(notes.values(), key=lambda item: item["created_at"], reverse=True)

    def get_note(self, note_id):
        with self._lock:
            result = self.collection.get(
                where=self._where("note", note_id=note_id),
                limit=1000,
                include=["documents", "metadatas"],
            )
            rows = sorted(
                zip(result.get("documents", []), result.get("metadatas", [])),
                key=lambda row: row[1]["chunk_index"],
            )
            if not rows:
                raise KeyError("笔记不存在")
            metadata = rows[0][1]
            return {
                "note_id": note_id,
                "title": metadata["title"],
                "subject": metadata["subject"],
                "source": metadata.get("source", ""),
                "created_at": metadata["created_at"],
                "chunks": len(rows),
                "content": "\n\n".join(document for document, _ in rows),
            }

    def delete_note(self, note_id):
        with self._lock:
            note = self.get_note(note_id)
            self.collection.delete(where={"note_id": note_id})
            self._add_event_locked("delete", f"删除《{note['title']}》", "笔记及关联复习卡已删除")
            self.collection.refresh_index()

    def create_cards(self, note_id):
        with self._lock:
            existing = self.list_cards(note_id)
            if existing:
                return existing, 0
            note = self.get_note(note_id)
            cards = make_cards(note["content"], note["title"])
            created_at = now_iso()
            ids = [f"card:{uuid.uuid4().hex}" for _ in cards]
            documents = [card["answer"] for card in cards]
            metadatas = [
                {
                    "kind": "card",
                    "card_id": card_id,
                    "note_id": note_id,
                    "title": note["title"],
                    "subject": note["subject"],
                    "question": card["question"],
                    "due_at": created_at,
                    "interval_days": 1,
                    "reviews": 0,
                    "mastery": 0,
                    "last_result": "未复习",
                    "created_at": created_at,
                }
                for card_id, card in zip(ids, cards)
            ]
            self.collection.add(
                ids=ids,
                documents=documents,
                embeddings=[embed(f"{card['question']}\n{card['answer']}") for card in cards],
                metadatas=metadatas,
            )
            self._add_event_locked(
                "cards", f"为《{note['title']}》生成 {len(cards)} 张复习卡", "复习卡已写入 seekdb", note_id
            )
            self.collection.refresh_index()
            return self.list_cards(note_id), len(cards)

    def list_cards(self, note_id=""):
        with self._lock:
            result = self.collection.get(
                where=self._where("card", note_id=note_id),
                limit=5000,
                include=["documents", "metadatas"],
            )
            cards = []
            for document, metadata in zip(result.get("documents", []), result.get("metadatas", [])):
                cards.append({**metadata, "answer": document})
            return sorted(cards, key=lambda item: (item["due_at"], item["created_at"]))

    def review_card(self, card_id, rating):
        with self._lock:
            result = self.collection.get(ids=card_id, include=["documents", "metadatas"])
            if not result.get("ids"):
                raise KeyError("复习卡不存在")
            metadata = dict(result["metadatas"][0])
            old_interval = int(metadata.get("interval_days", 1))
            mastery = int(metadata.get("mastery", 0))
            if rating == "again":
                interval, mastery, label = 1, max(0, mastery - 10), "模糊"
            elif rating == "good":
                interval, mastery, label = max(1, round(old_interval * 1.8)), min(100, mastery + 12), "熟悉"
            elif rating == "easy":
                interval, mastery, label = max(2, round(old_interval * 2.5)), min(100, mastery + 22), "掌握"
            else:
                raise ValueError("不支持的复习反馈")
            metadata.update(
                {
                    "due_at": (datetime.now() + timedelta(days=interval)).isoformat(timespec="seconds"),
                    "interval_days": interval,
                    "reviews": int(metadata.get("reviews", 0)) + 1,
                    "mastery": mastery,
                    "last_result": label,
                }
            )
            self.collection.update(ids=card_id, metadatas=metadata)
            self._add_event_locked(
                "review",
                f"复习《{metadata['title']}》：{label}",
                f"掌握度 {mastery}%，{interval} 天后再次出现",
                metadata["note_id"],
            )
            self.collection.refresh_index()
            return {**metadata, "answer": result["documents"][0]}

    def search(self, query, mode="hybrid", subject="", limit=6):
        with self._lock:
            started = time.perf_counter()
            where = self._where("note", subject=subject)
            candidate_count = max(10, limit)
            if mode == "vector":
                raw = self.collection.query(
                    query_embeddings=embed(query),
                    n_results=limit,
                    where=where,
                    include=["documents", "metadatas"],
                )
            elif mode == "fulltext":
                raw = self.collection.hybrid_search(
                    query={
                        "where_document": {"$contains": query},
                        "where": where,
                        "n_results": candidate_count,
                    },
                    n_results=limit,
                    include=["documents", "metadatas"],
                )
            elif mode == "hybrid":
                raw = self.collection.hybrid_search(
                    query={
                        "where_document": {"$contains": query},
                        "where": where,
                        "n_results": candidate_count,
                    },
                    knn={
                        "query_embeddings": embed(query),
                        "where": where,
                        "n_results": candidate_count,
                    },
                    rank={"rrf": {"rank_window_size": 60, "rank_constant": 60}},
                    n_results=limit,
                    include=["documents", "metadatas"],
                )
            else:
                raise ValueError("检索模式须为 hybrid、vector 或 fulltext")

            results = []
            ids = raw.get("ids", [[]])[0]
            documents = raw.get("documents", [[]])[0]
            metadatas = raw.get("metadatas", [[]])[0]
            distances = raw.get("distances", [[]])[0]
            for index, (chunk_id, document, metadata) in enumerate(zip(ids, documents, metadatas)):
                distance = distances[index] if index < len(distances) else 0
                results.append(
                    {
                        "chunk_id": chunk_id,
                        "note_id": metadata["note_id"],
                        "title": metadata["title"],
                        "subject": metadata["subject"],
                        "source": metadata.get("source", ""),
                        "snippet": make_snippet(document, query),
                        "score": round(float(distance), 6),
                    }
                )
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            trace = {
                "engine": "seekdb embedded",
                "mode": mode,
                "vector_index": "HNSW · cosine · 128d",
                "fulltext_index": "ngram · 2",
                "fusion": "RRF" if mode == "hybrid" else "未启用",
                "subject": subject or "全部",
                "elapsed_ms": elapsed,
                "result_ids": ids,
            }
            event_id = self._add_event_locked(
                "search", f"检索“{query}”命中 {len(results)} 段", json.dumps(trace, ensure_ascii=False)
            )
            self.collection.refresh_index()
            trace["event_id"] = event_id
            return {"results": results, "trace": trace}

    def overview(self):
        with self._lock:
            result = self.collection.get(limit=10000, include=["metadatas"])
            metadatas = result.get("metadatas", [])
            notes = [item for item in metadatas if item.get("kind") == "note"]
            cards = [item for item in metadatas if item.get("kind") == "card"]
            events = [item for item in metadatas if item.get("kind") == "event"]
            unique_notes = {item["note_id"]: item for item in notes}
            current_time = now_iso()
            subjects = {}
            for item in unique_notes.values():
                subjects[item["subject"]] = subjects.get(item["subject"], 0) + 1
            recent = sorted(events, key=lambda item: item["created_at"], reverse=True)[:8]
            return {
                "notes": len(unique_notes),
                "chunks": len(notes),
                "cards": len(cards),
                "due": sum(item["due_at"] <= current_time for item in cards),
                "reviews": sum(item.get("event_type") == "review" for item in events),
                "searches": sum(item.get("event_type") == "search" for item in events),
                "mastery": round(sum(int(item.get("mastery", 0)) for item in cards) / len(cards)) if cards else 0,
                "subjects": subjects,
                "recent": [
                    {
                        "event_type": item["event_type"],
                        "summary": item["summary"],
                        "detail": item.get("detail", ""),
                        "created_at": item["created_at"],
                    }
                    for item in recent
                ],
            }

    def export_markdown(self):
        notes = self.list_notes()
        sections = ["# 知溯笔记导出", ""]
        for item in notes:
            note = self.get_note(item["note_id"])
            sections.extend(
                [f"## {note['title']}", "", f"- 分类：{note['subject']}", f"- 来源：{note['source']}", "", note["content"], ""]
            )
        return "\n".join(sections)
