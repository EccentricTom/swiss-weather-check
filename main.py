import csv
import io
import json
import sys
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO, StringIO

from openpyxl import load_workbook
from PySide6.QtCore import QModelIndex, QPersistentModelIndex, Qt, QUrl
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QCompleter,
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

PICTOGRAMS_URL = (
    "https://www.meteoswiss.admin.ch/dam/"
    "jcr:bfcea855-ab6b-4602-9d8b-0464afa30e66/"
    "2022-02-14-Wetter-Icons-inkl-beschreibung-v1-an-website.xlsx"
)


def normalize_place_name(name: str) -> str:
    name = (
        name.replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("Ä", "Ae")
        .replace("Ö", "Oe")
        .replace("Ü", "Ue")
    )

    normalized = unicodedata.normalize("NFKD", name)

    return "".join(
        character for character in normalized if not unicodedata.combining(character)
    ).casefold()


def place_name_aliases(name: str) -> set[str]:
    stripped = unicodedata.normalize("NFKD", name)
    stripped = "".join(
        character for character in stripped if not unicodedata.combining(character)
    ).casefold()

    transliterated = (
        name.replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("Ä", "Ae")
        .replace("Ö", "Oe")
        .replace("Ü", "Ue")
        .casefold()
    )

    return {stripped, transliterated}


class NoramlizedCompleter(QCompleter):
    def splitPath(self, path: str) -> list[str]:
        return [normalize_place_name(path)]

    def pathFromIndex(self, index: QModelIndex | QPersistentModelIndex) -> str:
        return index.data(Qt.ItemDataRole.DisplayRole)


