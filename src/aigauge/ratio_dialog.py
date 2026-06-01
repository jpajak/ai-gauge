from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .ratio import (
    MIN_SAMPLES,
    MIN_SESSION_DELTA,
    MIN_WEEKLY_DELTA,
    RatioEstimate,
    WeeklyRatioRecord,
    is_confident,
    sessions_per_week,
    typical_sessions_per_week,
    weekly_pct_per_session,
)

_DARK_STYLESHEET = """
QDialog { background:#1f2937; color:#e5e7eb; }
QLabel { color:#e5e7eb; background:transparent; }
QPushButton {
    background:#374151; color:#f3f4f6;
    border:1px solid #4b5563; border-radius:4px;
    padding:5px 12px; min-height:22px;
}
QPushButton:hover { background:#4b5563; }
QPushButton:default { background:#2563eb; border-color:#1d4ed8; }
"""

_HEADER_COLS = ("Period", "Sessions/wk", "1 session ≈", "Coverage", "Readings")


def _parse(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None


def _period_label(index_from_newest: int, record: WeeklyRatioRecord) -> str:
    if index_from_newest == 0:
        return "last week"
    return f"{index_from_newest + 1} wks ago"


def _date_range(record: WeeklyRatioRecord) -> str:
    start = _parse(record.week_started_at)
    end = _parse(record.week_ended_at)
    if start is None or end is None:
        return ""
    return f"{start.strftime('%b %d')} → {end.strftime('%b %d')}"


class _Sparkline(QWidget):
    """Tiny line chart of sessions/week across the kept weeks (oldest → newest)."""

    _LINE = QColor("#60a5fa")
    _DOT = QColor("#93c5fd")
    _AXIS = QColor("#374151")

    def __init__(self, values: list[float], parent: QWidget | None = None):
        super().__init__(parent)
        self._values = values
        self.setFixedHeight(46)
        self.setMinimumWidth(220)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(6, 6, -6, -6)

        painter.setPen(QPen(self._AXIS, 1))
        painter.drawLine(
            int(rect.left()),
            int(rect.bottom()),
            int(rect.right()),
            int(rect.bottom()),
        )

        values = [v for v in self._values if v is not None]
        if len(values) < 2:
            painter.setPen(QPen(QColor("#6b7280")))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "not enough weeks yet",
            )
            painter.end()
            return

        lo = min(values)
        hi = max(values)
        span = hi - lo or 1.0
        n = len(values)
        points: list[tuple[float, float]] = []
        for i, v in enumerate(values):
            x = rect.left() + (rect.width() * i / (n - 1))
            y = rect.bottom() - ((v - lo) / span) * rect.height()
            points.append((x, y))

        painter.setPen(QPen(self._LINE, 2))
        painter.drawPolyline(QPolygonF([self._pt(x, y) for x, y in points]))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._DOT)
        for x, y in points:
            painter.drawEllipse(self._pt(x, y), 2.5, 2.5)
        painter.end()

    @staticmethod
    def _pt(x: float, y: float):
        from PyQt6.QtCore import QPointF

        return QPointF(x, y)


