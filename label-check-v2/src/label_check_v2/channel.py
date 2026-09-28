"""L2 channel cards and L3 human decision log.

Only sanitized requirements are stored. Images and full label inputs are never
written to the knowledge database. A candidate cannot affect hard checks.
"""
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .core import finding


SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
  id INTEGER PRIMARY KEY,
  channel TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT '*',
  sku TEXT NOT NULL DEFAULT '*',
  requirement TEXT NOT NULL,
  matcher TEXT NOT NULL CHECK(matcher IN ('contains','forbid','document','manual')),
  target TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  source_kind TEXT NOT NULL CHECK(source_kind IN ('channel_doc','review_feedback','internal_sop')),
  valid_from TEXT NOT NULL,
  valid_to TEXT,
  status TEXT NOT NULL DEFAULT 'candidate' CHECK(status IN ('candidate','confirmed','rejected')),
  submitted_at TEXT NOT NULL,
  reviewed_by TEXT,
  reviewed_at TEXT,
  review_note TEXT
);
CREATE TABLE IF NOT EXISTS human_decisions (
  id INTEGER PRIMARY KEY,
  question_summary TEXT NOT NULL,
  decision TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  decided_by TEXT NOT NULL,
  decided_at TEXT NOT NULL
);
"""


class ChannelStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        try:
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(requirement, target, channel, tokenize='trigram')")
            self.db.execute("DELETE FROM cards_fts")
            self.db.execute("INSERT INTO cards_fts(rowid,requirement,target,channel) SELECT id,requirement,target,channel FROM cards WHERE status='confirmed'")
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False
        self.db.commit()

    def close(self):
        self.db.close()

    def submit_card(self, channel, requirement, matcher, target, source_ref,
                    source_kind="channel_doc", category="*", sku="*", valid_from=None, valid_to=None):
        if not all((channel.strip(), requirement.strip(), target.strip(), source_ref.strip())):
            raise ValueError("渠道、要求、目标及来源不能为空")
        if matcher not in ("contains", "forbid", "document", "manual"):
            raise ValueError("不支持的匹配方式")
        if source_kind not in ("channel_doc", "review_feedback", "internal_sop"):
            raise ValueError("来源类型无效")
        start = valid_from or date.today().isoformat()
        date.fromisoformat(start)
        if valid_to:
            if date.fromisoformat(valid_to) < date.fromisoformat(start):
                raise ValueError("失效日期早于生效日期")
        cur = self.db.execute(
            """INSERT INTO cards(channel,category,sku,requirement,matcher,target,
               source_ref,source_kind,valid_from,valid_to,submitted_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (channel.strip(), category.strip() or "*", sku.strip() or "*", requirement.strip(),
             matcher, target.strip(), source_ref.strip(), source_kind, start, valid_to,
             datetime.now().isoformat(timespec="seconds")),
        )
        self.db.commit()
        return cur.lastrowid

    def review_card(self, card_id, decision, reviewer, note=""):
        if decision not in ("confirmed", "rejected"):
            raise ValueError("只能确认或驳回")
        if not reviewer.strip():
            raise ValueError("必须填写审核人")
        row = self.db.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        if row is None:
            raise ValueError("渠道卡不存在")
        if row["status"] != "candidate":
            raise ValueError("渠道卡已经审核，不可再次晋级")
        self.db.execute("UPDATE cards SET status=?,reviewed_by=?,reviewed_at=?,review_note=? WHERE id=?",
                        (decision, reviewer.strip(), datetime.now().isoformat(timespec="seconds"), note, card_id))
        if decision == "confirmed" and self.fts:
            self.db.execute("INSERT INTO cards_fts(rowid,requirement,target,channel) VALUES(?,?,?,?)",
                            (card_id, row["requirement"], row["target"], row["channel"]))
        self.db.commit()

    def record_human_decision(self, question_summary, decision, source_ref, reviewer):
        if not all(x and str(x).strip() for x in (question_summary, decision, source_ref, reviewer)):
            raise ValueError("人工决策记录必须有问题、结论、依据和审核人")
        cur = self.db.execute(
            "INSERT INTO human_decisions(question_summary,decision,source_ref,decided_by,decided_at) VALUES(?,?,?,?,?)",
            (question_summary.strip(), decision.strip(), source_ref.strip(), reviewer.strip(),
             datetime.now().isoformat(timespec="seconds")),
        )
        self.db.commit()
        return cur.lastrowid

    def list_cards(self, status=None):
        query = "SELECT * FROM cards" + (" WHERE status=?" if status else "") + " ORDER BY id"
        return [dict(row) for row in self.db.execute(query, (status,) if status else ())]

    def search(self, query, limit=10):
        if not query.strip():
            return []
        if self.fts and len(query.strip()) >= 3:
            try:
                rows = self.db.execute(
                    "SELECT c.* FROM cards_fts f JOIN cards c ON c.id=f.rowid WHERE cards_fts MATCH ? AND c.status='confirmed' LIMIT ?",
                    ('"' + query.replace('"', '""') + '"', limit),
                ).fetchall()
                return [dict(row) for row in rows]
            except sqlite3.OperationalError:
                pass
        like = "%" + query.strip().replace("%", "\\%").replace("_", "\\_") + "%"
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM cards WHERE status='confirmed' AND (requirement LIKE ? ESCAPE '\\' OR target LIKE ? ESCAPE '\\' OR channel LIKE ? ESCAPE '\\') LIMIT ?",
            (like, like, like, limit),
        )]

    def evaluate(self, channel, category="*", sku="*", label_text="", documents=None, on_date=None):
        if not channel:
            return []
        documents = documents or []
        today = on_date or date.today().isoformat()
        date.fromisoformat(today)
        rows = self.db.execute(
            """SELECT * FROM cards WHERE channel=? AND (category='*' OR category=?)
               AND (sku='*' OR sku=?) AND valid_from<=?
               AND (valid_to IS NULL OR valid_to>=?) AND status IN ('confirmed','candidate')""",
            (channel, category, sku, today, today),
        ).fetchall()
        confirmed = [row for row in rows if row["status"] == "confirmed"]
        candidates = [row for row in rows if row["status"] == "candidate"]
        findings = []
        if not confirmed:
            findings.append(finding("L2", "NOT_CHECKED", "CHANNEL-NO-CONFIRMED-RULES",
                                    "该渠道没有已确认且在有效期内的要求卡，不能宣称渠道审核通过。", "channel_rule"))
        for row in candidates:
            findings.append(finding("L3", "REVIEW", "CHANNEL-CANDIDATE-{}".format(row["id"]),
                                    "有待审核的渠道反馈：{}。".format(row["requirement"]), "human_decision",
                                    details={"source_ref": row["source_ref"], "card_id": row["id"]}))

        # Opposite approved cards for one target need a human reconciliation.
        by_target = {}
        for row in confirmed:
            by_target.setdefault(row["target"], set()).add(row["matcher"])
        conflicts = {target for target, matchers in by_target.items() if {"contains", "forbid"} <= matchers}
        for target in conflicts:
            findings.append(finding("L3", "REVIEW", "CHANNEL-CONFLICT", "渠道要求相互冲突：" + target,
                                    "human_decision"))
        for row in confirmed:
            if row["target"] in conflicts:
                continue
            if row["matcher"] == "manual":
                status = "REVIEW"
            elif row["matcher"] == "document":
                status = "PASS" if row["target"] in documents else "REVIEW"  # absence may mean not supplied
            elif not label_text:
                status = "REVIEW"
            elif row["matcher"] == "contains":
                status = "PASS" if row["target"] in label_text else "FAIL"
            else:
                status = "FAIL" if row["target"] in label_text else "PASS"
            findings.append(finding("L2", status, "CHANNEL-{}".format(row["id"]),
                                    row["requirement"], "confirmed_channel_rule",
                                    details={"source_ref": row["source_ref"], "reviewed_by": row["reviewed_by"],
                                             "matcher": row["matcher"], "target": row["target"]}))
        return findings