@dataclass
class WeatherSearchState:
    request_id: int
    region: str
    point_id: str | None = None
    point_type_id: str | None = None
    temperature_data: str | None = None
    weather_type_data: str | None = None
    temperature: str | None = None
    weather_code: str | None = None
    weather_description: str | None = None
    weather_emoji: str | None = None


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
        self.weather_descriptions: dict[int, str] = {}
        self.point_metadata: list[dict[str, str]] | None = None
        # Widgets
        self.region_label = QLabel("Enter region name: ")
        self.region_input = QLineEdit()
        self.get_weather_button = QPushButton("Get Weather")
        self.temperature_label = QLabel("--°C")
        self.temp_emoji_label = QLabel("")
        self.temp_description_label = QLabel("")
        # Network manager to handle network requests asynchronously
        self.network_manager = QNetworkAccessManager(self)

        self.init_ui()

        self.fetch_point_metadata()

        self.fetch_url(
            PICTOGRAMS_URL,
            self.handle_pictogram_data,
            request_id=self.request_id,
        )

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
        self.region_input.returnPressed.connect(self.get_weather)

    def fetch_point_metadata(self) -> None:
        request = QNetworkRequest(QUrl(POINTS_URL))
        reply = self.network_manager.get(request)

        reply.finished.connect(
            lambda: self.handle_startup_point_metadata(reply),
        )

    def handle_startup_point_metadata(
        self,
        reply: QNetworkReply,
    ) -> None:
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                print("Could not load location metadata: ", reply.errorString())
                return
            data = bytes(reply.readAll())

            reader = csv.DictReader(
                StringIO(data.decode("latin-1")),
                delimiter=";",
            )

            self.point_metadata = list(reader)

            place_names = sorted(
                {row["point_name"] for row in self.point_metadata if row["point_name"]}
            )

            self.completer_model = QStandardItemModel(self)

            for place_name in place_names:
                item = QStandardItem(place_name)

                aliases = place_name_aliases(place_name)

                item.setData(
                    " ".join(sorted(aliases)),
                    Qt.ItemDataRole.UserRole,
                )

                self.completer_model.appendRow(item)

            self.completer = NoramlizedCompleter(self.completer_model, self)
            self.completer.setCompletionRole(Qt.ItemDataRole.UserRole)
            self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            self.completer.setFilterMode(Qt.MatchFlag.MatchContains)

            self.region_input.setCompleter(self.completer)

        finally:
            reply.deleteLater()

    def is_active_request(self, request_id: int) -> bool:
        return (
            self.search_state is not None and request_id == self.search_state.request_id
        )

    def get_weather(self):
        self.request_id += 1

        region = self.region_input.text().strip()

        if not region:
            self.display_error("Please input a region")
            return

        if self.point_metadata is None:
            self.display_error("Location data is still loading.")
            return

        self.temperature_label.setText("--°C")
        self.temp_emoji_label.setText("")
        self.temp_description_label.setText("")

        self.get_weather_button.setEnabled(False)
        self.get_weather_button.setText("Loading...")

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
            request_id,
        )

        self.find_region(request_id)

    def fetch_url(
        self,
        url: str,
        callback: Callable[[bytes], None],
        request_id: int,
    ) -> None:
        request = QNetworkRequest(QUrl(url))
        reply = self.network_manager.get(request)

        reply.finished.connect(lambda: self.handle_reply(reply, callback, request_id))

    def handle_reply(
        self,
        reply: QNetworkReply,
        callback: Callable[[bytes], None],
        request_id: int,
    ) -> None:
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.display_error(reply.errorString(), request_id)
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
            self.display_error("No temperature data available.", request_id)
            return

        if weather_type_url is None:
            self.display_error("No weather type data available.", request_id)
            return

        self.fetch_url(
            temperature_url,
            lambda data: self.handle_temperature_data(
                data,
                request_id,
            ),
            request_id,
        )

        self.fetch_url(
            weather_type_url,
            lambda data: self.handle_weather_type_data(
                data,
                request_id,
            ),
            request_id,
        )

    @staticmethod
    def get_weather_emoji(description: str) -> str:
        description = description.casefold()

        if "thunder" in description:
            return "⛈️"

        if "snow" in description:
            return "🌨️"

        if "rain" in description or "shower" in description:
            return "🌧️"

        if "fog" in description or "mist" in description:
            return "🌫️"

        if "cloud" in description:
            return "⛅"

        if "sunny" in description or "sun" in description:
            return "☀️"

        return "🌤️"

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

    def find_region(
        self,
        request_id: int,
    ) -> None:
        if not self.is_active_request(request_id):
            return

        if self.search_state is None:
            return

        if self.point_metadata is None:
            return

        target_aliases = place_name_aliases(self.search_state.region)

        for row in self.point_metadata:
            point_name = row["point_name"]
            if target_aliases & place_name_aliases(point_name):
                self.search_state.point_id = row["point_id"]
                self.search_state.point_type_id = row["point_type_id"]
                self.try_update_weather()
                return

        self.display_error(
            f"Region not found: {self.search_state.region}",
            request_id,
        )

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

    def handle_pictogram_data(self, data: bytes) -> None:
        workbook = load_workbook(
            BytesIO(data),
            read_only=True,
            data_only=True,
        )

        worksheet = workbook["aktuellste Version"]

        for row in worksheet.iter_rows(values_only=True):
            code = row[1]
            description_en = row[5]

            if isinstance(code, int) and description_en:
                self.weather_descriptions[code] = str(description_en)

        workbook.close()

    def try_update_weather(self) -> None:
        self.try_update_temperature()
        self.try_update_weather_type()
        self.display_weather()

    def try_update_weather_type(self) -> None:
        if self.search_state.point_id is None:
            return
        if self.search_state.weather_type_data is None:
            return

        current_hour = datetime.now(UTC).strftime("%Y%m%d%H00")

        reader = csv.DictReader(
            StringIO(self.search_state.weather_type_data),
            delimiter=";",
        )

        for row in reader:
            if (
                row["point_id"] == self.search_state.point_id
                and row["point_type_id"] == self.search_state.point_type_id
                and row["Date"] == current_hour
            ):
                weather_code = int(row["jww003i0"])
                self.search_state.weather_code = str(weather_code)
                self.search_state.weather_description = self.weather_descriptions.get(
                    weather_code,
                    f"Unknown weather code ({weather_code})",
                )
                self.search_state.weather_emoji = self.get_weather_emoji(
                    self.search_state.weather_description
                )
                return

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
            if (
                row["point_id"] == self.search_state.point_id
                and row["point_type_id"] == self.search_state.point_type_id
            ):
                self.search_state.temperature = row["tre200h0"]
                return

    def display_error(self, message: str, request_id: int | None = None) -> None:
        if request_id is not None:
            if not self.is_active_request(request_id):
                return

        self.temperature_label.setText("--°C")
        self.temp_emoji_label.setText("")
        self.temp_description_label.setText(message)

        self.get_weather_button.setEnabled(True)
        self.get_weather_button.setText("Get Weather")

    def display_weather(self):
        if self.search_state is None:
            return

        if self.search_state.temperature is None:
            return

        if self.search_state.weather_description is None:
            return

        if self.search_state.weather_emoji is None:
            return

        self.temperature_label.setText(f"{self.search_state.temperature}°C")

        self.temp_description_label.setText(self.search_state.weather_description)

        self.temp_emoji_label.setText(self.search_state.weather_emoji)

        self.get_weather_button.setEnabled(True)
        self.get_weather_button.setText("Get Weather")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    weather_app = WeatherApp()
    weather_app.show()
    sys.exit(app.exec())
