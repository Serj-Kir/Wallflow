"""Schedule view: time-based rules that switch profiles automatically.

This is Wallflow's cross-platform answer to Wallpaper Engine's application
rules, which are Windows-only.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..models import ScheduleRule
from .widgets import section

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WEEKDAYS_FULL = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


class ScheduleView(QWidget):
    """Create rules: when a given profile should be active."""

    status = Signal(str)
    rules_changed = Signal()

    def __init__(self, controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller

        self._list = QListWidget(self)
        self._list.setMaximumWidth(260)
        self._list.currentItemChanged.connect(self._on_selected)

        self._enabled = QComboBox(self)
        self._enabled.addItem("Включено", True)
        self._enabled.addItem("Выключено", False)

        self._name = QLineEdit(self)
        self._name.setPlaceholderText("Например: Рабочий день")

        self._profile = QComboBox(self)

        self._days: list[QPushButton] = []
        day_row = QHBoxLayout()
        for index in range(7):
            button = QPushButton(WEEKDAYS[index], self)
            button.setCheckable(True)
            button.setFixedWidth(40)
            button.setToolTip(WEEKDAYS_FULL[index])
            button.clicked.connect(self._collect_days)
            self._days.append(button)
            day_row.addWidget(button)
        all_days = QPushButton("Все", self)
        all_days.clicked.connect(lambda: self._set_all_days(True))
        no_days = QPushButton("Сброс", self)
        no_days.clicked.connect(lambda: self._set_all_days(False))
        day_row.addWidget(all_days)
        day_row.addWidget(no_days)
        day_row.addStretch()

        self._from_time = QTimeEdit(self)
        self._from_time.setDisplayFormat("HH:mm")
        self._to_time = QTimeEdit(self)
        self._to_time.setDisplayFormat("HH:mm")

        self._priority = QSpinBox(self)
        self._priority.setRange(0, 100)
        self._priority.setToolTip("Выше приоритет — правило сработает первым при пересечении")

        self._add_btn = QPushButton("+ Создать", self)
        self._add_btn.setObjectName("Primary")
        self._add_btn.clicked.connect(self._create)
        self._delete_btn = QPushButton("Удалить", self)
        self._delete_btn.setObjectName("Danger")
        self._delete_btn.clicked.connect(self._delete)
        self._enable_btn = QPushButton("Вкл/выкл", self)
        self._enable_btn.clicked.connect(self._toggle)

        editor_box, editor_layout = section("Правило")
        editor_layout.addWidget(self._enabled)
        editor_layout.addWidget(self._name)
        form = QHBoxLayout()
        form.addWidget(QLabel("Профиль:"))
        form.addWidget(self._profile, 1)
        form.addSpacing(14)
        form.addWidget(QLabel("Приоритет:"))
        form.addWidget(self._priority)
        editor_layout.addLayout(form)
        editor_layout.addWidget(QLabel("Дни недели"))
        editor_layout.addLayout(day_row)
        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("С:"))
        time_row.addWidget(self._from_time)
        time_row.addWidget(QLabel("До:"))
        time_row.addWidget(self._to_time)
        time_row.addSpacing(14)
        time_row.addWidget(QLabel("(через полночь тоже можно)"))
        time_row.addStretch()
        editor_layout.addLayout(time_row)

        buttons = QHBoxLayout()
        buttons.addWidget(self._add_btn)
        buttons.addWidget(self._enable_btn)
        buttons.addWidget(self._delete_btn)
        buttons.addStretch()

        left = QVBoxLayout()
        left.addWidget(self._list)
        left.addLayout(buttons)

        hint = QLabel(
            "Правила проверяются раз в минуту. Если несколько правил подходят одновременно, "
            "побеждает правило с наибольшим приоритетом."
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addLayout(left)
        layout.addWidget(editor_box, 1)
        layout.addWidget(hint, 0)

        self.reload()

    # --------------------------------------------------------------- loading
    def reload(self) -> None:
        self._list.clear()
        self._profile.clear()
        for profile in self._controller.profiles:
            self._profile.addItem(profile.name, profile.id)

        for rule in self._controller.rules:
            profile = self._controller.find_profile(rule.profile_id)
            item = QListWidgetItem()
            item.setData(Qt.UserRole, rule.id)
            item.setText(self._describe(rule, profile.name if profile else "—"))
            if not rule.enabled:
                item.setForeground(Qt.gray)
            self._list.addItem(item)

        if self._list.count():
            self._list.setCurrentRow(0)
        else:
            self._set_editor_enabled(False)

    @staticmethod
    def _describe(rule: ScheduleRule, profile_name: str) -> str:
        days = "каждый день" if not rule.weekdays else ",".join(WEEKDAYS[d] for d in rule.weekdays)
        start = f"{rule.start_minutes // 60:02d}:{rule.start_minutes % 60:02d}"
        end = f"{rule.end_minutes // 60:02d}:{rule.end_minutes % 60:02d}"
        prefix = "" if rule.enabled else "⏸ "
        return f"{prefix}{profile_name}\n{days} {start}–{end}"

    def current_rule(self) -> Optional[ScheduleRule]:
        item = self._list.currentItem()
        if item is None:
            return None
        rule_id = item.data(Qt.UserRole)
        return next((r for r in self._controller.rules if r.id == rule_id), None)

    def _on_selected(self, *_args) -> None:
        rule = self.current_rule()
        self._set_editor_enabled(rule is not None)
        if rule is None:
            return
        self._enabled.setCurrentIndex(0 if rule.enabled else 1)
        self._name.setText(rule.name)
        index = self._profile.findData(rule.profile_id)
        self._profile.setCurrentIndex(index if index >= 0 else 0)
        self._priority.setValue(rule.priority)
        self._set_all_days(False)
        for day in rule.weekdays:
            if 0 <= day < len(self._days):
                self._days[day].setChecked(True)
        self._from_time.setTime(_minutes_to_time(rule.start_minutes))
        self._to_time.setTime(_minutes_to_time(rule.end_minutes))

    def _set_editor_enabled(self, enabled: bool) -> None:
        for widget in (self._enabled, self._name, self._profile, self._priority,
                       self._from_time, self._to_time, self._enable_btn, self._delete_btn):
            widget.setEnabled(enabled)
        self._days_enabled = enabled
        for button in self._days:
            button.setEnabled(enabled)

    # --------------------------------------------------------------- editing
    def _set_all_days(self, checked: bool) -> None:
        for button in self._days:
            button.setChecked(checked)

    def _collect_days(self) -> list[int]:
        return [index for index, button in enumerate(self._days) if button.isChecked()]

    def _create(self) -> None:
        if not self._controller.profiles:
            QMessageBox.information(self, "Нет профилей", "Сначала создайте профиль.")
            return
        profile_id = self._profile.currentData()
        if not profile_id:
            return
        start = self._from_time.time().hour() * 60 + self._from_time.time().minute()
        end = self._to_time.time().hour() * 60 + self._to_time.time().minute()
        rule = ScheduleRule(
            name=self._name.text().strip() or "Правило",
            profile_id=profile_id,
            weekdays=self._collect_days(),
            start_minutes=start,
            end_minutes=end,
            priority=self._priority.value(),
        )
        self._controller.add_rule(rule)
        self.reload()
        for row in range(self._list.count()):
            if self._list.item(row).data(Qt.UserRole) == rule.id:
                self._list.setCurrentRow(row)
        self.status.emit(f"Создано правило «{rule.name}»")

    def _toggle(self) -> None:
        rule = self.current_rule()
        if rule is None:
            return
        rule.enabled = not rule.enabled
        self._controller.save_rules()
        self.reload()
        for row in range(self._list.count()):
            if self._list.item(row).data(Qt.UserRole) == rule.id:
                self._list.setCurrentRow(row)

    def _delete(self) -> None:
        rule = self.current_rule()
        if rule is None:
            return
        answer = QMessageBox.question(
            self, "Удалить правило", f"Удалить правило «{rule.name}»?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._controller.remove_rule(rule.id)
        self.reload()


def _minutes_to_time(minutes: int):
    from PySide6.QtCore import QTime

    minutes = max(0, min(24 * 60 - 1, minutes))
    return QTime(minutes // 60, minutes % 60)


__all__ = ["ScheduleView"]