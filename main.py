import csv
import io
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from PySide6.QtCore import Qt, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

POINTS_URL = (
    "https://data.geo.admin.ch/"
    "ch.meteoschweiz.ogd-local-forecasting/"
    "ogd-local-forecasting_meta_point.csv"
)

ITEMS_URL = (
    "https://data.geo.admin.ch/api/stac/v1/"
    "collections/ch.meteoschweiz.ogd-local-forecasting/items"
)


@dataclass
class WeatherSearchState:
    request_id: int
    region: str
    point_id: str | None = None
    temperature_data: str | None = None
    weather_type_data: str | None = None
    weather_code: str | None = None


class WeatherApp(QWidget):
    """_summary_

    Args:
        QWidget (_type_): _description_
    """

    def __init__(self):
        super().__init__()
        # Empty data points, will be replaced by manager
        self.request_id: int = 0
        self.search_state: WeatherSearchState | None = None
        # Widgets
        self.region_label = QLabel("Enter region name: ")
        self.region_input = QLineEdit()
        self.get_weather_button = QPushButton("Get Weather")
        self.temperature_label = QLabel("18ºC")
        self.temp_emoji_label = QLabel("☀️")
        self.temp_description_label = QLabel("sunny")
        # Network manager to handle network requests asynchronously
        self.network_manager = QNetworkAccessManager(self)

        self.init_ui()

    def init_ui(self):
        """Initialise the UI"""
        self.setWindowTitle("Weather App")

        vbox = QVBoxLayout()

        vbox.addWidget(self.region_label)
        vbox.addWidget(self.region_input)
        vbox.addWidget(self.get_weather_button)
        vbox.addWidget(self.temperature_label)
        vbox.addWidget(self.temp_emoji_label)
        vbox.addWidget(self.temp_description_label)

        self.setLayout(vbox)

        self.region_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.region_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.temperature_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.temp_emoji_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.temp_description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.region_label.setObjectName("region_label")
        self.region_input.setObjectName("region_input")
        self.get_weather_button.setObjectName("get_weather_button")
        self.temperature_label.setObjectName("temperature_label")
        self.temp_emoji_label.setObjectName("temp_emoji_label")
        self.temp_description_label.setObjectName("temp_description_label")

        self.setStyleSheet("""
                QLabel, QPushButton {
                    font-family: times new roman;
                }
                QLabel#region_label {
                    font-size: 40px;
                    font-style: italic;

                }
                QLineEdit#region_input {
                    font-size: 40px;
                    padding: 8px 12px;
                }
                QPushButton#get_weather_button {
                    font-size: 30px;
                    font-weight: bold;
                }
                QLabel#temperature_label{
                font-size: 75px;
                }
                QLabel#temp_emoji_label{
                    font-size: 100px;
                    font-family: Segoe UI emoji;
                }
                QLabel#temp_description_label{
                    font-size: 50px;
                }
        """)

        self.get_weather_button.clicked.connect(self.get_weather)

    def is_active_request(self, request_id: int) -> bool:
        return (
            self.search_state is not None and request_id == self.search_state.request_id
        )

    def get_weather(self):
        self.request_id += 1

        region = self.region_input.text().strip()

        self.search_state = WeatherSearchState(
            request_id=self.request_id,
            region=region,
        )
        request_id = self.search_state.request_id

        self.fetch_url(
            ITEMS_URL,
            lambda data: self.handle_items_data(
                data,
                request_id,
            ),
        )

        self.fetch_url(
            POINTS_URL,
            lambda data: self.handle_point_metadata(
                data,
                request_id,
            ),
        )

    def fetch_url(
        self,
        url: str,
        callback: Callable[[bytes], None],
    ) -> None:
        request = QNetworkRequest(QUrl(url))
        reply = self.network_manager.get(request)

        reply.finished.connect(lambda: self.handle_reply(reply, callback))

    def handle_reply(
        self,
        reply: QNetworkReply,
        callback: Callable[[bytes], None],
    ) -> None:
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.display_error(reply.errorString())
                return

            data = bytes(reply.readAll())

            callback(data)

        finally:
            reply.deleteLater()

    def handle_items_data(
        self,
        data: bytes,
        request_id: int,
    ) -> None:
        if not self.is_active_request(request_id):
            return
        response = json.loads(data)
        features = response["features"]

        temperature_url = self.find_latest_asset_url(
            features,
            "tre200h0",
        )

        weather_type_url = self.find_latest_asset_url(
            features,
            "jww003i0",
        )

        if temperature_url is None:
            self.display_error("No temperature data available.")
            return

        if weather_type_url is None:
            self.display_error("No weather type data available.")
            return

        self.fetch_url(
            temperature_url,
            lambda data: self.handle_temperature_data(
                data,
                request_id,
            ),
        )

        self.fetch_url(
            weather_type_url,
            lambda data: self.handle_weather_type_data(
                data,
                request_id,
            ),
        )

    @staticmethod
    def find_latest_asset_url(
        features: list[dict],
        parameter: str,
    ) -> str | None:
        matching_assets = []

        for feature in features:
            for filename, asset in feature["assets"].items():
                if parameter in filename:
                    matching_assets.append((filename, asset["href"]))

        if not matching_assets:
            return None

        _, url = max(
            matching_assets,
            key=lambda item: item[0],
        )

        return url

    def handle_point_metadata(
        self,
        data: bytes,
        request_id: int,
    ) -> None:
        if not self.is_active_request(request_id):
            return

        region = self.search_state.region

        text = data.decode("latin-1")

        reader = csv.DictReader(io.StringIO(text), delimiter=";")

        target = region.casefold()

        for row in reader:
            if row["point_name"].casefold() == target:
                self.search_state.point_id = row["point_id"]
                self.try_update_weather()
                return

        self.display_error(f"Region not found: {region}")

    def handle_temperature_data(
        self,
        data: bytes,
        request_id: int,
    ) -> None:
        if not self.is_active_request(request_id):
            return
        self.search_state.temperature_data = data.decode("latin-1")
        self.try_update_weather()

    def handle_weather_type_data(
        self,
        data: bytes,
        request_id: int,
    ) -> None:
        if not self.is_active_request(request_id):
            return
        self.search_state.weather_type_data = data.decode("latin-1")
        self.try_update_weather()

    def try_update_weather(self) -> None:
        self.try_update_temperature()
        self.try_update_weather_type()

    def try_update_weather_type(self) -> None:
        if self.search_state.point_id is None:
            return
        if self.search_state.weather_type_data is None:
            return

        current_hour = datetime.now(UTC).strftime("%Y%m%d%H00")

        reader = csv.DictReader(
            io.StringIO(self.search_state.weather_type_data),
            delimiter=";",
        )

        for row in reader:
            if (
                row["point_id"] == self.search_state.point_id
                and row["Date"] == current_hour
            ):
                self.weather_code = row["jww003i0"]

                print("Current weather code:", self.weather_code)
                break

    def try_update_temperature(self) -> None:
        if self.search_state.point_id is None:
            return
        if self.search_state.temperature_data is None:
            return

        reader = csv.DictReader(
            io.StringIO(self.search_state.temperature_data),
            delimiter=";",
        )

        for row in reader:
            if row["point_id"] == self.search_state.point_id:
                temperature = row["tre200h0"]

                self.temperature_label.setText(f"{temperature}°C")
                break

    def display_error(
        self,
        message,
    ) -> None:
        print(message)

    def display_weather(self, data):
        pass


if __name__ == "__main__":
    app = QApplication(sys.argv)
    weather_app = WeatherApp()
    weather_app.show()
    sys.exit(app.exec())
