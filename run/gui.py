import sys
import pandas as pd
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QGridLayout, QLabel, QComboBox, QLineEdit, 
                             QCheckBox, QPushButton, QScrollArea, QFrame,
                             QMessageBox, QSpinBox, QDoubleSpinBox, QStyle, QProgressBar,
                             QSizePolicy)
from PyQt6.QtCore import Qt, QUrl, QTimer
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage
from run import config, data_loader, filtering, mapping

class ConsolePage(QWebEnginePage):
    def __init__(self, parent=None, callback=None):
        super().__init__(parent)
        self.callback = callback

    def javaScriptConsoleMessage(self, level, message, line, source):
        # Print logs for debug, but also check for commands
        # print(f"JS: {message}", flush=True) 
        if self.callback:
            self.callback(message)

class CheckableComboBox(QComboBox):
    def __init__(self):
        super().__init__()
        self.view().pressed.connect(self.handle_item_pressed)
        self.setModel(self.model())
        self.changed = False

    def handle_item_pressed(self, index):
        item = self.model().itemFromIndex(index)
        text = item.text()
        
        if text == "(Select All)":
            for i in range(self.count()):
                it = self.model().item(i)
                if it.text() not in ["(Select All)", "(Clear)"]:
                    it.setCheckState(Qt.CheckState.Checked)
        elif text == "(Clear)":
            for i in range(self.count()):
                it = self.model().item(i)
                if it.text() not in ["(Select All)", "(Clear)"]:
                    it.setCheckState(Qt.CheckState.Unchecked)
        else:
            if item.checkState() == Qt.CheckState.Checked:
                item.setCheckState(Qt.CheckState.Unchecked)
            else:
                item.setCheckState(Qt.CheckState.Checked)
        self.changed = True

    def hidePopup(self):
        if not self.changed:
            super().hidePopup()
        self.changed = False

    def get_checked_items(self):
        checked_items = []
        for i in range(self.count()):
            item = self.model().item(i, 0)
            if item.text() in ["(Select All)", "(Clear)"]:
                continue
            if item.checkState() == Qt.CheckState.Checked:
                checked_items.append(item.text())
        return checked_items

# Fix: Import QSizePolicy
from PyQt6.QtWidgets import QSizePolicy

