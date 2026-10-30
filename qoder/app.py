import json
import os
from pathlib import Path

from flask import Flask, jsonify, request, send_file

from store import StudyStore

ROOT = Path(__file__).resolve().parent


def create_app(data_dir=None):
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
    data_dir = data_dir or os.environ.get("ZHISHU_DATA", str(ROOT / "data" / "seekdb"))
    store = StudyStore(data_dir)

    @app.errorhandler(ValueError)
    def bad_request(error):
        return jsonify({"error": str(error)}), 400

    @app.errorhandler(KeyError)
    def not_found(error):
        return jsonify({"error": error.args[0]}), 404

    @app.errorhandler(413)
    def too_large(_):
        return jsonify({"error": "请求内容不能超过 2 MB"}), 413

    @app.get("/")
    def index():
        return send_file(ROOT / "index.html")

    @app.get("/app.js")
    def javascript():
        return send_file(ROOT / "app.js")

    @app.get("/styles.css")
    def stylesheet():
        return send_file(ROOT / "styles.css")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "engine": "seekdb embedded", "database": "zhishu_v1"})

    @app.get("/api/overview")
    def overview():
        return jsonify(store.overview())

    @app.get("/api/notes")
    def notes():
        return jsonify({"notes": store.list_notes()})

    @app.post("/api/notes")
    def create_note():
        data = request.get_json(silent=True) or {}
        title = str(data.get("title", "")).strip()
        content = str(data.get("content", "")).strip()
        subject = str(data.get("subject", "未分类")).strip() or "未分类"
        source = str(data.get("source", "手动录入")).strip() or "手动录入"
        if not title or len(title) > 120:
            raise ValueError("标题须为 1–120 个字符")
        if not content or len(content) > 100000:
            raise ValueError("正文须为 1–100000 个字符")
        if len(subject) > 40 or len(source) > 200:
            raise ValueError("分类或来源过长")
        return jsonify({"note": store.add_note(title, content, subject, source)}), 201

    @app.get("/api/notes/<note_id>")
    def note_detail(note_id):
        note = store.get_note(note_id)
        return jsonify({"note": note, "cards": store.list_cards(note_id)})

    @app.delete("/api/notes/<note_id>")
    def remove_note(note_id):
        store.delete_note(note_id)
        return jsonify({"ok": True})

    @app.post("/api/notes/<note_id>/cards")
    def create_cards(note_id):
        cards, created = store.create_cards(note_id)
        return jsonify({"cards": cards, "created": created})

    @app.post("/api/search")
    def search():
        data = request.get_json(silent=True) or {}
        query = str(data.get("query", "")).strip()
        if not query or len(query) > 200:
            raise ValueError("检索词须为 1–200 个字符")
        limit = data.get("limit", 6)
        if not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("返回数量须为 1–20")
        return jsonify(store.search(query, data.get("mode", "hybrid"), str(data.get("subject", "")), limit))

    @app.get("/api/review")
    def review():
        cards = store.list_cards()
        current = store.overview()
        return jsonify({"cards": cards, "due": current["due"], "mastery": current["mastery"]})

    @app.post("/api/review/<card_id>")
    def review_card(card_id):
        data = request.get_json(silent=True) or {}
        return jsonify({"card": store.review_card(card_id, data.get("rating", ""))})

    @app.post("/api/demo")
    def load_demo():
        if store.list_notes():
            raise ValueError("知识库已有内容，无需重复载入示例")
        samples = json.loads((ROOT / "sample_notes.json").read_text(encoding="utf-8"))
        for sample in samples:
            note = store.add_note(**sample)
            store.create_cards(note["note_id"])
        return jsonify({"notes": store.list_notes()}), 201

    @app.get("/api/export")
    def export():
        return app.response_class(
            store.export_markdown(),
            mimetype="text/markdown; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=zhishu-notes.md"},
        )

    return app


if __name__ == "__main__":
    create_app().run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
