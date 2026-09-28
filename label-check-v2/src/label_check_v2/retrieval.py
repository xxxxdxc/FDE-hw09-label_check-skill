"""Evidence discovery only. Retrieved text never executes a rule by itself."""
import json
import math
import os
import sqlite3
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path


TOPICS = Path(__file__).resolve().parents[2] / "knowledge" / "topics.json"
MODEL_NAME = "BAAI/bge-small-zh-v1.5"


def load_topics(path=None):
    with open(path or TOPICS, encoding="utf-8") as stream:
        return json.load(stream)


def _lexical_rank(query, documents, top_k):
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE docs USING fts5(title,text,tokenize='trigram')")
        connection.executemany("INSERT INTO docs(rowid,title,text) VALUES(?,?,?)",
                               ((i + 1, d["title"], d["text"]) for i, d in enumerate(documents)))
        if len(query.strip()) >= 3:
            try:
                rows = connection.execute("SELECT rowid,rank FROM docs WHERE docs MATCH ? ORDER BY rank LIMIT ?",
                                          ('"' + query.replace('"', '""') + '"', top_k)).fetchall()
            except sqlite3.OperationalError:
                rows = []
        else:
            rows = []
        ranked = [documents[rowid - 1] for rowid, _ in rows]
        if len(ranked) >= top_k:
            return ranked[:top_k]
        remaining = [doc for doc in documents if doc not in ranked]
        remaining.sort(key=lambda doc: SequenceMatcher(None, query,
                       doc["title"] + " " + doc["text"]).ratio(), reverse=True)
        return (ranked + remaining)[:top_k]
    finally:
        connection.close()


@lru_cache(maxsize=2)
def _embedding_model(cache):
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise RuntimeError("语义检索需安装可选依赖 fastembed；普通检索无需安装") from exc
    return TextEmbedding(model_name=MODEL_NAME, cache_dir=cache)


def _semantic_rank(query, documents, top_k, cache_dir=None):
    cache = cache_dir or os.environ.get("LABEL_CHECK_MODEL_CACHE") or str(Path.home() / ".cache" / "label-check-v2" / "models")
    model = _embedding_model(cache)
    texts = [doc["title"] + "。" + doc["text"] for doc in documents]
    vectors = list(model.embed([query] + texts))
    q = vectors[0]
    def score(vector):
        top = float(sum(float(a) * float(b) for a, b in zip(q, vector)))
        qnorm = math.sqrt(float(sum(float(x) * float(x) for x in q)))
        vnorm = math.sqrt(float(sum(float(x) * float(x) for x in vector)))
        return top / (qnorm * vnorm) if qnorm and vnorm else -1
    ranked = sorted(zip(documents, vectors[1:]), key=lambda pair: score(pair[1]), reverse=True)
    return [doc for doc, _ in ranked[:top_k]]


def search(query, method="lexical", top_k=3, documents=None, cache_dir=None):
    docs = documents if documents is not None else load_topics()
    if not query.strip():
        return []
    if method == "lexical":
        return _lexical_rank(query, docs, top_k)
    if method == "embedding":
        return _semantic_rank(query, docs, top_k, cache_dir)
    raise ValueError("method仅支持 lexical 或 embedding")


RETRIEVAL_CASES = [
    ("每份40克蛋白质如何统一到百克口径", "COURSE-100G-001"),
    ("蛋白脂肪碳水算千焦还有纤维", "GB28050-ENERGY"),
    ("糖在营养成分表NRV一栏留空", "GB28050-NRV"),
    ("检测出的真实含量比标签标注高多少", "GB28050-TOLERANCE"),
    ("包装正面宣称富含纤维是否达标", "GB28050-HIGH-FIBER"),
    ("高蛋白宣传需要多少克", "GB28050-HIGH-PROTEIN"),
    ("商超客户专供包材审核退回经验", "CHANNEL-REVIEW"),
    ("品牌调性意见和审核人争议怎么处理", "HUMAN-DECISION"),
    ("一包重四十克，蛋白2.9克，输出列头该怎么写", "COURSE-100G-001"),
    ("营养表写每份72克，课程交付应该换算成什么", "COURSE-100G-001"),
    ("营养表千焦数与蛋白质脂肪碳水算出来不一样", "GB28050-ENERGY"),
    ("膳食纤维参与热量计算吗，糖要重复加吗", "GB28050-ENERGY"),
    ("营养素参考值百分比的分母选什么", "GB28050-NRV"),
    ("糖这一行没有营养素参考值能填百分数吗", "GB28050-NRV"),
    ("化验结果与标签值不一致能相差多少", "GB28050-TOLERANCE"),
    ("钠的实测值大于标示值是不是超范围", "GB28050-TOLERANCE"),
    ("宣传富含膳食纤维但表里只有五克", "GB28050-HIGH-FIBER"),
    ("六克纤维每百克可以写高纤维吗", "GB28050-HIGH-FIBER"),
    ("包装正面写高蛋白，营养表每百克十一克", "GB28050-HIGH-PROTEIN"),
    ("富含蛋白质需要达到参考值的多少比例", "GB28050-HIGH-PROTEIN"),
    ("商超邮件通知专供标签新增文字", "CHANNEL-REVIEW"),
    ("某渠道审核退回了一次，怎么沉淀这条经验", "CHANNEL-REVIEW"),
    ("产品经理觉得字体不好看，谁来拍板", "HUMAN-DECISION"),
    ("两个审批人的意见冲突，系统能直接放行吗", "HUMAN-DECISION"),
]


def evaluate(method="lexical", cache_dir=None):
    rows = []
    for query, expected in RETRIEVAL_CASES:
        found = search(query, method=method, top_k=3, cache_dir=cache_dir)
        ids = [doc["id"] for doc in found]
        rows.append({"query": query, "expected": expected, "top1": ids[0] if ids else None,
                     "hit_at_1": bool(ids and ids[0] == expected), "hit_at_3": expected in ids})
    return {"method": method, "cases": len(rows),
            "hit_at_1": sum(x["hit_at_1"] for x in rows),
            "hit_at_3": sum(x["hit_at_3"] for x in rows), "details": rows,
            "warning": "仅24条人工编写的改写查询测试，不能代表真实标签准确率。"}
