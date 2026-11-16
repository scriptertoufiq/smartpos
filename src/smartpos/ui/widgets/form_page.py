"""The inline create/edit form every list screen uses instead of a dialog.

A modal covers the list you were just reading, and on a maximised window it
floats a small box in the middle of a lot of empty space. These screens put the
form on a second page of the same screen instead: the list and the form swap
places, nothing is covered, and a refused save leaves your typing where it is
rather than closing and losing it.

Two pieces:

    FormCard   the card itself -- a title, a form layout, a hint line and the
               Cancel / Save pair.
    FormPage   that card parked at the top-left of an otherwise empty page,
               which is what goes into the screen's QStackedWidget.

A screen wires it up like this:

    self.pages = QStackedWidget()
    self.pages.addWidget(self._build_list())    # PAGE_LIST
    self.pages.addWidget(self._build_form())    # PAGE_FORM

and `_build_form` fills a FormCard with its own fields.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Wide enough for a label and a comfortable field, narrow enough to read.
FORM_WIDTH = 560
# Forms with many fields, or a table inside them, ask for more room.
WIDE_FORM_WIDTH = 820

PAGE_LIST = 0
PAGE_FORM = 1


class FormCard(QFrame):
    """Title, fields, hint, Cancel / Save."""

    save_clicked = pyqtSignal()
    cancel_clicked = pyqtSignal()

    def __init__(self, save_text: str = "Save", width: int = FORM_WIDTH,
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("panelCard")
        self.setFixedWidth(width)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self.title = QLabel("")
        self.title.setObjectName("panelHeading")
        layout.addWidget(self.title)

        self.form = QFormLayout()
        self.form.setSpacing(9)
        # Left-aligned: these labels vary a lot in length, and right-aligning
        # them leaves a ragged edge against the fields.
        self.form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.form.setHorizontalSpacing(14)
        layout.addLayout(self.form)

        self.body = QVBoxLayout()      # for anything that is not a labelled row
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        layout.addLayout(self.body)

        self.hint = QLabel("")
        self.hint.setObjectName("hintLabel")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setMinimumHeight(32)
        buttons.addWidget(self.cancel_button)
        self.save_button = QPushButton(save_text)
        self.save_button.setProperty("primary", True)
        self.save_button.setMinimumHeight(32)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

        self.save_button.clicked.connect(self.save_clicked)
        self.cancel_button.clicked.connect(self.cancel_clicked)

    # ----------------------------------------------------------- building

    def add_row(self, label, widget):
        """Add one labelled field and hand the widget straight back."""
        widget.setMinimumHeight(30)
        self.form.addRow(label, widget)
        return widget

    def add_block(self, widget):
        """Add something that carries its own layout, below the fields."""
        self.body.addWidget(widget)
        return widget

    # ---------------------------------------------------------- behaviour

    def set_title(self, text: str) -> None:
        self.title.setText(text)

    def set_hint(self, text: str) -> None:
        self.hint.setText(text)
        self.hint.setVisible(bool(text))

    def set_busy(self, busy: bool) -> None:
        """Keep a second click from saving twice while the first is in flight."""
        self.save_button.setEnabled(not busy)


class FormPage(QWidget):
    """A card parked at the top-left of an otherwise empty page."""

    def __init__(self, card: FormCard, parent=None) -> None:
        super().__init__(parent)
        self.card = card

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        row = QHBoxLayout()
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)
