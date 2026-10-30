import json
from pathlib import Path

from store import StudyStore

ROOT = Path(__file__).resolve().parent


if __name__ == "__main__":
    store = StudyStore(ROOT / "data" / "seekdb")
    if store.list_notes():
        print("知识库已有内容，未重复载入。")
    else:
        samples = json.loads((ROOT / "sample_notes.json").read_text(encoding="utf-8"))
        for sample in samples:
            note = store.add_note(**sample)
            _, created = store.create_cards(note["note_id"])
            print(f"已导入《{note['title']}》，生成 {created} 张复习卡")
        print("示例知识库已就绪，运行 python3 app.py 即可体验。")
