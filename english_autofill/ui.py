"""
Qt6 dialogs for the Auto-fill add-on.

DefinitionPickerDialog — shown when multiple definitions exist.
ImagePickerDialog       — thumbnail grid for choosing an image.
"""

from __future__ import annotations

from aqt.qt import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPixmap,
    QPushButton,
    QSizePolicy,
    Qt,
    QVBoxLayout,
)


# ---------------------------------------------------------------------------
# Definition picker
# ---------------------------------------------------------------------------

class DefinitionPickerDialog(QDialog):
    """Present a list of definitions and let the user pick one."""

    def __init__(self, word: str, definitions, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'Select definition for "{word}"')
        self.setMinimumWidth(540)
        self._definitions = definitions

        layout = QVBoxLayout(self)

        label = QLabel(
            f"Multiple definitions found for <b>{word}</b>. "
            "Select the one you want to learn:"
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        self._list = QListWidget()
        self._list.setWordWrap(True)
        self._list.setSpacing(2)
        for d in definitions:
            pos_label = f"[{d.pos}]  " if d.pos else ""
            item = QListWidgetItem(f"{pos_label}{d.text}")
            item.setSizeHint(item.sizeHint())
            self._list.addItem(item)
        self._list.setCurrentRow(0)
        self._list.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self._list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def chosen_definition(self):
        """Return the selected DefinitionEntry, or the first if none selected."""
        row = self._list.currentRow()
        if 0 <= row < len(self._definitions):
            return self._definitions[row]
        return self._definitions[0]


# ---------------------------------------------------------------------------
# Image picker
# ---------------------------------------------------------------------------

_THUMB_W = 130
_THUMB_H = 100
_COLS = 3


class ImagePickerDialog(QDialog):
    """Show a 3-column thumbnail grid and let the user pick or skip an image."""

    def __init__(self, word: str, images, pixmaps: list[QPixmap], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'Select image for "{word}"')
        self._images = images
        self._chosen_url: str | None = None
        self._selected_idx: int | None = None
        self._labels: list[QLabel] = []

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(f"Pick an image for <b>{word}</b> (click to select, then confirm):")
        )

        grid = QGridLayout()
        grid.setSpacing(6)

        display_items = list(zip(images[:9], pixmaps[:9]))
        for i, (img, pm) in enumerate(display_items):
            lbl = QLabel()
            lbl.setFixedSize(_THUMB_W, _THUMB_H)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                "border: 2px solid transparent; background: #f0f0f0; border-radius: 3px;"
            )
            if pm and not pm.isNull():
                lbl.setPixmap(
                    pm.scaled(
                        _THUMB_W - 4,
                        _THUMB_H - 4,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            else:
                lbl.setText("(no preview)")

            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            # Capture loop variable with default argument
            lbl.mousePressEvent = lambda _ev, idx=i: self._on_select(idx)
            self._labels.append(lbl)
            grid.addWidget(lbl, i // _COLS, i % _COLS)

        layout.addLayout(grid)

        btn_row = QHBoxLayout()
        skip_btn = QPushButton("Skip Image")
        skip_btn.clicked.connect(self.reject)
        use_btn = QPushButton("Use Selected")
        use_btn.setDefault(True)
        use_btn.clicked.connect(self._on_use)
        btn_row.addWidget(skip_btn)
        btn_row.addStretch()
        btn_row.addWidget(use_btn)
        layout.addLayout(btn_row)

    def _on_select(self, idx: int) -> None:
        self._selected_idx = idx
        for i, lbl in enumerate(self._labels):
            if i == idx:
                lbl.setStyleSheet(
                    "border: 2px solid #0078d4; background: #ddeeff; border-radius: 3px;"
                )
            else:
                lbl.setStyleSheet(
                    "border: 2px solid transparent; background: #f0f0f0; border-radius: 3px;"
                )

    def _on_use(self) -> None:
        if self._selected_idx is not None:
            self._chosen_url = self._images[self._selected_idx].full_url
        self.accept()

    def chosen_full_url(self) -> str | None:
        """Return the full-resolution URL of the selected image, or None if skipped."""
        return self._chosen_url