class FilterRow(QWidget):
    def __init__(self, parent=None, df=None, remove_callback=None, show_logic=True):
        super().__init__(parent)
        self.df = df
        self.remove_callback = remove_callback
        
        # Ensure minimum height to prevent squashing
        self.setMinimumHeight(40)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        # Logic (AND/OR)
        self.logic_combo = QComboBox()
        self.logic_combo.addItems(["AND", "OR"])
        self.logic_combo.setMinimumWidth(80) 
        self.logic_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        if not show_logic:
            self.logic_combo.hide() 
            # Add spacer or just empty
        layout.addWidget(self.logic_combo)

        # Column Selector
        self.col_combo = QComboBox()
        self.col_combo.addItem("Select Column...", None)
        
        # Sort columns by display name
        cols_to_add = []
        for col in df.columns:
            display = config.COLUMN_DISPLAY_NAMES.get(col, col)
            cols_to_add.append((display, col))
        
        cols_to_add.sort(key=lambda x: x[0])
        
        for display, col in cols_to_add:
            self.col_combo.addItem(display, col)
        
        self.col_combo.currentIndexChanged.connect(self.update_operators)
        layout.addWidget(self.col_combo)

        # Operator Selector
        self.op_combo = QComboBox()
        self.op_combo.setFixedWidth(110)
        layout.addWidget(self.op_combo)

        # Value Widget Container
        self.value_container = QWidget()
        self.value_layout = QHBoxLayout(self.value_container)
        self.value_layout.setContentsMargins(0,0,0,0)
        self.value_widget = None 
        layout.addWidget(self.value_container)
        
        # Remove Button
        self.remove_btn = QPushButton("✕") 
        self.remove_btn.setFixedSize(24, 24)
        self.remove_btn.setStyleSheet("""
            QPushButton { color: #d9534f; font-weight: bold; border: none; background: transparent; }
            QPushButton:hover { background-color: rgba(217, 83, 79, 0.1); border-radius: 12px; }
        """)
        self.remove_btn.clicked.connect(self.on_remove)
        layout.addWidget(self.remove_btn)
        
        # Initial State
        self.value_container.hide()
        self.op_combo.hide()

    def on_remove(self):
        if self.remove_callback:
            self.remove_callback(self)
            
    def update_operators(self):
        col = self.col_combo.currentData()
        
        # Clear value widget
        if self.value_widget:
            self.value_widget.deleteLater()
            self.value_widget = None
        
        self.op_combo.clear()
        
        if not col:
            self.op_combo.hide()
            self.value_container.hide()
            return
            
        self.op_combo.show()
        self.value_container.show()

        is_numeric = pd.api.types.is_numeric_dtype(self.df[col])
        unique_vals = filtering.get_unique_values(self.df, col)
        
        if is_numeric:
             self.op_combo.addItem("Equals (=)", "equals")
             self.op_combo.addItem("Greater (>)", ">")
             self.op_combo.addItem("Less (<)", "<")
             self.op_combo.addItem("Greater/Eq (>=)", ">=")
             self.op_combo.addItem("Less/Eq (<=)", "<=")
             
             self.value_widget = QDoubleSpinBox()
             self.value_widget.setRange(-999999999, 999999999)
             self.value_widget.setDecimals(2)
             self.value_widget.setStyleSheet("padding: 2px;")
             
        else:
             self.op_combo.addItem("Contains", "contains")
             self.op_combo.addItem("Equals", "equals")
             self.op_combo.addItem("Starts With", "starts_with")
             self.op_combo.addItem("Ends With", "ends_with")
             
             if len(unique_vals) < 50: # Use combo for small sets
                 self.value_widget = QComboBox()
                 self.value_widget.setEditable(True)
                 self.value_widget.addItems([str(x) for x in unique_vals])
                 self.value_widget.setCurrentIndex(-1)
                 self.value_widget.setPlaceholderText("Select or type...")
             else:
                 self.value_widget = QLineEdit()
                 self.value_widget.setPlaceholderText("Value...")
        
        if self.value_widget:
             self.value_layout.addWidget(self.value_widget)

    def get_filter_data(self):
        col = self.col_combo.currentData()
        if not col:
            return None
            
        op = self.op_combo.currentData()
        logic = self.logic_combo.currentText()
        
        val = None
        ftype = 'text'
        
        if isinstance(self.value_widget, QDoubleSpinBox):
            val = self.value_widget.value()
            ftype = 'number'
        elif isinstance(self.value_widget, QComboBox):
            val = self.value_widget.currentText()
            # If combo text is empty
            if not val: return None
        elif isinstance(self.value_widget, QLineEdit):
            val = self.value_widget.text()
        
        if val == "" or val is None:
            return None

        return {
            'column': col,
            'operator': op,
            'value': val,
            'logic': logic,
            'type': ftype
        }

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FSDispatch")
        self.resize(1200, 800)

        # State
        self.df = data_loader.load_data()
        
        # Calculate global airport frequency for coloring logic
        if not self.df.empty:
            all_iata = pd.concat([self.df['dep_airport_iata'], self.df['arr_airport_iata']])
            self.airport_counts = all_iata.value_counts()
        else:
            self.airport_counts = pd.Series()
            
        self.current_theme = 'dark'
        self.map_type = 'Hybrid'
        self.filter_rows = [] # List of FilterRow widgets
        self.map_view = None
        self.current_filtered_df = self.df  # Start with full dataset

        # UI Setup
        self.setup_ui()
        self.apply_theme()
        
        # Initial map (full dataset)
        self.render_map(self.df)
        
        # Determine filter columns
        if self.df.empty:
            QMessageBox.critical(self, "Error", "No flight data found or CSV is empty.")

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Use Grid Layout to allow overlay
        main_layout = QGridLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- Map Area (Background) ---
        self.map_view = QWebEngineView()
        self.map_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Enable console logging using custom Page
        page = ConsolePage(self.map_view, callback=self.on_js_console)
        self.map_view.setPage(page)
        
        # Add map view to grid (0,0) so it covers everything
        main_layout.addWidget(self.map_view, 0, 0)
        
        # --- Top Right Overlay Container ---
        # Parented to central_widget but NOT added to layout -> Manual positioning
        self.top_right_container = QWidget(central_widget)
        self.top_right_container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        tr_layout = QVBoxLayout(self.top_right_container)
        tr_layout.setContentsMargins(0, 0, 0, 0)
        tr_layout.setSpacing(0)
        tr_layout.setAlignment(Qt.AlignmentFlag.AlignRight)

        # Button Wrapper (to align button right)
        btn_wrapper = QWidget()
        btn_wrapper.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        btn_wrapper_layout = QHBoxLayout(btn_wrapper)
        btn_wrapper_layout.setContentsMargins(0, 0, 0, 0)
        btn_wrapper_layout.setAlignment(Qt.AlignmentFlag.AlignRight)
        
        self.show_filters_btn = QPushButton("Filters ▼")
        self.show_filters_btn.clicked.connect(self.toggle_filters_visibility)
        self.show_filters_btn.setVisible(True)
        self.show_filters_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        
        btn_wrapper_layout.addWidget(self.show_filters_btn)
        tr_layout.addWidget(btn_wrapper)

        # --- Filters Overlay (Foreground) ---
        self.filter_container = QFrame()
        self.filter_container.setObjectName("filter_container")
        self.filter_container.hide() # Start hidden by default
        self.filter_container.setFixedWidth(700) # Increased width
        
        # Add to container
        tr_layout.addWidget(self.filter_container)

        filter_layout = QVBoxLayout(self.filter_container)
        filter_layout.setContentsMargins(15, 10, 15, 5)
        
        # 1. Filter Header (Always visible)
        header_layout = QHBoxLayout()
        title = QLabel("Flight Filters")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #333;")
        header_layout.addWidget(title)
        
        header_layout.addStretch()

        # Map Type
        self.map_type_combo = QComboBox()
        self.map_type_combo.addItems(config.TILES.keys())
        self.map_type_combo.setCurrentText(self.map_type) # Sync default selection
        self.map_type_combo.currentTextChanged.connect(self.change_map_type)
        header_layout.addWidget(QLabel("Map Type:"))
        header_layout.addWidget(self.map_type_combo)

        filter_layout.addLayout(header_layout)

        # 2. Collapsible Content Wrapper
        self.filter_content = QWidget()
        content_layout = QVBoxLayout(self.filter_content)
        content_layout.setContentsMargins(0, 5, 0, 0)
        content_layout.setSpacing(5)

        # Dynamic Filters Area
        self.filters_widget = QWidget()
        self.filters_layout = QVBoxLayout(self.filters_widget)
        self.filters_layout.setContentsMargins(0, 0, 0, 0)
        self.filters_layout.setSpacing(8) # Increased spacing
        self.filters_layout.addStretch() # Push filters up

        self.add_filter_btn = QPushButton("+ Add Filter")
        self.add_filter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_filter_btn.setStyleSheet("""
            QPushButton {
                border: 1px dashed #aaa;
                border-radius: 4px;
                background-color: transparent;
                padding: 4px;
                color: #555;
            }
            QPushButton:hover {
                background-color: rgba(0,0,0,0.05);
                color: #000;
                border-color: #888;
            }
        """)
        self.add_filter_btn.clicked.connect(self.add_filter_row)
        
        self.create_filters()
        
        content_layout.addWidget(self.filters_widget)
        content_layout.addWidget(self.add_filter_btn)
        
        # Filter Buttons
        btn_widget = QWidget()
        btn_layout = QHBoxLayout(btn_widget)
        btn_layout.setContentsMargins(0, 5, 0, 0)
        
        self.apply_btn = QPushButton("Apply Filters & Show Map")
        self.apply_btn.clicked.connect(self.on_apply_filters)
        self.apply_btn.setMinimumWidth(200)
        
        reset_btn = QPushButton("Reset Filters")
        reset_btn.clicked.connect(self.reset_filters)

        btn_layout.addStretch()
        btn_layout.addWidget(reset_btn)
        btn_layout.addWidget(self.apply_btn)
        
        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0) # Indeterminate mode
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedWidth(150)
        self.progress_bar.setStyleSheet("QProgressBar { border: 1px solid grey; border-radius: 4px; text-align: center; } QProgressBar::chunk { background-color: #0078d4; }")
        
        btn_layout.addWidget(self.progress_bar)
        
        content_layout.addWidget(btn_widget)
        
        filter_layout.addWidget(self.filter_content)

        # 3. Toggle Button (Bottom Corner)
        toggle_layout = QHBoxLayout()
        toggle_layout.addStretch() # Push to right
        
        self.toggle_btn = QPushButton("▲") 
        self.toggle_btn.setFixedSize(24, 20)
        self.toggle_btn.setToolTip("Collapse filters")
        self.toggle_btn.clicked.connect(self.toggle_filters_visibility)
        self.toggle_btn.setStyleSheet("""
            QPushButton {
                border: none; 
                background: transparent; 
                font-weight: bold;
                color: #555;
            }
            QPushButton:hover {
                color: #000;
                background-color: rgba(0,0,0,0.05);
                border-radius: 3px;
            }
        """)
        toggle_layout.addWidget(self.toggle_btn)
        filter_layout.addLayout(toggle_layout)
        
        # Force initial style now that filter_container exists
        self.apply_theme()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Position the overlay container
        if hasattr(self, 'top_right_container'):
            # Calculate position: Top-Right with margins
            margin_top = 10
            margin_right = 20
            
            # The container needs to size itself to fit its children
            self.top_right_container.adjustSize()
            
            w = self.top_right_container.width()
            h = self.top_right_container.height()
            
            x = self.width() - w - margin_right
            y = margin_top
            
            self.top_right_container.move(x, y)
            self.top_right_container.raise_()

    def toggle_filters_visibility(self):
        """
        Toggles between [Full Panel Visible] and [Only 'Filters' Button Visible].
        """
        is_expanded = self.filter_container.isVisible()
        
        if is_expanded:
            # Collapse: Hide panel, Show button
            self.filter_container.hide()
            self.show_filters_btn.show() # In wrapper
        else:
            # Expand: Show panel, Hide button
            self.filter_container.show()
            self.show_filters_btn.hide() # In wrapper
        
        # Trigger re-layout of the overlay container
        if hasattr(self, 'top_right_container'):
            self.top_right_container.adjustSize()
            # Trigger resize event to re-position
            self.resizeEvent(None) # Safe to pass None

    def create_filters(self):
        # Clear existing
        while self.filters_layout.count():
             child = self.filters_layout.takeAt(0)
             if child.widget():
                 child.widget().deleteLater()
        
        # Add stretch at the end to push items up
        self.filters_layout.addStretch()
             
        self.filter_rows = []
        # Add one initial row
        self.add_filter_row()

    def add_filter_row(self):
        # Determine if logic combo should be shown
        show_logic = len(self.filter_rows) > 0
        
        row = FilterRow(self.filters_widget, self.df, remove_callback=self.remove_filter_row, show_logic=show_logic)
        row.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.filter_rows.append(row)
        
        # Insert before the stretch item (last item)
        count = self.filters_layout.count()
        if count > 0:
            self.filters_layout.insertWidget(count - 1, row)
        else:
            self.filters_layout.addWidget(row)
        
        # Trigger resize of the overlay container to fit new content
        if hasattr(self, 'top_right_container'):
            self.top_right_container.adjustSize()
            self.filter_container.adjustSize()
            QTimer.singleShot(10, lambda: [self.top_right_container.adjustSize(), self.resizeEvent(None)])
            
    def remove_filter_row(self, row_widget):
        if row_widget in self.filter_rows:
            self.filter_rows.remove(row_widget)
            self.filters_layout.removeWidget(row_widget)
            row_widget.deleteLater()
            
            # Update logic combos visibility
            for i, r in enumerate(self.filter_rows):
                if i == 0:
                    r.logic_combo.hide()
                else:
                    r.logic_combo.show()
            
            # Trigger resize of the overlay container
            if hasattr(self, 'top_right_container'):
                QTimer.singleShot(10, lambda: [self.top_right_container.adjustSize(), self.resizeEvent(None)])

    def apply_theme(self):
        style = config.DARK_STYLE # Force Dark Mode
        self.setStyleSheet(style)
        
        if not hasattr(self, 'filter_container'):
            return

        # Always Dark Mode colors
        bg_color = "rgba(30, 30, 30, 0.95)"
        border_color = "#555"
        text_color = "#ddd"
        input_bg = "rgba(40, 40, 40, 0.9)"
        input_text = "white"
        input_border = "#666"
        toggle_color = "#aaa"

        sheet = f"""
            QFrame#filter_container {{
                background-color: {bg_color}; 
                border-radius: 10px;
                border: 1px solid {border_color};
            }}
            /* Make layouts and containers transparent */
            QFrame#filter_container QWidget {{
                background-color: transparent;
            }}
            QFrame#filter_container QLabel {{
                color: {text_color};
                background-color: transparent;
            }}
            /* Inputs need background */
            QFrame#filter_container QComboBox, 
            QFrame#filter_container QLineEdit, 
            QFrame#filter_container QDoubleSpinBox,
            QFrame#filter_container QSpinBox {{
                background-color: {input_bg};
                color: {input_text};
                border: 1px solid {input_border};
                border-radius: 3px;
            }}
            /* PushButtons (except toggle which is transparent) need background */
            QFrame#filter_container QPushButton {{
               background-color: {input_bg};
               border: 1px solid {input_border};
               border-radius: 4px;
               padding: 4px 8px;
               color: {input_text};
            }}
            QFrame#filter_container QPushButton:hover {{
               background-color: {border_color}; 
            }}
        """
        self.filter_container.setStyleSheet(sheet)
        
        # Style the "Show Filters" button
        if hasattr(self, 'show_filters_btn'):
            self.show_filters_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {bg_color};
                    border: 1px solid {border_color};
                    border-radius: 8px;
                    padding: 8px 16px;
                    color: {text_color};
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: {input_bg};
                }}
            """)
        
        if hasattr(self, 'toggle_btn'):
             self.toggle_btn.setStyleSheet(f"border: none; background: transparent; color: {toggle_color}; font-weight: bold;")

    def change_map_type(self, text):
        self.map_type = text
        self.render_map(self.current_filtered_df)

    def get_current_filters(self):
        active_filters = []
        for row in self.filter_rows:
            data = row.get_filter_data()
            if data:
                active_filters.append(data)
        return active_filters
    
    def on_apply_filters(self):
        current_filters = self.get_current_filters()
        # We allow empty filters (clears filter)
        
        print(f"Applying filters: {current_filters}", flush=True)
        
        # Show progress
        self.progress_bar.setVisible(True)
        self.apply_btn.setEnabled(False)
        self.apply_btn.setText("Rendering...")
        QApplication.processEvents() # Force UI update
        
        # Use QTimer to allow UI to render first frame of progress before blocking work starts
        QTimer.singleShot(100, lambda: self.process_map_update(current_filters))

    def process_map_update(self, current_filters):
        try:
            self.update_map(current_filters)
        finally:
            self.progress_bar.setVisible(False)
            self.apply_btn.setEnabled(True)
            self.apply_btn.setText("Apply Filters & Show Map")

    def reset_filters(self):
        # Clear rows
        self.create_filters() # Re-creates initial row
        
        # Reset to full dataset
        self.current_filtered_df = self.df
        self.render_map(self.df)

    def on_js_console(self, message):
        if not message: return
        # print(f"JS: {message}", flush=True)
        
        if message.startswith("REQUEST_ROUTES|"):
            try:
                code = message.split("|")[1]
                # Filter current DF for matches
                df = getattr(self, 'current_filtered_df', pd.DataFrame())
                if df.empty: 
                    self.map_view.page().runJavaScript(f"loadRoutes([])")
                    return

                # Find matches (Dep OR Arr)
                matches = df[ (df['dep_airport_iata'] == code) | (df['arr_airport_iata'] == code) ]
                
                # Convert to JSON format expected by JS
                routes = []
                for _, row in matches.iterrows():
                    routes.append({
                        "dep": str(row.get('dep_airport_iata', '')),
                        "arr": str(row.get('arr_airport_iata', '')),
                        "flight": str(row.get('flight_number', '')),
                        "type": str(row.get('type', '')),
                        # Additional details
                        "callsign": str(row.get('calsign', '')),
                        "type_icao": str(row.get('type_icao', '')),
                        "reg": str(row.get('reg', '')),
                        "dep_icao": str(row.get('dep_airport_icao', '')),
                        "arr_icao": str(row.get('arr_airport_icao', '')),
                        "airline": str(row.get('owner', '')),
                        "date": str(row.get('timestamp_read', ''))[:10]
                    })
                
                import json
                routes_json = json.dumps(routes)
                
                # Call JS
                self.map_view.page().runJavaScript(f"loadRoutes({routes_json})")
                
            except Exception as e:
                print(f"Error handling route request: {e}", flush=True)

    def render_map(self, filtered_df):
        """Render the map from the given dataframe."""
        self.current_filtered_df = filtered_df
        try:
            html = mapping.create_map_html(filtered_df, self.map_type, self.current_theme, self.airport_counts)
            self.map_view.setHtml(html)
        except Exception as e:
            print(f"Error generating map: {e}", flush=True)

    def update_map(self, filters=None):
        """Apply filters (if any) and re-render."""
        filtered_df = self.df

        if filters:
            try:
                filtered_df = filtering.apply_filters(self.df, filters)
                print(f"Filtered result: {len(filtered_df)} rows", flush=True)
            except Exception as e:
                print(f"Error filtering: {e}", flush=True)
                return

        if filtered_df.empty and filters:
            QMessageBox.information(self, "No Results", "No flights matched your filter criteria.")

        self.render_map(filtered_df)

    def process_map_update(self, current_filters):
        try:
            self.update_map(current_filters)
        finally:
            self.progress_bar.setVisible(False)
            self.apply_btn.setEnabled(True)
            self.apply_btn.setText("Apply Filters & Show Map")

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