class RatioHistoryDialog(QDialog):
    """Read-only window showing the session->weekly burn rate and its history."""

    def __init__(
        self,
        provider: str,
        display_name: str,
        records: list[WeeklyRatioRecord],
        current_estimate: RatioEstimate | None,
        weekly_pct_used: float | None = None,
        parent=None,
    ):
        super().__init__(None)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowTitle(f"{display_name} — session vs weekly")
        self.setStyleSheet(_DARK_STYLESHEET)
        self.resize(460, 440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        self._typical = typical_sessions_per_week(records)
        layout.addWidget(self._summary_block(current_estimate, weekly_pct_used))

        explainer = QLabel(
            "How many full 5-hour sessions you can run before the weekly limit is "
            "used up, measured from how your weekly usage climbs as you spend each "
            "session. Providers retune these limits over time, so the rate drifts."
        )
        explainer.setWordWrap(True)
        explainer.setStyleSheet("color:#9ca3af; font-size:11px;")
        layout.addWidget(explainer)

        # oldest -> newest for the trend line
        spark_values = [
            sessions_per_week(r.sum_session_delta, r.sum_weekly_delta) for r in records
        ]
        layout.addWidget(_Sparkline([v for v in spark_values if v is not None]))

        layout.addWidget(self._history_grid(records), 1)

        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(self.reject)
        close_box.accepted.connect(self.accept)
        layout.addWidget(close_box)

    def _summary_block(
        self, est: RatioEstimate | None, weekly_pct_used: float | None
    ) -> QWidget:
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        if est is None or not est.confident or est.sessions_per_week is None:
            title = QLabel("<b>Calibrating</b>")
            title.setTextFormat(Qt.TextFormat.RichText)
            outer.addWidget(title)
            sub = QLabel(
                "Building an estimate from usage seen while running (not the "
                "absolute %). It locks in once a session has climbed enough."
            )
            sub.setStyleSheet("color:#9ca3af; font-size:11px;")
            sub.setWordWrap(True)
            outer.addWidget(sub)
            if est is not None:
                progress = QLabel(
                    "Progress: session "
                    f"{min(est.session_delta, MIN_SESSION_DELTA):.0f}/{MIN_SESSION_DELTA:.0f} pts · "
                    f"weekly {min(est.coverage_pct, MIN_WEEKLY_DELTA):.1f}/{MIN_WEEKLY_DELTA:.0f} pts · "
                    f"readings {min(est.sample_count, MIN_SAMPLES)}/{MIN_SAMPLES}"
                )
                progress.setStyleSheet("color:#e5e7eb; font-size:11px; font-weight:600;")
                progress.setWordWrap(True)
                outer.addWidget(progress)
            if self._typical is not None:
                median, weeks = self._typical
                typical = QLabel(
                    f"Typical (last {weeks} weeks): ~{median:.1f} sessions/week"
                )
                typical.setStyleSheet("color:#9ca3af; font-size:11px;")
                outer.addWidget(typical)
            return container

        when = "This week so far" if est.source == "current" else "Last week"
        title = QLabel(f"<b>{when}</b>")
        title.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(title)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        rows = [
            ("Full sessions per week", f"~{est.sessions_per_week:.1f}"),
            ("Each full session uses", self._cost_text(est)),
            ("Sessions left this week", self._remaining_text(est, weekly_pct_used)),
        ]
        if self._typical is not None:
            median, weeks = self._typical
            rows.append((f"Typical (last {weeks} weeks)", f"~{median:.1f}"))
        rows.append(
            (
                "Coverage",
                f"{est.coverage_pct:.0f}% of weekly · {est.sample_count} readings",
            )
        )
        for row, (key_text, value_text) in enumerate(rows):
            key = QLabel(key_text)
            key.setStyleSheet("color:#9ca3af; font-size:11px;")
            value = QLabel(value_text)
            value.setStyleSheet("color:#e5e7eb; font-size:12px; font-weight:600;")
            grid.addWidget(key, row, 0)
            grid.addWidget(value, row, 1)
        grid.setColumnStretch(2, 1)
        outer.addLayout(grid)
        return container

    @staticmethod
    def _cost_text(est: RatioEstimate) -> str:
        r = est.weekly_pct_per_session
        return "n/a" if r is None else f"~{r:.1f}% of weekly"

    @staticmethod
    def _remaining_text(est: RatioEstimate, weekly_pct_used: float | None) -> str:
        r = est.weekly_pct_per_session
        if r is None or r <= 0 or weekly_pct_used is None:
            return "n/a"
        remaining = max(0.0, 100.0 - weekly_pct_used) / r
        return f"~{remaining:.1f}  (weekly {weekly_pct_used:.0f}% used)"

    def _history_grid(self, records: list[WeeklyRatioRecord]) -> QWidget:
        container = QWidget()
        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(3)

        for col, title in enumerate(_HEADER_COLS):
            head = QLabel(title)
            head.setStyleSheet("color:#9ca3af; font-size:10px; font-weight:700;")
            grid.addWidget(head, 0, col)

        if not records:
            empty = QLabel("No finalized weeks yet.")
            empty.setStyleSheet("color:#6b7280; font-size:11px; font-style:italic;")
            grid.addWidget(empty, 1, 0, 1, len(_HEADER_COLS))
            return container

        # newest first
        for row, (offset, record) in enumerate(enumerate(reversed(records)), start=1):
            n = sessions_per_week(record.sum_session_delta, record.sum_weekly_delta)
            r = weekly_pct_per_session(
                record.sum_session_delta, record.sum_weekly_delta
            )
            confident = is_confident(
                record.sum_session_delta,
                record.sum_weekly_delta,
                record.sample_count,
            )
            color = "#e5e7eb" if confident else "#6b7280"
            cells = (
                _period_label(offset, record),
                "n/a" if n is None else f"{n:.1f}",
                "n/a" if r is None else f"{r:.0f}%",
                f"{record.sum_weekly_delta:.0f}%",
                str(record.sample_count),
            )
            for col, value in enumerate(cells):
                cell = QLabel(value)
                cell.setStyleSheet(f"color:{color}; font-size:11px;")
                if col == 0:
                    cell.setToolTip(_date_range(record))
                grid.addWidget(cell, row, col)
        grid.setRowStretch(len(records) + 1, 1)
        return container
