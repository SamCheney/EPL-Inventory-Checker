import requests

from dataclasses import dataclass

from app.constants import APP_VERSION

import tempfile
from pathlib import Path

@dataclass
class UpdateInfo:
    latest_version: str
    release_url: str
    download_url: str

class UpdateChecker:
    LATEST_RELEASE_URL = (
        "https://api.github.com/repos/"
        "SamCheney/EPL-Inventory-Checker/releases/latest"
    )

    def download_installer(self, download_url: str, version: str,) -> Path:
        response = requests.get(download_url, stream=True, timeout=60)
        response.raise_for_status()

        download_folder = Path(tempfile.gettempdir()) / "EPL Inventory Checker"
        download_folder.mkdir(parents=True, exist_ok=True)

        clean_version = version.lstrip("v")
        installer_path = (
            download_folder
            / f"EPL-Inventory-Checker-Setup-v{clean_version}.exe"
        )

        with installer_path.open("wb") as installer_file:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    installer_file.write(chunk)

        return installer_path
    
    def __init__(self):
        self.current_version = APP_VERSION

    def check(self):
        try:
            response = requests.get(self.LATEST_RELEASE_URL, timeout=10)
            response.raise_for_status()

            data = response.json()

            latest_version = data["tag_name"].lstrip("v")
            download_url = ""

            for asset in data["assets"]:
                if asset["name"].endswith(".exe"):
                    download_url = asset["browser_download_url"]
                    break

            print(f"Current: {self.current_version}")
            print(f"Latest : {latest_version}")

            current_parts = tuple(
                int(part)
                for part in self.current_version.lstrip("v").split(".")
            )

            latest_parts = tuple(
                int(part)
                for part in latest_version.lstrip("v").split(".")
            )

            if latest_parts <= current_parts:
                return None

            return UpdateInfo(
                latest_version=latest_version,
                release_url=data["html_url"],
                download_url=download_url,
            ) 
        except requests.RequestException as e:
            print(f"Update check failed: {e}")
            return None   

        
        

        