import hashlib
import math
import re

VECTOR_SIZE = 128


def plain_text(text):
    text = re.sub(r"!\[([^]]*)]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_`>~]", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def chunk_text(text, max_chars=420):
    paragraphs = [plain_text(p).strip() for p in re.split(r"\n\s*\n", text) if plain_text(p).strip()]
    pieces = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            pieces.append(paragraph)
            continue
        sentences = [s.strip() for s in re.split(r"(?<=[。！？!?；;])\s*", paragraph) if s.strip()]
        current = ""
        for sentence in sentences:
            while len(sentence) > max_chars:
                if current:
                    pieces.append(current)
                    current = ""
                pieces.append(sentence[:max_chars])
                sentence = sentence[max_chars:]
            if current and len(current) + len(sentence) + 1 > max_chars:
                pieces.append(current)
                current = sentence
            else:
                current = f"{current}\n{sentence}" if current else sentence
        if current:
            pieces.append(current)

    chunks = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > max_chars:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def embed(text):
    tokens = re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]", text.lower())
    features = [(token, 0.75) for token in tokens]
    features += [("".join(tokens[i:i + 2]), 1.25) for i in range(len(tokens) - 1)]
    vector = [0.0] * VECTOR_SIZE
    for token, weight in features:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % VECTOR_SIZE
        vector[index] += weight if digest[4] & 1 else -weight
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def make_snippet(text, query, width=190):
    text = re.sub(r"\s+", " ", text).strip()
    position = text.lower().find(query.lower()) if query else -1
    if position < 0:
        return text[:width] + ("…" if len(text) > width else "")
    start = max(0, position - width // 3)
    end = min(len(text), start + width)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def make_cards(content, title, limit=3):
    sentences = [s.strip() for s in re.split(r"(?<=[。！？!?])\s*", plain_text(content)) if 18 <= len(s.strip()) <= 240]
    if not sentences:
        sentences = [chunk[:220] for chunk in chunk_text(content) if len(chunk) >= 8]

    def rank(item):
        index, sentence = item
        score = sum(keyword in sentence for keyword in ("是", "指", "因为", "通过", "用于", "包括", "影响", "可以"))
        score += min(len(sentence), 120) / 120
        return score, -index

    selected = sorted(sorted(enumerate(sentences), key=rank, reverse=True)[:limit])
    cards = []
    for index, sentence in selected:
        match = re.match(r"^([^，。；：]{2,24}?)(?:是指|指的是|是|指)(.+)$", sentence)
        if match:
            question = f"请解释“{match.group(1).strip()}”的核心含义。"
        else:
            question = f"请复述《{title}》中的第 {index + 1} 个核心要点。"
        cards.append({"question": question, "answer": sentence})
    return cards
