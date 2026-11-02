"""Cursor pull and resume rules, with durable local pages before clean writes."""
from datetime import UTC, datetime, timedelta

from brand_presence import ledger, request_keys
from brand_presence.bigquery import query, rows_parameter
from brand_presence.runner import _Run
from brand_presence.sources.base import ERROR, PARTIAL, SUCCESS, Request, stored_request


class CursorRun(_Run):
    def cursor(self):
        rows = query(self.bq, f"SELECT cursor_value, resume_value FROM "
                     f"{self.source.cursor_model.table} ORDER BY moved_at DESC LIMIT 1")
        return (rows[0]['cursor_value'], rows[0]['resume_value']) if rows else (None, None)

    def move_cursor(self, cursor, resume=None):
        model = self.source.cursor_model
        columns = ', '.join(model.model_fields)
        query(self.bq, f"INSERT INTO {model.table} ({columns}) "
              f"SELECT {columns} FROM UNNEST(@rows)", [rows_parameter('rows', model, [
                  model(cursor_value=cursor, moved_at=self.clock(), run_id=self.run_id,
                        resume_value=resume)])])

    def write(self, answers):
        sender, self.sender = self.sender, None
        try:
            super().write(answers)
        finally:
            self.sender = sender

    def recover(self, unreadable):
        self.ledger.orphaned(self.run_id)
        pending = (self.ledger.in_state(ledger.UNKNOWN)
                   + self.ledger.not_loaded() + self.ledger.not_written())
        broken = [row for row in pending if self.raw_path(row) in unreadable]
        self.ledger.rewrite([ledger.changed(row, state=ledger.FAILED) for row in broken])
        self.counts['requests_failed'] += len(broken)
        for row in self.ledger.in_state(ledger.UNKNOWN):
            raw = self.warehouse.read_raw(self.source.raw_model, self.raw_path(row))
            if raw is None:
                self.ledger.rewrite([ledger.changed(row, state=ledger.FAILED)])
                self.counts['requests_failed'] += 1
            else:
                self.ledger.rewrite([ledger.changed(
                    row, state=ledger.COLLECTED if raw.usable else ledger.UNUSABLE,
                    collected_at=raw.pulled_at, stored_run_id=raw.run_id)])
        self.finish()
        unfinished = self.ledger.not_loaded() + self.ledger.not_written()
        self.ledger.rewrite([ledger.changed(row, state=ledger.FAILED) for row in unfinished])
        self.counts['requests_failed'] += len(unfinished)

    def expire_local(self):
        directory = self.settings.state_path / "raw" / self.source.name
        now = self.clock()
        expired, unreadable = [], set()
        for path in directory.rglob("*.json"):
            try:
                raw = self.source.raw_model.model_validate_json(path.read_text())
            except (OSError, ValueError):
                self.error('raw file unreadable')
                unreadable.add(str(path.relative_to(self.settings.state_path)))
                continue
            expires = datetime.combine(raw.content_date, datetime.min.time(), UTC) + timedelta(
                days=self.settings.mention_text_days)
            if expires > now:
                continue
            expired.append((path, raw))
        keys = {(raw.period, raw.request_key) for _, raw in expired}
        periods = self.ledger.of_periods(sorted({period for period, _ in keys}))
        self.ledger.rewrite([
            ledger.changed(row, state=ledger.EXPIRED)
            for rows in periods.values() for row in rows
            if (row.period, row.request_key) in keys
        ])
        for path, _ in expired:
            path.unlink()
        return unreadable

    def tasks(self):
        self.recover(self.expire_local())
        start, point = self.cursor()
        sender = self.sender
        sender.go_on(start, point)
        last, whole = start, False
        completed_point = None
        deadline = self.started_at + timedelta(seconds=self.world.wait_seconds)
        for page in range(1, sender.max_pages + 1):
            if self.clock() > deadline:
                break
            key = request_keys.request_key(self.settings.client_id, self.source.name,
                                           self.period.name,
                                           {'cursor': start, 'page': page, 'run': self.run_id})
            now = self.clock()
            row = self.source.ledger_model(
                request_key=key, period=self.period.name, attempt=1, vendor_task_id=None,
                state=ledger.SENDING, run_id=self.run_id, created_at=now, submitted_at=None,
                collected_at=None, stored_run_id=None, loaded_at=None, written_at=None,
                error_class=None, vendor_status=None,
                request=stored_request(Request({'cursor': start, 'page': page}),
                                       self.ref.load_number, self.ref.snapshot_id))
            self.ledger.add([row])
            self.counts['requests_planned'] += 1
            self.counts['requests_sent'] += 1
            try:
                got = sender.pull(start, page)
                verdict, raw, path = self.store(row, got.answer)
                marked = ledger.changed(
                    row, state=ledger.COLLECTED if verdict.usable else ledger.UNUSABLE,
                    collected_at=raw.pulled_at, stored_run_id=raw.run_id)
                self.ledger.rewrite([marked])
                self.stored.append((marked, raw, path))
            except Exception as error:
                self.error(type(error).__name__)
                self.counts['requests_failed'] += 1
                break
            if not verdict.usable:
                break
            last = got.cursor if got.cursor is not None else last
            completed_point = sender.resume(start)
            if not got.more:
                whole = True
                break
        self.note(sender.counts())
        self.load_and_write()
        all_written = not (self.ledger.not_loaded() or self.ledger.not_written())
        if whole and all_written:
            if last is not None and (last != start or point is not None):
                self.move_cursor(last)
            self.pull_status = PARTIAL if self.left_out or self.errors else SUCCESS
        else:
            if all_written:
                resume = completed_point
                if resume is not None and (resume.cursor, resume.point) != (start, point):
                    self.move_cursor(resume.cursor, resume.point)
            self.pull_status = PARTIAL if self.counts['clean_rows'] else ERROR

    def judge(self):
        return self.pull_status
